# Tests

Toutes les suites passent sur le VPS (Python 3.12, venv `/srv/tasks/venv`).
Marker `integration` = exige Radicale démarré + compte bootstrapé
(`TASKS_ENV_FILE=/srv/tasks/secrets/tasks.env`, lecture par root ou tasks-app).

## Exécution complète (sur le VPS)

```bash
sudo -u tasks-app bash -c '
  set -a; . /srv/tasks/secrets/tasks.env; set +a
  export PYTHONPATH=/srv/tasks/src
  export TASKS_ENV_FILE=/srv/tasks/secrets/tasks.env
  cd /srv/tasks
  /srv/tasks/venv/bin/python3 -m pytest tests -v -p no:cacheprovider
'
```

Résultat attendu : **23 passed** (4 intégration + unités MCP + passerelle).

- `tests/mcp/` — unités : modèle de temps (Europe/Paris), conversion iCalendar↔Tache,
  contexte ChatGPT (validation URL/titre, registre TTL, bloc notes), endpoint HTTP
  `/context/chatgpt` (jeton, taux, tailles, effacement), et lien `tasks_create`
  ↔ contexte (frais/expiré/absent, préservation et non-duplication des notes).
- `tests/integration/test_service_caldav.py` — cycle complet CalDAV réel
  (create/get/update/due/complete/reopen/move/soft-delete/restore/permanent),
  conflit ETag (If-Match, pas d'écrasement), détection de changement externe
  (`caldav_external` journalisé), `recently_changed`. Utilise des listes
  `ZZTest-*` créées puis supprimées (teardown).
- `tests/gateway/` — passerelle : découverte RFC 8414/9728, enregistrement dynamique,
  PKCE + consentement par phrase, `/mcp` anonyme → 401, mauvais jeton → 401,
  politique outil par outil (lecture/écriture/fail-closed), 502 si upstream
  indisponible, /health.

## Tests de l'extension (node, poste de développement)

```bash
node --test extension/tests/detect.test.cjs
```

Couvre la logique pure de détection : URL `/c/…` vs autres pages ChatGPT,
détection initiale, navigation SPA, changement d'onglet (visibilité), heartbeat
et effacement hors conversation.

## Tests manuels déjà exécutés (déploiement réel)

1. **CalDAV direct** : découverte principal/calendar-home-set, MKCOL Inbox/Corbeille
   (corps resourcetype calendar obligatoire chez Radicale), PROPFIND listes, auth 401.
2. **CalDAV TLS NetBird depuis le PC** (pair NetBird) :
   `https://netbird.internal.example:5232` — PROPFIND authentifié 207, listes
   Inbox + Corbeille visibles, chaîne validée par la CA privée.
3. **MCP streamable-http via nginx NetBird (:8793/mcp)** avec le SDK officiel :
   initialize → tools/list (16 outils) → tasks_create → tasks_get → tasks_update →
   tasks_complete → tasks_reopen → tasks_recently_changed → tasks_history →
   tasks_delete (corbeille + restauration proposée). Auth : sans jeton → 401,
   mauvais jeton → 401, bon jeton → 200.
4. **Sauvegarde/restauration** : tâche témoin créée → backup → suppression définitive →
   `tasks_restore.sh --restaurer` → tâche de nouveau présente → nettoyée.

## Tests restant à faire avec l'utilisateur (iPhone)

Procédure complète dans `docs/IPHONE.md` — tests bout-en-bout A→F :
création iPhone→MCP, renommage MCP→iPhone, échéance, cocher iPhone→MCP,
création MCP→iPhone, conflit ETag sans écrasement silencieux.
