#!/usr/bin/env bash
# bootstrap.sh -- create the Python 3.14 venv and install everything (Linux/macOS/WSL).
#
# Targets CPython 3.14. Uses `uv` if available (recommended), else `python3.14`.
#
# Usage (from this folder):
#     ./bootstrap.sh
#
# Then point your MCP client at:  ./.venv/bin/python -m openerp_mcp.server
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv"
WHEEL="$ROOT/vendor/odoo_client_lib-2.0.1+calcool.311-py3-none-any.whl"

echo "== OpenERP MCP bootstrap (Python 3.14) =="

if command -v uv >/dev/null 2>&1; then
    echo "Using uv to provision CPython 3.14..."
    uv python install 3.14
    uv venv --python 3.14 "$VENV"
    PY="$VENV/bin/python"
    uv pip install --python "$PY" "$WHEEL"
    uv pip install --python "$PY" -e .
elif command -v python3.14 >/dev/null 2>&1; then
    echo "uv not found; using system python3.14..."
    python3.14 -m venv "$VENV"
    PY="$VENV/bin/python"
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install "$WHEEL"
    "$PY" -m pip install -e .
else
    echo "ERROR: neither 'uv' nor 'python3.14' found. Install one, then re-run." >&2
    exit 1
fi

echo ""
echo "Done. Verify with:"
echo "    $PY -c \"import odoolib, openerp_mcp; print('OK', odoolib.__name__)\""
echo ""
echo "Run the server (after copying .env.example to .env and filling it in):"
echo "    $PY -m openerp_mcp.server"
