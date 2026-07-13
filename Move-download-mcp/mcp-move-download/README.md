# mcp-move-download

Serveur MCP (stdio) avec un seul outil : `move_download`.

## Installation

```
npm install
```

## Outil

`move_download(filename, destination)`

- `filename` : nom exact du fichier dans `%USERPROFILE%/Downloads` (pas de chemin).
- `destination` : chemin absolu du dossier cible (créé si besoin).

Conditions imposées :
- le fichier doit exister dans `Downloads` avec un nom **strictement identique**,
- sa dernière écriture (mtime) doit dater de **moins de 5 minutes**,
- pas d'écrasement si un fichier du même nom existe déjà à destination.

## Configuration Claude Desktop

Dans `claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "move-download": {
      "command": "node",
      "args": ["CHEMIN_ABSOLU_VERS/mcp-move-download/server.js"]
    }
  }
}
```
