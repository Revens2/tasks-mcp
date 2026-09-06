# Stack tasks — Apple Rappels ↔ CalDAV (Radicale) ↔ tasks-mcp ↔ ChatGPT

Flux : l'iPhone (application Rappels) synchronise une liste CalDAV privée sur un serveur privé ; `tasks-mcp` expose ces tâches en MCP (Streamable HTTP) à ChatGPT / un
planner quotidien. CalDAV est joignable **uniquement via NetBird** ; le MCP est en
NetBird pour la V1 (CLI/bearer), avec une passerelle OAuth prête pour la phase 2 publique.

```
iPhone (Apple Rappels, liste Inbox)
        │  CalDAV + TLS (CA privée), NetBird uniquement
        ▼
Radicale 3.8 (docker, 127.0.0.1:5232)      ← nginx TLS NetBird :5232
        │  CalDAV local
        ▼
tasks-mcp (systemd, 127.0.0.1:8791)        ← miroir SQLite + journal d'audit
        │  MCP streamable-http (en-tête acteur interne)
        ▼
tasks-gateway (systemd, 127.0.0.1:8792)    ← auth OAuth (dormante) + Bearer CLI
        │
        ▼
nginx NetBird :8793 → /mcp  (ChatGPT/CLI)
```

### Contexte ChatGPT → lien de conversation dans les rappels

Quand tu dis à ChatGPT « je regarderai ça plus tard », la tâche créée contient
dans ses notes un lien cliquable vers la conversation exacte
(`https://chatgpt.com/c/<id>`). Une extension Chrome locale (voir
`docs/CONTEXTE-CHATGPT.md`) publie l'URL de l'onglet actif sur
`POST /context/chatgpt` (jeton dédié, validation stricte) ; `tasks_create`
ajoute le bloc aux notes si le contexte est récent (TTL 300 s). Rien n'est
envoyé à un tiers, aucun lien public `share` n'est généré.

## Services et ports

| Composant | Emplacement | Écoute | Accès |
|---|---|---|---|
| Radicale 3.8.0.0 (conteneur `radicale-tasks`) | Docker | `127.0.0.1:5232` | interne |
| nginx vhost `caldav-tasks` | hôte | `198.51.100.10:5232` (TLS, CA privée) | NetBird |
| tasks-mcp (upstream, `tasks-mcp.service`) | systemd, user `tasks-app` | `127.0.0.1:8791` | interne |
| tasks-gateway (`tasks-gateway.service`) | systemd, user `tasks-app` | `127.0.0.1:8792` | interne |
| nginx vhost `tasks-mcp` | hôte | `127.0.0.1:8793` + `198.51.100.10:8793` | NetBird |
| Sauvegarde (`tasks-backup.timer`) | systemd | quotidienne 03:10 | — |

Aucune écoute publique (198.51.100.80) sur ces ports : l'exposition est impossible au
niveau bind, en plus du firewall.

## Répertoires

- `/srv/tasks/radicale/data` — collections (persistant, volume docker).
- `/srv/tasks/radicale/config` — config + `users` (empreintes bcrypt), 640 root:radicale.
- `/srv/tasks/data` — base SQLite `tasks.db` (miroir + journal + versions) + état OAuth.
- `/srv/tasks/secrets/tasks.env` — secrets d'exécution (0600 tasks-app). **Jamais commité.**
- `/srv/tasks/pki` — CA privée + certificat feuille CalDAV (racine uniquement).
- `/srv/tasks/backups` — archives `tasks-<ts>.tar.gz` (racine, 600), rétention 14.

## Lecture rapide

- `docs/IPHONE.md` — configuration de l'iPhone + tests bout-en-bout A→F.
- `docs/OPERATIONS.md` — sauvegarde/restauration, rotation du jeton et du mot de passe,
  renouvellement PKI, santé.
- `docs/TESTS.md` — suites de tests et leur exécution.
- `docs/ARCHITECTURE.md` — décisions, modèle de données, concurrence, sécurité.
- `docs/CONTEXTE-CHATGPT.md` — contexte ChatGPT : extension, endpoint, jeton,
  sécurité, tests manuels, rotation, désinstallation.

## Identifiants (jamais dans Git)

- Compte CalDAV : `juliann` (mot de passe dans `secrets/tasks.env`,
  récupérable par `sudo bash scripts/afficher-secret.sh`).
- Jeton MCP CLI : `TASKS_MCP_TOKEN` (rotation : `sudo bash scripts/rotation-jeton-tasks.sh`).
- Jeton contexte ChatGPT (extension) : `TASKS_CONTEXT_TOKEN` (rotation :
  `sudo bash scripts/rotation-jeton-contexte-chatgpt.sh` ; lecture :
  `sudo bash scripts/afficher-secret.sh contexte`).
- Phrase de consentement OAuth (phase 2) : `TASKS_MCP_CONSENT_HASH`.

## Mise à jour

```bash
cd /srv/tasks && git pull
sudo bash /srv/tasks/deploy/install.sh   # unités systemd + nginx (idempotent)
# + redémarrage des services Python si le code a changé
sudo systemctl restart tasks-mcp.service tasks-gateway.service
```
