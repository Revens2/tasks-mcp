# Architecture

## Principes (ordre de priorité)

`simple > robuste > observable > maintenable > sécurisé > sophistiqué`

La stack réutilise les patterns déjà en place sur `vps-etude` (vault-mcp, passerelle
calendar) : systemd durci `User=tasks-app`, nginx NetBird, passerelle OAuth colocalisée,
secrets hors Git. Aucun reverse proxy ni infra parallèle ajouté.

## Choix structurants

### Radicale 3.8 (docker), image épinglée `tomsquest/docker-radicale:3.8.0.0`
- arm64, bcrypt intégré, `--read-only` capable, exécuté non-root (uid 2999 aligné hôte).
- Écoute `127.0.0.1:5232` seulement (publication docker), jamais exposé.
- Auth htpasswd bcrypt **rounds=10** : rounds=12 coûtait ~400 ms/requête (chaque requête
  CalDAV re-vérifie le mot de passe) ; rounds=10 ≈ 60 ms — coût de sécurité inchangé
  pour un endpoint privé NetBird + `delay=1` anti-bruteforce.
- Droits `owner_only` : l'utilisateur ne voit/écrit que `/juliann/...`.

### Listes
- `Inbox` — liste principale (capture iPhone, défaut du MCP).
- `Corbeille` — soft-delete : `tasks_delete` y déplace l'item (conservation 30 j,
  purge au démarrage et via `Service.purger_corbeille`). `tasks_move` restaure.
- La corbeille est une liste CalDAV ordinaire : l'iPhone la voit ; suppression réelle
  = `DELETE` HTTP avec `If-Match` après relecture.

### TLS CalDAV — CA privée (V1 NetBird)
- CA locale `/srv/tasks/pki/ca/tasks-ca.pem` (3650 j), feuille 800 j (limite Apple 825 j)
  couvrant `vps-etude.netbird.selfhosted`, `caldav-vps.netbird.selfhosted` et
  `10.200.114.203` (SAN IP).
- L'iPhone installe la CA une fois (profil) ; ensuite validation normale.
- Phase 2 (ChatGPT public) : remplacer par Let's Encrypt duckdns (pattern vaultwarden),
  la structure nginx ne change pas.

### Concurrence iPhone ↔ ChatGPT
- Toute écriture : relecture → patch iCalendar → `PUT` avec `If-Match` (jamais de
  last-write-wins silencieux). Sur 412 : relecture et `ConflitModification` avec la
  tâche fraîche ; l'outil MCP remonte `conflit:true` + `tache_actuelle`, l'agent relit
  et ré-applique.
