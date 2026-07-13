# build-mcpb.ps1 -- construit le bundle .mcpb sur Windows.
#
# Prerequis : Node.js (npx). La CLI mcpb est appelee via npx (installee a la volee).
#
# Usage :
#     .\build-mcpb.ps1                 # pack (non signe) + controle d'integrite
#     .\build-mcpb.ps1 -Sign           # pack + signature self-signed (voir note)
#
# NOTE sur la signature :
#   'mcpb sign --self-signed' s'est revele instable et peut produire un .mcpb
#   corrompu ("Invalid comment length"). Une signature self-signed n'apporte de
#   toute facon aucune confiance (Claude Desktop avertit "editeur non verifie").
#   Par defaut on NE signe PAS. N'utilise -Sign que si tu as un vrai certificat.
#
# Produit : openerp-mcp.mcpb dans ce dossier.

param(
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $PSScriptRoot
$Output = Join-Path $PSScriptRoot "openerp-mcp.mcpb"

# --- Versions minimales requises pour que la CLI mcpb fonctionne -----------
# mcpb (@anthropic-ai/mcpb) n'impose pas d'"engines" mais s'appuie sur des
# dependances (inquirer, node-forge) qui exigent Node >= 18. npx est fourni
# avec npm (>= 9), lui-meme livre avec Node >= 18.
$NodeMin = [Version]"18.0.0"
$NodeRecommended = "20 LTS (ou 22 LTS)"
Write-Host "Pre-requis : Node.js >= $($NodeMin) (recommande : $NodeRecommended), npm >= 9 / npx fourni avec." -ForegroundColor DarkCyan

# --- Initialisation de Node via fnm dans CE shell --------------------------
# Si Node n'est pas deja disponible, on tente d'amorcer fnm (utile quand le
# profil PowerShell ne le fait pas automatiquement).
function Test-NodeAvailable { [bool](Get-Command node -ErrorAction SilentlyContinue) }

if (-not (Test-NodeAvailable)) {
    $fnm = Get-Command fnm -ErrorAction SilentlyContinue
    if ($fnm) {
        Write-Host "Node introuvable : amorcage de fnm dans ce shell..." -ForegroundColor Yellow
        # Charge les variables d'environnement fnm (equivaut a la ligne du profil).
        fnm env --use-on-cd --shell powershell | Out-String | Invoke-Expression
        # Selectionne la version par defaut de fnm.
        try { fnm use default } catch { Write-Host "  (fnm use default a echoue ; tentative avec la version courante)" -ForegroundColor Yellow }
    } else {
        throw "Ni Node ni fnm ne sont disponibles. Installe Node.js (>= $NodeMin) ou fnm, puis relance."
    }
}

if (-not (Test-NodeAvailable)) {
    throw "Node reste indisponible apres l'amorcage fnm. Verifie 'fnm list' et 'fnm default <version>'."
}

# --- Verification des versions actives -------------------------------------
$nodeVerRaw = (& node --version).Trim()          # ex: v22.22.3
$nodeVer = [Version]($nodeVerRaw.TrimStart('v'))
$npxOk = [bool](Get-Command npx -ErrorAction SilentlyContinue)
Write-Host ("Node actif : {0}  |  npx present : {1}" -f $nodeVerRaw, $npxOk) -ForegroundColor DarkCyan
if ($nodeVer -lt $NodeMin) {
    throw "Node $nodeVerRaw est trop ancien pour mcpb (minimum $NodeMin). Fais 'fnm install 20' puis 'fnm default 20'."
}
if (-not $npxOk) {
    throw "npx est introuvable alors que Node est present. Reinstalle npm (fourni avec Node)."
}

# Resout l'executable mcpb : install globale sinon npx.
$mcpbCmd = Get-Command mcpb -ErrorAction SilentlyContinue
if ($mcpbCmd) { $Exe = "mcpb"; $Pre = @() }
else          { $Exe = "npx";  $Pre = @("--yes", "@anthropic-ai/mcpb") }

function Run-Mcpb {
    param([string[]]$CmdArgs)
    $all = $Pre + $CmdArgs
    Write-Host ">> $Exe $($all -join ' ')" -ForegroundColor DarkGray
    & $Exe @all
    if ($LASTEXITCODE -ne 0) { throw "mcpb a echoue (code $LASTEXITCODE) : $($CmdArgs -join ' ')" }
}

# Repart d'un fichier propre.
Remove-Item $Output -ErrorAction SilentlyContinue

Write-Host "== 1/3  Validation du manifest ==" -ForegroundColor Cyan
Run-Mcpb @("validate", "manifest.json")

Write-Host "== 2/3  Packaging ==" -ForegroundColor Cyan
Run-Mcpb @("pack", ".", $Output)

if ($Sign) {
    Write-Host "== 3/3  Signature self-signed (option -Sign) ==" -ForegroundColor Cyan
    Run-Mcpb @("sign", "--self-signed", $Output)
    try { Run-Mcpb @("verify", $Output) }
    catch { Write-Host "  (verification ignoree pour un cert auto-signe)" -ForegroundColor Yellow }
}

# Controle d'integrite : le .mcpb doit etre un ZIP valide et non vide.
Write-Host "== 3/3  Controle d'integrite du ZIP ==" -ForegroundColor Cyan
$size = (Get-Item $Output).Length
Write-Host "  Taille : $size octets"
if ($size -lt 1024) { throw "Le bundle fait moins de 1 Ko : le packaging a echoue." }
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($Output)
    $count = $zip.Entries.Count
    $hasManifest = [bool]($zip.Entries | Where-Object { $_.FullName -eq 'manifest.json' })
    $zip.Dispose()
    Write-Host "  Entrees ZIP : $count"
    if (-not $hasManifest) { throw "manifest.json absent a la racine du ZIP." }
    Write-Host "  ZIP valide, manifest present. OK." -ForegroundColor Green
} catch {
    throw "ZIP invalide : $($_.Exception.Message)"
}

Write-Host ""
Write-Host "Termine -> $Output" -ForegroundColor Green
Write-Host "Installe-le par double-clic ou via Claude Desktop > Extensions." -ForegroundColor Green
