# tasks-mcp

Stack « tasks » : faire dialoguer **Apple Rappels** (CalDAV) avec des agents IA via MCP.

- `tasks-mcp` — serveur MCP (Streamable HTTP) sur un miroir SQLite des tâches CalDAV.
- `tasks-gateway` — passerelle d'authentification OAuth 2.1 colocalisée (Bearer CLI / OAuth ChatGPT).
- `radicale` (Docker) — backend CalDAV, publication limitée à la boucle locale.
- Déploiement hôte : `deploy/` (systemd, nginx, scripts d'installation et de sauvegarde).

Documentation : [`docs/`](docs/README.md) (architecture, procédure iPhone, opérations, tests).
