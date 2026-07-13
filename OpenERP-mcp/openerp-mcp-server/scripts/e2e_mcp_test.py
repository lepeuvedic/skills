#!/usr/bin/env python3
"""Test de BOUT EN BOUT passant par le protocole MCP stdio.

Contrairement à smoke_test.py (qui importe la librairie directement), ce script :
  1. lance `python -m openerp_mcp.server` comme un SOUS-PROCESSUS, exactement comme
     le ferait un client MCP (Claude Desktop / Cowork),
  2. effectue le handshake MCP (`initialize`),
  3. liste les outils via `tools/list`,
  4. APPELLE les outils via le protocole (`tools/call`) : openerp_whoami,
     puis openerp_search_read sur res.users / res.company,
  5. parse les enveloppes JSON renvoyées par les outils.

Le serveur MCP est donc réellement mis en œuvre ici : on ne touche jamais
openerp_mcp.* directement, tout passe par stdin/stdout du sous-processus.

À lancer SUR la machine qui a accès réseau au serveur OpenERP (pas la sandbox).
Les identifiants sont transmis au sous-processus via l'environnement OPENERP_*.

Usage (PowerShell), base de dev 'criticaldev' :

    $env:OPENERP_HOST="portal.critical-optimisation.com"
    $env:OPENERP_DB="criticaldev"
    $env:OPENERP_LOGIN="admin"
    $env:OPENERP_PASSWORD="<mot-de-passe>"
    $env:OPENERP_PORT="443"
    $env:OPENERP_PROTOCOL="xmlrpcs"
    .\.venv\Scripts\python.exe scripts\e2e_mcp_test.py

Dépend uniquement du SDK `mcp` (déjà une dépendance du projet).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


def _unwrap(call_result) -> dict:
    """Extrait l'enveloppe JSON {"ok":..., ...} renvoyée par un outil."""
    if not call_result.content:
        return {"ok": False, "error": {"kind": "empty", "message": "no content"}}
    text = call_result.content[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"kind": "non_json", "message": text[:300]}}


async def run() -> int:
    # On lance le MÊME interpréteur (celui du venv) que ce script.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openerp_mcp.server"],
        env={**os.environ},  # transmet les OPENERP_* au sous-processus serveur
    )

    print("=== Test E2E via protocole MCP stdio ===")
    print(f"Lancement du serveur : {sys.executable} -m openerp_mcp.server\n")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Handshake
            init = await session.initialize()
            print(f"[MCP] initialize OK -> serveur='{init.serverInfo.name}' "
                  f"protocole={init.protocolVersion}")

            # 2. Liste des outils (via le protocole)
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"[MCP] tools/list -> {len(names)} outils : {names}\n")

            overall_ok = True

            # 3. openerp_whoami (connexion HTTPS + login se font ICI, côté serveur)
            print("[CALL] openerp_whoami ...")
            who = _unwrap(await session.call_tool("openerp_whoami", {}))
            print("  ->", json.dumps(who, ensure_ascii=False))
            if not who.get("ok"):
                print("\n[RÉSULTAT] La connexion/auth a échoué côté serveur MCP. "
                      "Détail ci-dessus (kind/message).")
                return 3
            user_id = who["result"]["user_id"]
            print(f"  OK -> user_id={user_id}\n")

            # 4. openerp_search_read sur res.users (moi)
            print("[CALL] openerp_search_read res.users (moi) ...")
            me = _unwrap(await session.call_tool("openerp_search_read", {
                "params": {
                    "model": "res.users",
                    "domain": [["id", "=", user_id]],
                    "fields": ["login", "name"],
                    "limit": 1,
                }
            }))
            print("  ->", json.dumps(me, ensure_ascii=False))
            overall_ok = overall_ok and me.get("ok", False)

            # 5. openerp_search_read sur res.company
            print("\n[CALL] openerp_search_read res.company ...")
            comp = _unwrap(await session.call_tool("openerp_search_read", {
                "params": {
                    "model": "res.company",
                    "fields": ["name", "currency_id"],
                    "limit": 5,
                }
            }))
            print("  ->", json.dumps(comp, ensure_ascii=False))
            overall_ok = overall_ok and comp.get("ok", False)

            print("\n=== RÉSULTAT :",
                  "TOUT OK ✅ (bout en bout via MCP)" if overall_ok
                  else "des appels d'outils ont échoué ⚠️", "===")
            return 0 if overall_ok else 1


def main() -> int:
    try:
        return asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        print(f"[E2E] ÉCHEC inattendu : {type(exc).__name__}: {exc}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
