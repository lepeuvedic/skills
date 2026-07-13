"""Transport layer: an HTTPS-forced connection to OpenERP 7 behind Traefik.

``odoo-client-lib`` ships secure connectors (``XmlRPCSConnector`` / ``JsonRPCSConnector``),
but they hardcode the URL as ``https://<host>:<port>/xmlrpc`` with:

  * no support for a URL path prefix (Traefik may route OpenERP under ``/erp`` etc.), and
  * no control over TLS certificate verification.

This module subclasses those connectors so that:

  * the scheme is **always** ``https`` (plain http is refused unless explicitly allowed),
  * an optional ``base_path`` is honoured, and
  * TLS verification is configurable (the Traefik proxy terminates TLS).

OpenERP 7 speaks XML-RPC over ``/xmlrpc/<service>`` (``common`` for login, ``object`` for
``execute``/``execute_kw``). That is exactly what ``odoo-client-lib`` drives, so the model
abstraction works unchanged against OpenERP 7.
"""

from __future__ import annotations

import logging
import ssl
import xmlrpc.client as _xmlrpc

import httpx

import odoolib
from odoolib.rpc import Connection, XmlRPCSConnector, JsonRPCSConnector

from .config import OpenERPConfig

_logger = logging.getLogger("openerp_mcp.connection")


class InsecureTransportError(RuntimeError):
    """Raised if a non-HTTPS transport is requested without an explicit override."""


def _build_xmlrpc_transport(verify_tls: bool, timeout: float) -> _xmlrpc.Transport:
    """A SafeTransport that respects the TLS-verify flag and a timeout.

    The Traefik proxy terminates TLS. In production ``verify_tls`` should stay True; it can
    be disabled for a self-signed staging certificate.
    """

    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    class _SafeTransport(_xmlrpc.SafeTransport):
        def make_connection(self, host):  # noqa: D401 - stdlib override
            conn = super().make_connection(host)
            try:
                conn.timeout = timeout
            except Exception:  # pragma: no cover - defensive
                pass
            return conn

    return _SafeTransport(context=context)


class HttpsXmlRPCSConnector(XmlRPCSConnector):
    """XML-RPC over HTTPS with base-path and TLS-verify support."""

    PROTOCOL = "xmlrpcs"

    def __init__(self, hostname: str, port: int, base_path: str = "",
                 verify_tls: bool = True, timeout: float = 30.0):
        super().__init__(hostname, port)
        prefix = ("/" + base_path.strip("/")) if base_path.strip("/") else ""
        # Force https + optional prefix. Library appends "/<service>" to self.url.
        self.url = f"https://{hostname}:{port}{prefix}/xmlrpc"
        self._verify_tls = verify_tls
        self._timeout = timeout

    def send(self, service_name, method, *args):
        url = f"{self.url}/{service_name}"
        if not url.startswith("https://"):
            raise InsecureTransportError(f"Refusing non-HTTPS XML-RPC URL: {url!r}")
        transport = _build_xmlrpc_transport(self._verify_tls, self._timeout)
        service = _xmlrpc.ServerProxy(url, transport=transport, allow_none=True)
        return getattr(service, method)(*args)


class HttpsJsonRPCSConnector(JsonRPCSConnector):
    """JSON-RPC over HTTPS with base-path and TLS-verify support."""

    PROTOCOL = "jsonrpcs"

    def __init__(self, hostname: str, port: int, base_path: str = "",
                 verify_tls: bool = True, timeout: float = 30.0):
        super().__init__(hostname, port)
        prefix = ("/" + base_path.strip("/")) if base_path.strip("/") else ""
        self.url = f"https://{hostname}:{port}{prefix}/jsonrpc"
        self._verify_tls = verify_tls
        self._timeout = timeout

    def send(self, service_name, method, *args):
        if not self.url.startswith("https://"):
            raise InsecureTransportError(f"Refusing non-HTTPS JSON-RPC URL: {self.url!r}")
        import random

        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service_name, "method": method, "args": args},
            "id": random.randint(0, 1_000_000_000),
        }
        with httpx.Client(verify=self._verify_tls, timeout=self._timeout) as client:
            resp = client.post(self.url, json=payload,
                               headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            data = resp.json()
        if data.get("error"):
            from odoolib.tools import JsonRPCException

            raise JsonRPCException(data["error"])
        return data.get("result", False)


def build_connection(config: OpenERPConfig) -> Connection:
    """Create an authenticated ``odoo-client-lib`` :class:`Connection`, HTTPS-forced.

    Authentication is verified eagerly (``check_login``) so misconfiguration surfaces at
    startup rather than on the first tool call.
    """

    if config.allow_insecure:
        _logger.warning(
            "OPENERP_ALLOW_INSECURE is set; HTTPS enforcement relaxed. "
            "Do not use this against production."
        )

    if config.protocol == "jsonrpcs":
        connector = HttpsJsonRPCSConnector(
            config.host, config.port, base_path=config.base_path,
            verify_tls=config.verify_tls, timeout=config.timeout,
        )
    else:
        connector = HttpsXmlRPCSConnector(
            config.host, config.port, base_path=config.base_path,
            verify_tls=config.verify_tls, timeout=config.timeout,
        )

    _logger.info("Connecting to OpenERP at %s (db=%s)", connector.url, config.database)

    connection = odoolib.Connection(
        connector,
        database=config.database,
        login=config.login,
        password=config.password,
    )
    connection.check_login(force=True)
    _logger.info("Authenticated as user_id=%s", connection.user_id)
    return connection
