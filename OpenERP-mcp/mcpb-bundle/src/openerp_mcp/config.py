"""Configuration for the OpenERP MCP server.

All settings are read from environment variables so the stdio server can be launched by an
MCP client without a separate config file. A ``.env`` file is loaded if present (see
``.env.example``).

Required
--------
OPENERP_HOST      Hostname exposed by the Traefik proxy, e.g. ``erp.example.com``.
OPENERP_DB        Database name.
OPENERP_LOGIN     User login.
OPENERP_PASSWORD  User password (or API key).

Optional
--------
OPENERP_PORT          TLS port exposed by Traefik (default: 443).
OPENERP_PROTOCOL      ``xmlrpcs`` (default) or ``jsonrpcs``. Both are HTTPS-only here.
OPENERP_BASE_PATH     URL path prefix if Traefik routes OpenERP under a sub-path
                      (default: "" -> the library's native ``/xmlrpc`` or ``/jsonrpc``).
OPENERP_TIMEOUT       Request timeout in seconds (default: 30).
OPENERP_VERIFY_TLS    ``1``/``true`` to verify the proxy certificate (default: true).
OPENERP_LOCKED_ACCOUNTS  Comma-separated account codes that are read-only (default: "").
OPENERP_LOCKED_JOURNALS  Comma-separated journal codes that are read-only (default: "").
OPENERP_ALLOW_INSECURE   ``1`` to permit plain http (NOT recommended; default: false).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

try:  # optional dependency, used only for developer convenience
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _split_csv(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _as_bool(raw: str, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when mandatory configuration is missing or inconsistent."""


@dataclass(frozen=True)
class OpenERPConfig:
    """Immutable snapshot of the server configuration."""

    host: str
    database: str
    login: str
    password: str
    port: int = 443
    protocol: str = "xmlrpcs"
    base_path: str = ""
    timeout: float = 30.0
    verify_tls: bool = True
    allow_insecure: bool = False
    locked_accounts: List[str] = field(default_factory=list)
    locked_journals: List[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "OpenERPConfig":
        host = os.environ.get("OPENERP_HOST", "").strip()
        database = os.environ.get("OPENERP_DB", "").strip()
        login = os.environ.get("OPENERP_LOGIN", "").strip()
        password = os.environ.get("OPENERP_PASSWORD", "")

        missing = [
            name
            for name, value in (
                ("OPENERP_HOST", host),
                ("OPENERP_DB", database),
                ("OPENERP_LOGIN", login),
                ("OPENERP_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". See .env.example."
            )

        protocol = os.environ.get("OPENERP_PROTOCOL", "xmlrpcs").strip().lower()
        if protocol not in {"xmlrpcs", "jsonrpcs"}:
            raise ConfigError(
                f"OPENERP_PROTOCOL must be 'xmlrpcs' or 'jsonrpcs' (HTTPS only), got '{protocol}'."
            )

        allow_insecure = _as_bool(os.environ.get("OPENERP_ALLOW_INSECURE", ""), False)

        try:
            port = int(os.environ.get("OPENERP_PORT", "443"))
        except ValueError as exc:
            raise ConfigError("OPENERP_PORT must be an integer.") from exc

        try:
            timeout = float(os.environ.get("OPENERP_TIMEOUT", "30"))
        except ValueError as exc:
            raise ConfigError("OPENERP_TIMEOUT must be a number.") from exc

        return cls(
            host=host,
            database=database,
            login=login,
            password=password,
            port=port,
            protocol=protocol,
            base_path=os.environ.get("OPENERP_BASE_PATH", "").strip(),
            timeout=timeout,
            verify_tls=_as_bool(os.environ.get("OPENERP_VERIFY_TLS", ""), True),
            allow_insecure=allow_insecure,
            locked_accounts=_split_csv(os.environ.get("OPENERP_LOCKED_ACCOUNTS", "")),
            locked_journals=_split_csv(os.environ.get("OPENERP_LOCKED_JOURNALS", "")),
        )

    def redacted(self) -> dict:
        """Config dict safe to log (password masked)."""
        data = {
            "host": self.host,
            "database": self.database,
            "login": self.login,
            "password": "***" if self.password else "",
            "port": self.port,
            "protocol": self.protocol,
            "base_path": self.base_path,
            "timeout": self.timeout,
            "verify_tls": self.verify_tls,
            "allow_insecure": self.allow_insecure,
            "locked_accounts": self.locked_accounts,
            "locked_journals": self.locked_journals,
        }
        return data
