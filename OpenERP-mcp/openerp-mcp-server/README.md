# OpenERP MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes an
**OpenERP 7 / Odoo** instance through the [`odoo-client-lib`](https://github.com/odoo/odoo-client-lib)
model abstraction. The connection is **HTTPS-forced** because the OpenERP server runs behind a
Traefik proxy that terminates TLS.

It keeps the simple, model-based interface (`search_read`, `read`, `create`, `write`,
`unlink`, `call`) but routes every operation through a **neutral business layer** that hosts
validation hooks, a locked-accounts/journals registry, and a multi-operation transaction
context — a single choke point for future accounting guarantees.

## Layout

```
openerp-mcp-server/
├── openerp_mcp/
│   ├── config.py        # env-based configuration (OPENERP_*)
│   ├── connection.py    # HTTPS-forced connectors (XML-RPC / JSON-RPC) over Traefik
│   ├── business.py      # NEUTRAL business layer: hooks, account/journal lock, transactions
│   └── server.py        # FastMCP stdio server + 8 tools
├── tests/               # offline tests (no live server needed)
├── vendor/              # patched odoo-client-lib wheel (py3-none-any, pinned to 3.14)
├── bootstrap.ps1 / .sh  # create the Python 3.14 venv and install everything
├── .env.example
└── mcp-client-config.example.json
```

## Requirements

- **Python 3.14** (matches the local CPython 3.14.3). The bootstrap script provisions it via
  [`uv`](https://docs.astral.sh/uv/) (recommended) or the `py -3.14` launcher.
- Network access to the OpenERP server **over HTTPS** (Traefik).

## Install (Windows)

```powershell
cd C:\Users\lepeu\Documents\Claude\OpenERP-mcp\openerp-mcp-server
.\bootstrap.ps1
copy .env.example .env   # then edit .env with your OpenERP credentials
```

`bootstrap.ps1` creates `.\.venv` on Python 3.14, installs the vendored patched
`odoo-client-lib` wheel, then installs this project.

> Note: the vendored wheel is **pure Python** (`py3-none-any`), so it installs on any
> CPython ≥ 3.14 regardless of where it was built.

## Configure your MCP client

Merge the `openerp` block from `mcp-client-config.example.json` into your client's
`mcpServers`, adjusting the absolute paths and the `env` credentials. The server launches as:

```
.\.venv\Scripts\python.exe -m openerp_mcp.server
```

Configuration is read from environment variables (or a `.env` file):

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `OPENERP_HOST` | ✅ | — | Hostname exposed by Traefik |
| `OPENERP_DB` | ✅ | — | Database name |
| `OPENERP_LOGIN` | ✅ | — | User login |
| `OPENERP_PASSWORD` | ✅ | — | Password / API key |
| `OPENERP_PORT` | | `443` | TLS port on Traefik |
| `OPENERP_PROTOCOL` | | `xmlrpcs` | `xmlrpcs` (OpenERP 7) or `jsonrpcs` — both HTTPS-only |
| `OPENERP_BASE_PATH` | | `` | URL prefix if OpenERP is routed under a sub-path |
| `OPENERP_TIMEOUT` | | `30` | Request timeout (s) |
| `OPENERP_VERIFY_TLS` | | `true` | Verify the proxy certificate |
| `OPENERP_ALLOW_INSECURE` | | `false` | Emergency override to allow plain HTTP |
| `OPENERP_LOCKED_ACCOUNTS` | | `` | Comma-separated `account.account` codes to lock |
| `OPENERP_LOCKED_JOURNALS` | | `` | Comma-separated `account.journal` codes to lock |

## Tools

| Tool | Read-only | Purpose |
|---|---|---|
| `openerp_search_read` | ✅ | Search + read records in one call |
| `openerp_read` | ✅ | Read records by id |
| `openerp_create` | | Create a record (guarded) |
| `openerp_write` | | Update records (guarded) |
| `openerp_unlink` | | Delete records (guarded) |
| `openerp_call` | | Call an arbitrary model method (guarded) |
| `openerp_fields_get` | ✅ | Describe a model's fields |
| `openerp_whoami` | ✅ | Show authenticated user + active locking policy |

All tools return a JSON envelope: `{"ok": true, "result": ...}` or
`{"ok": false, "error": {"kind", "message"}}`. A blocked operation returns
`kind = "policy_violation"`.

## The business layer (neutral, extensible)

`openerp_mcp/business.py` is the **only** path from tools to models. Today it allows
everything; it exists so accounting rules live in one place that direct model access cannot
sidestep.

**Validation hooks** — subclass `PolicyHook` and `layer.add_hook(...)`. `before()` may raise
`PolicyViolation` to veto an operation; `after()` is for audit/side-effects.

```python
class FreezePeriod(PolicyHook):
    name = "freeze-2025"
    def before(self, op, ctx):
        if op.is_write() and op.model.startswith("account.") and \
           (op.values or {}).get("date", "") < "2026-01-01":
            raise PolicyViolation("Period 2025 is frozen.")
layer.add_hook(FreezePeriod())
```

**Account/journal locking** — set `OPENERP_LOCKED_ACCOUNTS` / `OPENERP_LOCKED_JOURNALS`. The
built-in `AccountGuard` then refuses: edits to those `account.account` / `account.journal`
records, and create/write/unlink on `account.move` / `account.move.line` that reference a
locked journal (`journal_id`) or account (`account_id`). Reads are never blocked. Empty
lists ⇒ no-op (neutral). Extend `AccountGuard._violates()` as your rules firm up.

**Transactions** — `layer.transaction(label)` groups steps: all pre-hooks run first, steps
execute in order, and on failure any completed step with a `compensate` callback is undone in
reverse (best effort). OpenERP's XML-RPC has no cross-call DB transaction, so this is
application-level sequencing + compensation, **not** an ACID rollback.

```python
txn = layer.transaction("invoice+payment")
txn.add(TransactionStep("invoice", run=make_invoice, compensate=cancel_invoice))
txn.add(TransactionStep("payment", run=register_payment))
txn.execute()
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -v
```

13 offline tests cover the neutral layer, custom hooks, account/journal locking, transaction
commit + compensation, and HTTPS enforcement (no live OpenERP server required).

## OpenERP 7 compatibility

OpenERP 7 speaks XML-RPC at `/xmlrpc/<service>` (`common` for `login`, `object` for
`execute`/`execute_kw`) — exactly what `odoo-client-lib` drives, so the model abstraction
works against OpenERP 7. The vendored `odoo-client-lib` was patched only to remove a dead
Python-2 import branch and to require Python ≥ 3.14 (see `../odoo-client-lib/.upstream-commit`
for the upstream commit it was built from).
```