- Les propriétés iCalendar inconnues (Apple) sont préservées : le patch est fait par
  arbre `icalendar` (on ne réécrit jamais l'objet depuis zéro).

### Miroir + journal (SQLite `/srv/tasks/data/tasks.db`)
- `taches` : miroir normalisé (uid, liste, etag, champs, ICS brut) pour détecter les
  changements externes et servir les vues sans re-parser CalDAV à chaque requête.
- `journal` : append-only (qui/quoi/quand). Sources : `tasks_mcp` (acteur = en-tête
  posé par la passerelle après auth) et `caldav_external` pour les changements venus
  de l'iPhone via CalDAV (aucune prétention d'identité : CalDAV n'en fournit pas).
- `versions` : snapshots ICS bruts (création/modification/corbeille/suppression) →
  restauration d'une version antérieure possible via SQL.
- Aucun secret dans la base.

### Passerelle MCP (`tasks-gateway`, calquée vault/calendar)
- `/mcp` derrière `RequireAuthMiddleware` : OAuth (RFC 8414/7591/7636, dormant en V1)
  **ou** Bearer statique CLI (`TASKS_MCP_TOKEN`, ≥32 car., rotation scriptée).
- Proxy transparent Streamable HTTP vers l'upstream ; `mcp-session-id` relayé tel quel ;
  l'identité authentifiée est propagée en en-tête **interne** `x-tasks-mcp-acteur`
  (jamais accepté depuis l'extérieur) pour le journal.
- V1 : aucun outil masqué (`OUTILS_RETIRES = {}`) — CRUD complet pour tout client
  authentifié. Le mécanisme de filtrage `tools/list` (JSON + SSE) reste disponible pour
  de futures politiques (lecture seule, interdiction de suppression, scopes par outil).
- nginx : rate-limit 10 r/s (burst 20), 10 connexions/IP, méthode non-MCP coupée 444,
  `client_max_body_size 2m`.

### Outils MCP (16)
`tasks_list`, `tasks_get`, `tasks_search`, `tasks_create`, `tasks_update`,
`tasks_complete`, `tasks_reopen`, `tasks_move`, `tasks_delete`, `tasks_inbox`,
`tasks_today`, `tasks_overdue`, `tasks_upcoming`, `tasks_unscheduled`,
`tasks_recently_changed`, `tasks_history`.

### Contexte ChatGPT → lien de conversation (endpoint /context/chatgpt)
- Extension v2 : le content script ne fait que signaler les navigations SPA
  (URL + titre, jamais le contenu) ; le service worker (cerveau.js) choisit
  l'ONGLET PROPRIÉTAIRE = dernière conversation réellement activée
  (`tabs.onActivated` / `windows.onFocusChanged`) et envoie un heartbeat
  ~45 s (alarme) tant que l'onglet reste sur `/c/<id>`, même en arrière-plan.
- **Anti-effacement croisé** : une page ChatGPT SANS conversation ne peut
  jamais effacer le contexte d'une autre conversation. Chaque dépôt porte un
  `onglet_id` ; l'effacement (`{"actif": false}`) n'est accepté que par
  l'onglet propriétaire (ou, en legacy, par le client d'un dépôt sans onglet).
- L'endpoint vit DANS tasks-mcp (127.0.0.1:8791) — même registre mémoire que
  `tasks_create`, aucune sync inter-processus — et n'est jamais exposé par la
  passerelle `/mcp` : nginx (vhost NetBird 8793 + vhost public HTTPS duckdns)
  route la route exacte vers l'upstream.
- Auth : jeton dédié ultra-scopé `TASKS_CONTEXT_TOKEN` (aucun droit MCP,
  rotation scriptée indépendante, comparé en temps constant). Validation
  stricte de l'URL (`https://chatgpt.com/c/<id>` uniquement, jamais `/share/`),
  titre et libellé de compte assainis (contrôles → espaces, bornés), corps ≤
  4 Ko, rate limits nginx + applicatifs (jeton/IP). Aucune URL/ID de
  conversation journalisée.
- Registre mémoire par `client_id` + TTL (`TASKS_CONTEXT_TTL_S`, défaut 300 s) :
  pas d'historique de navigation. Quand un contexte est frais, `tasks_create`
  appelle la fonction UNIQUE `contexte.composer_notes_avec_contexte` :
  `https://chatgpt.com/c/<id>` en PREMIÈRE ligne, description préservée, puis
  `---\nSource : ChatGPT\nCompte : <label optionnel>\nConversation : <titre>`
  — best-effort, jamais d'échec, jamais de doublon d'URL (ancien bloc
  « Conversation ChatGPT : » retiré si présent dans les notes fournies).
- Réponses explicites : un dépôt valide répond `conversation_detectee:true` /
  `id_present:true` (l'ID n'est écho que si `TASKS_CONTEXT_ECHO_ID=1`, jamais
  par défaut ni loggé) ; `GET /context/chatgpt` (même jeton) expose un
  diagnostic interne `contexte_present`/`age_s`/`raison`
  (`contexte_actif`/`contexte_expire`/`contexte_absent`) sans URL ni ID, pour
  distinguer « serveur accessible » de « conversation détectée » dans le test
  de l'extension.

### Modèle de tâche (normalisé, indépendant d'iCalendar)
`id/uid, title, notes, status (needs_action/completed), completed, completed_at,
priority, due, start, created, updated, list, etag, href, trashed`. Fuseau Europe/Paris,
Unicode/accents/emojis préservés.

## Sécurité appliquée
- systemd durci (NoNewPrivileges, ProtectSystem=strict, RestrictAddressFamilies,
  IPAddressAllow, ReadWritePaths limité à `data/`, UMask 0077).
- Radicale : conteneur non-root, volume dédié, droits `root:radicale`.
- Secrets : 0600/0700, hors Git (`.gitignore`), jamais en argv/logs ; génération via
  stdin (`env_set`, `hacher_bcrypt_stdin`).
- nginx : aucun secret, logs sans jeton (le bearer n'apparaît pas dans les logs nginx —
  vérifié).
- Ports : aucune écoute publique pour CalDAV ni MCP.

## Observabilité
- `systemctl status tasks-mcp tasks-gateway` ; santé HTTP : `GET :8793/health`.
- `radicale-tasks` a un healthcheck docker (`healthy`).
- Compteurs utiles : `sqlite3 data/tasks.db 'SELECT count(*) FROM taches'` (miroir),
  `journal` pour la dernière synchro/erreur. Pas de Prometheus (pas nécessaire pour 3 métriques).
