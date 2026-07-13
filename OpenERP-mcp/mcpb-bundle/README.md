# OpenERP MCP — bundle .mcpb

Serveur MCP exposant un OpenERP 7 / Odoo via `odoo-client-lib`, connexion **HTTPS forcée**
(proxy Traefik), avec une couche métier neutre (hooks de validation, verrouillage de
comptes/journaux, transactions).

Ce dossier se package en un fichier `.mcpb` installable en un clic dans Claude Desktop.

## Type de serveur : `uv`

Le bundle ne contient que le code + `pyproject.toml`. À l'installation, Claude Desktop
utilise `uv` pour créer l'environnement et installer les dépendances (y compris les paquets
compilés comme pydantic) sur la machine cible — donc cross-plateforme (Windows / macOS / Linux)
sans figer de binaires. La librairie `odoo-client-lib` patchée est fournie dans `vendor/` et
résolue via `[tool.uv.sources]`.

## Construire le .mcpb

Voir `build-mcpb.ps1` (Windows) ou `build-mcpb.sh` (macOS/Linux). En résumé :

```
npm install -g @anthropic-ai/mcpb
mcpb validate manifest.json
mcpb pack . openerp-mcp.mcpb
mcpb sign --self-signed openerp-mcp.mcpb
```

## Installation

Double-clic sur `openerp-mcp.mcpb` (ou glisser dans Claude Desktop → Extensions). Claude
affiche un formulaire pour les paramètres OpenERP (hôte, base, login, mot de passe…). Le mot
de passe est marqué `sensitive` : il est saisi par l'utilisateur et stocké de façon sécurisée
par Claude Desktop, **jamais** inclus dans le bundle.

## Paramètres demandés à l'installation

| Champ | Requis | Défaut | Variable injectée |
|---|---|---|---|
| Hôte OpenERP | ✅ | — | `OPENERP_HOST` |
| Base de données | ✅ | — | `OPENERP_DB` |
| Login | ✅ | — | `OPENERP_LOGIN` |
| Mot de passe | ✅ | — | `OPENERP_PASSWORD` (sensitive) |
| Port TLS | | 443 | `OPENERP_PORT` |
| Protocole | | xmlrpcs | `OPENERP_PROTOCOL` |
| Comptes verrouillés | | — | `OPENERP_LOCKED_ACCOUNTS` |
| Journaux verrouillés | | — | `OPENERP_LOCKED_JOURNALS` |
