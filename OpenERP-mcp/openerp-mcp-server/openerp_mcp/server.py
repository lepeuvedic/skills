#!/usr/bin/env python3
"""OpenERP MCP server (stdio).

Exposes an OpenERP 7 / Odoo instance through the ``odoo-client-lib`` model abstraction.
Connection is HTTPS-forced (the server sits behind a Traefik proxy that terminates TLS).

Architecture
------------
Tools never touch raw models. Every operation goes through :class:`openerp_mcp.business.
BusinessLayer`, which today is neutral but hosts the validation hooks, the locked
accounts/journals registry, and the multi-operation transaction context. This keeps a single
choke point for future accounting guarantees.

Run::

    python -m openerp_mcp.server          # stdio transport (default)

Configuration comes from environment variables; see :mod:`openerp_mcp.config`.
"""

from __future__ import annotations

import json
import logging
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

from .business import BusinessLayer, PolicyViolation
from .config import ConfigError, OpenERPConfig
from .connection import build_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,  # stdout is reserved for the MCP stdio protocol
)
_logger = logging.getLogger("openerp_mcp.server")

mcp = FastMCP("openerp_mcp")

# Lazily-built singletons so the module imports even without a live server.
_LAYER: Optional[BusinessLayer] = None
_CONFIG: Optional[OpenERPConfig] = None


def get_layer() -> BusinessLayer:
    """Return the business layer, building the HTTPS connection on first use."""
    global _LAYER, _CONFIG
    if _LAYER is None:
        _CONFIG = OpenERPConfig.from_env()
        _logger.info("Config: %s", _CONFIG.redacted())
        connection = build_connection(_CONFIG)
        _LAYER = BusinessLayer(
            connection,
            locked_accounts=_CONFIG.locked_accounts,
            locked_journals=_CONFIG.locked_journals,
        )
    return _LAYER


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "result": data}, indent=2, default=str)


def _err(message: str, kind: str = "error") -> str:
    return json.dumps({"ok": False, "error": {"kind": kind, "message": message}}, indent=2)


def _handle(exc: Exception) -> str:
    if isinstance(exc, PolicyViolation):
        return _err(str(exc), kind="policy_violation")
    if isinstance(exc, ConfigError):
        return _err(str(exc), kind="config_error")
    return _err(f"{type(exc).__name__}: {exc}", kind="server_error")


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #
class SearchReadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., description="OpenERP model name (e.g. 'res.partner', 'account.move').", min_length=1)
    domain: Optional[List[Any]] = Field(default=None, description="Search domain, OpenERP triplet list, e.g. [[\"customer\",\"=\",true]].")
    fields: Optional[List[str]] = Field(default=None, description="Fields to read; empty/None = all.")
    offset: int = Field(default=0, ge=0, description="Pagination offset.")
    limit: Optional[int] = Field(default=80, ge=1, le=1000, description="Max rows (default 80).")
    order: Optional[str] = Field(default=None, description="Sort clause, e.g. 'name asc, id desc'.")


class ReadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., min_length=1, description="OpenERP model name.")
    ids: List[int] = Field(..., min_length=1, description="Record ids to read.")
    fields: Optional[List[str]] = Field(default=None, description="Fields to read; empty/None = all.")


class CreateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., min_length=1, description="OpenERP model name.")
    values: Dict[str, Any] = Field(..., description="Field -> value mapping for the new record.")


class WriteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., min_length=1, description="OpenERP model name.")
    ids: List[int] = Field(..., min_length=1, description="Record ids to update.")
    values: Dict[str, Any] = Field(..., description="Field -> value mapping to write.")


class UnlinkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., min_length=1, description="OpenERP model name.")
    ids: List[int] = Field(..., min_length=1, description="Record ids to delete.")


class CallInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., min_length=1, description="OpenERP model name.")
    method: str = Field(..., min_length=1, description="Model method to call (e.g. 'button_validate', 'name_get').")
    args: List[Any] = Field(default_factory=list, description="Positional arguments for the method.")
    kwargs: Dict[str, Any] = Field(default_factory=dict, description="Keyword arguments for the method.")


class FieldsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    model: str = Field(..., min_length=1, description="OpenERP model name.")
    attributes: Optional[List[str]] = Field(
        default=None,
        description="Field attributes to return (e.g. ['string','type','required']); None = default set.",
    )


