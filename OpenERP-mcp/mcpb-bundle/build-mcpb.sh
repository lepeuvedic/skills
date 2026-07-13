#!/usr/bin/env bash
# build-mcpb.sh -- construit (et signe) le bundle .mcpb (macOS/Linux/WSL).
#
# Prérequis : Node.js (pour npx/npm). La CLI mcpb est installée à la volée.
#
# Usage :
#     ./build-mcpb.sh            # pack + signature self-signed
#     ./build-mcpb.sh --no-sign  # pack seulement (non signé)
#
# Produit : openerp-mcp.mcpb dans ce dossier.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
OUTPUT="$ROOT/openerp-mcp.mcpb"
NO_SIGN=0
[ "${1:-}" = "--no-sign" ] && NO_SIGN=1

mcpb() { npx --yes @anthropic-ai/mcpb "$@"; }

echo "== 1/3  Validation du manifest =="
mcpb validate manifest.json

echo "== 2/3  Packaging =="
mcpb pack . "$OUTPUT"

if [ "$NO_SIGN" -eq 0 ]; then
    echo "== 3/3  Signature self-signed =="
    mcpb sign --self-signed "$OUTPUT"
    echo "Vérification de la signature :"
    # 'verify' peut signaler 'not signed' pour un cert auto-signé non rattaché à une
    # autorité de confiance : c'est normal. Claude Desktop installe en avertissant que
    # l'éditeur n'est pas vérifié.
    mcpb verify "$OUTPUT" || echo "  (avertissement de vérification ignoré pour un cert auto-signé)"
else
    echo "== 3/3  Signature ignorée (--no-sign) =="
fi

echo ""
echo "Terminé -> $OUTPUT"
echo "Installe-le par double-clic ou via Claude Desktop > Extensions."
