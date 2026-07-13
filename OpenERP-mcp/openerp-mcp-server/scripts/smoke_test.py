#!/usr/bin/env python3
"""Smoke test du connecteur OpenERP MCP contre un serveur réel.

À lancer SUR la machine qui a accès réseau au serveur (pas dans la sandbox).
Exerce le chemin complet : connexion HTTPS forcée -> login -> lectures via la
couche métier -> résumé du verrouillage. N'écrit rien (lectures seules).

Usage (PowerShell), base de dev 'criticaldev' :

    $env:OPENERP_HOST="portal.critical-optimisation.com"
    $env:OPENERP_DB="criticaldev"
    $env:OPENERP_LOGIN="admin"
    $env:OPENERP_PASSWORD="<mot-de-passe>"
    $env:OPENERP_PORT="443"
    $env:OPENERP_PROTOCOL="xmlrpcs"
    .\.venv\Scripts\python.exe scripts\smoke_test.py

Tout est piloté par les variables d'environnement OPENERP_* (voir .env.example).
"""

from __future__ import annotations

import sys
import time

from openerp_mcp.config import OpenERPConfig, ConfigError
from openerp_mcp.connection import build_connection
from openerp_mcp.business import BusinessLayer


def main() -> int:
    print("=== OpenERP MCP — smoke test (lectures seules) ===\n")

    # 1. Configuration
    try:
        cfg = OpenERPConfig.from_env()
    except ConfigError as exc:
        print(f"[CONFIG] ÉCHEC : {exc}")
        return 2
    print(f"[CONFIG] OK  -> {cfg.redacted()}\n")

    # 2. Connexion HTTPS + authentification
    t0 = time.time()
    try:
        conn = build_connection(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[CONNEXION] ÉCHEC : {type(exc).__name__}: {exc}")
        print("  -> Vérifie host/port/db/login/password et que le certificat est valide.")
        return 3
    dt = (time.time() - t0) * 1000
    print(f"[CONNEXION] OK  -> authentifié user_id={conn.user_id} en {dt:.0f} ms\n")

    layer = BusinessLayer(
        conn, locked_accounts=cfg.locked_accounts, locked_journals=cfg.locked_journals
    )

    # 3. Lecture : version serveur (service 'db'/'common'), puis modèles courants
    checks = []

    try:
        uid = layer.user_id
        me = layer.search_read("res.users", domain=[["id", "=", uid]],
                               fields=["login", "name"], limit=1)
        checks.append(("res.users (moi)", bool(me), me))
    except Exception as exc:  # noqa: BLE001
        checks.append(("res.users (moi)", False, repr(exc)))

    try:
        n_partners = layer.search("res.partner", domain=[], limit=5)
        checks.append(("res.partner search (max 5 ids)", True, n_partners))
    except Exception as exc:  # noqa: BLE001
        checks.append(("res.partner search", False, repr(exc)))

    try:
        companies = layer.search_read("res.company", fields=["name", "currency_id"], limit=5)
        checks.append(("res.company", bool(companies), companies))
    except Exception as exc:  # noqa: BLE001
        checks.append(("res.company", False, repr(exc)))

    # Modèle comptable : présent sur OpenERP 7 si la compta est installée.
    try:
        journals = layer.search_read("account.journal", fields=["code", "name"], limit=10)
        checks.append(("account.journal", True, [j.get("code") for j in journals]))
    except Exception as exc:  # noqa: BLE001
        checks.append(("account.journal (compta installée ?)", False, repr(exc)))

    print("=== Lectures via la couche métier ===")
    all_ok = True
    for name, ok, detail in checks:
        mark = "OK " if ok else "ÉCHEC"
        all_ok = all_ok and ok
        shown = detail if isinstance(detail, (list, dict)) else str(detail)
        print(f"[{mark}] {name}")
        print(f"      -> {shown}")
    print()

    # 4. Résumé du verrouillage (doit être neutre par défaut)
    print("=== Politique de verrouillage active ===")
    print(f"  {layer.locked_summary()}\n")

    print("=== RÉSULTAT :", "TOUT OK ✅" if all_ok else "des lectures ont échoué ⚠️", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
