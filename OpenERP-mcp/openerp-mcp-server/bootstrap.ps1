# bootstrap.ps1 -- create the Python 3.14 venv on Windows and install everything.
#
# Targets CPython 3.14 (your local interpreter is 3.14.3). Uses `uv` if available
# (recommended, provisions an exact 3.14), otherwise falls back to the `py` launcher.
#
# Usage (from this folder, in PowerShell):
#     .\bootstrap.ps1
#
# Then point your MCP client at:  .\.venv\Scripts\python.exe -m openerp_mcp.server

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "== OpenERP MCP bootstrap (Python 3.14) ==" -ForegroundColor Cyan

$venv = Join-Path $ProjectRoot ".venv"

function Test-Command($name) {
    $null = Get-Command $name -ErrorAction SilentlyContinue
    return $?
}

if (Test-Command "uv") {
    Write-Host "Using uv to provision CPython 3.14..." -ForegroundColor Green
    uv python install 3.14
    uv venv --python 3.14 $venv
    $py = Join-Path $venv "Scripts\python.exe"
    # Install the vendored, patched odoo-client-lib wheel first (offline), then the project.
    uv pip install --python $py (Join-Path $ProjectRoot "vendor\odoo_client_lib-2.0.1+calcool.311-py3-none-any.whl")
    uv pip install --python $py -e .
}
elseif (Test-Command "py") {
    Write-Host "uv not found; using the 'py' launcher with -3.14..." -ForegroundColor Yellow
    py -3.14 -m venv $venv
    $py = Join-Path $venv "Scripts\python.exe"
    & $py -m pip install --upgrade pip
    & $py -m pip install (Join-Path $ProjectRoot "vendor\odoo_client_lib-2.0.1+calcool.311-py3-none-any.whl")
    & $py -m pip install -e .
}
else {
    throw "Neither 'uv' nor the 'py' launcher was found. Install one, then re-run."
}

Write-Host ""
Write-Host "Done. Verify with:" -ForegroundColor Cyan
Write-Host "    $py -c `"import odoolib, openerp_mcp; print('OK', odoolib.__name__)`""
Write-Host ""
Write-Host "Run the server (after copying .env.example to .env and filling it in):" -ForegroundColor Cyan
Write-Host "    $py -m openerp_mcp.server"