# --------------------------------------------------------------------------- #
# Tools -- generic model abstraction (the simple interface)
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="openerp_search_read",
    annotations={"title": "OpenERP search_read", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def openerp_search_read(params: SearchReadInput) -> str:
    """Search and read records of an OpenERP model in one call (the common read path).

    Routes through the business layer. Read operations are never blocked by the guard.

    Args:
        params: model, domain, fields, offset, limit, order.

    Returns:
        JSON: {"ok": true, "result": [ {field: value, ...}, ... ]} on success,
        or {"ok": false, "error": {"kind","message"}} on failure.
    """
    try:
        rows = get_layer().search_read(
            params.model, domain=params.domain, fields=params.fields,
            offset=params.offset, limit=params.limit, order=params.order,
        )
        return _ok(rows)
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


@mcp.tool(
    name="openerp_read",
    annotations={"title": "OpenERP read", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def openerp_read(params: ReadInput) -> str:
    """Read specific records by id from an OpenERP model.

    Returns:
        JSON envelope with the list of record dicts.
    """
    try:
        return _ok(get_layer().read(params.model, params.ids, fields=params.fields))
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


@mcp.tool(
    name="openerp_create",
    annotations={"title": "OpenERP create", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def openerp_create(params: CreateInput) -> str:
    """Create a record. Subject to business-layer validation hooks and the account guard.

    Returns:
        JSON envelope; result is the new record id (int), or a policy_violation error.
    """
    try:
        return _ok(get_layer().create(params.model, params.values))
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


@mcp.tool(
    name="openerp_write",
    annotations={"title": "OpenERP write", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
)
async def openerp_write(params: WriteInput) -> str:
    """Update records. Subject to business-layer validation hooks and the account guard.

    Writes targeting a locked account/journal (when configured) are refused with a
    policy_violation error.

    Returns:
        JSON envelope; result is True on success, or a policy_violation error.
    """
    try:
        return _ok(get_layer().write(params.model, params.ids, params.values))
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


@mcp.tool(
    name="openerp_unlink",
    annotations={"title": "OpenERP unlink (delete)", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def openerp_unlink(params: UnlinkInput) -> str:
    """Delete records. Subject to business-layer validation hooks and the account guard.

    Returns:
        JSON envelope; result is True on success, or a policy_violation error.
    """
    try:
        return _ok(get_layer().unlink(params.model, params.ids))
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


@mcp.tool(
    name="openerp_call",
    annotations={"title": "OpenERP call model method", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def openerp_call(params: CallInput) -> str:
    """Call an arbitrary method on an OpenERP model (escape hatch for workflow buttons etc).

    Routed through the business layer's CALL path so future hooks can inspect method calls
    (e.g. blocking 'button_validate' on a locked journal). Use the dedicated CRUD tools when
    possible; this is for methods they don't cover.

    Returns:
        JSON envelope with the method's return value.
    """
    try:
        return _ok(get_layer().call(params.model, params.method, *params.args, **params.kwargs))
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


# --------------------------------------------------------------------------- #
# Tools -- introspection / metadata
# --------------------------------------------------------------------------- #
@mcp.tool(
    name="openerp_fields_get",
    annotations={"title": "OpenERP fields_get", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def openerp_fields_get(params: FieldsInput) -> str:
    """Describe the fields of an OpenERP model (names, types, labels, requirements).

    Useful before create/write to learn the schema of a model.

    Returns:
        JSON envelope; result maps field name -> attribute dict.
    """
    try:
        attrs = params.attributes or ["string", "type", "required", "readonly", "relation", "selection"]
        return _ok(get_layer().call(params.model, "fields_get", [], attrs))
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


@mcp.tool(
    name="openerp_whoami",
    annotations={"title": "OpenERP connection info", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def openerp_whoami() -> str:
    """Return the authenticated user id and the active locking policy.

    Confirms the HTTPS connection works and shows which accounts/journals are locked and
    which policy hooks are installed.

    Returns:
        JSON envelope: {"user_id": int, "locking": {...}}.
    """
    try:
        layer = get_layer()
        return _ok({"user_id": layer.user_id, "locking": layer.locked_summary()})
    except Exception as exc:  # noqa: BLE001
        return _handle(exc)


def main() -> None:
    """Entry point for ``python -m openerp_mcp.server`` and the console script."""
    mcp.run()


if __name__ == "__main__":
    main()
