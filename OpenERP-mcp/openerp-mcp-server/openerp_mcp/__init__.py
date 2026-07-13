"""OpenERP MCP server package.

A Model Context Protocol server exposing an OpenERP 7 / Odoo instance through the
`odoo-client-lib` model abstraction.

Layers
------
- :mod:`openerp_mcp.connection` -- transport. Wraps ``odoo-client-lib`` and forces an
  HTTPS connection (the OpenERP 7 server sits behind a Traefik proxy that terminates TLS).
- :mod:`openerp_mcp.business` -- neutral business layer. Validation hooks, a registry of
  locked accounts/journals, and a multi-operation transaction context. This is the choke
  point: MCP tools never touch the raw model directly, so accounting guarantees cannot be
  trivially bypassed.
- :mod:`openerp_mcp.server` -- the FastMCP server and tool definitions.
"""

__version__ = "0.1.0"
