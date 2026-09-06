# Contexte ChatGPT → liens de conversation dans les rappels

Quand ChatGPT crée une tâche via Tasks MCP (« je regarderai ça plus tard »…),
le rappel (Apple Rappels) contient désormais un lien cliquable vers la
conversation ChatGPT exacte qui a déclenché la création : l'URL privée
`https://chatgpt.com/c/<id>` dans les notes.

## Fonctionnement

```
Onglet sur https://chatgpt.com/c/<id>   (la dernière conversation que tu as activée)
        │  content script : signale les navigations SPA (URL + titre uniquement)
        ▼
Service worker (cerveau.js) : choisit l'onglet PROPRIÉTAIRE
        │  = dernière conversation réellement activée (tabs/windows/onActivated)
        │  heartbeat automatique ~45 s tant que l'onglet reste sur /c/<id>
        │  (même en arrière-plan : une conversation active n'expire plus)
        ▼
POST /context/chatgpt   (tasks-mcp, 127.0.0.1:8791 — via nginx HTTPS public ou NetBird)
        │  jeton dédié « browser context writer » (TASKS_CONTEXT_TOKEN)
        │  validation stricte : https://chatgpt.com/c/<id> uniquement
        ▼
Registre mémoire (TTL 300 s) — {url, conversation_id, titre?, client_id, onglet_id, vu_le}
        │
ChatGPT (n'importe où) appelle tasks_create via /mcp (flux existant inchangé)
        ▼
tasks_create : si un contexte récent existe → composer_notes_avec_contexte
            (URL en PREMIÈRE ligne + pied Source/Compte/Conversation)
        ▼
Radicale (CalDAV) → synchro iPhone → Rappels affiche la note avec le lien cliquable
```

Points clés (v2 — corrige le bug « contexte effacé en boucle ») :

- **Un seul propriétaire** : le contexte publié est celui de la **dernière
  conversation réellement activée** (onglet activé / fenêtre focalisée).
  Basculer A → B → A republie A ; un onglet conversation en arrière-plan ne
  vole jamais la propriété.
- **Heartbeat ~45 s piloté par le worker** (alarme), pas par la visibilité de
  l'onglet : tant que l'onglet propriétaire reste ouvert sur `/c/<id>`, le TTL
  serveur (300 s) est rafraîchi — même fenêtre réduite ou onglet en fond.
- **Aucun effacement croisé** : une page ChatGPT SANS conversation (accueil,
  `/share/`…) ne peut **jamais** effacer le contexte d'une conversation ouverte
  ailleurs. L'effacement n'a lieu que si l'**onglet propriétaire** quitte sa
  conversation (navigation SPA/chargement) ou se ferme. Côté serveur,
  l'effacement n'est accepté que s'il porte le même `onglet_id` que le dépôt.
- Le serveur conserve **un seul contexte récent** par client (pas d'historique
  de navigation) ; sans contexte frais, `tasks_create` crée la tâche **sans**
  lien plutôt que d'ajouter un mauvais lien (raison journalisée, jamais l'URL).
- Aucun lien `chatgpt.com/share/…` n'est jamais généré ; rien n'est envoyé à un
  tiers ; aucune URL de conversation n'est journalisée.

## Format des notes (v3 — URL en tête)

Quand `tasks_create` dispose d'un contexte valide, les notes finales sont
composées par la fonction UNIQUE `composer_notes_avec_contexte` :

```text
https://chatgpt.com/c/<conversation-id>

<description originale de l'agent>

---
Source : ChatGPT
Compte : <label configuré>          ← omis si non renseigné
Conversation : <titre réel>          ← omis si indisponible
```

- L'URL est impérativement la **première ligne** : depuis Apple Rappels, un
  toucher ouvre la conversation (critère principal).
- La description originale n'est jamais supprimée ni altérée ; notes vides →
  URL puis pied de page.
- **Une seule occurrence** de l'URL : si les notes fournies contiennent déjà
  cette URL (agent qui l'aurait collée) ou l'ancien bloc
  « `Conversation ChatGPT :` », celui-ci est retiré avant composition — les
  anciens rappels existants ne sont pas modifiés.
- Le libellé de compte est un **label local configuré dans l'extension** (ex.
  « ChatGPT principal ») — jamais de cookies, session, email ou scraping.
  Absent → ligne omise, `tasks_create` n'est jamais bloqué.

## Mise en route pas à pas (validée le 2026-09-06)

### 1. Charger l'extension dans Chrome

1. Copie le dossier `extension/chatgpt-contexte` du dépôt vers un emplacement
   stable (ex. `Documents/extension-chatgpt-contexte`).
2. Chrome/Chromium/Brave → `chrome://extensions` → active **Mode développeur**.
3. **Charger l'extension non empaquetée** → sélectionne le dossier qui contient
   `manifest.json`.
4. La carte « Contexte ChatGPT → Tasks MCP » doit apparaître **sans erreur**.

   > Si Chrome affiche « Échec du chargement de l'extension », vérifie que le
   > dossier choisi est bien le bon et que ton dépôt est à jour (voir
   > Dépannage).

### 2. Récupérer le jeton « browser context writer »

⚠️ Secret : lis-le dans **ton** terminal, ne le colle jamais dans un chat/log.

```bash
ssh vps-etude "sudo bash /srv/tasks/scripts/afficher-secret.sh contexte"
```

### 3. Configurer l'extension

1. Clic sur l'**icône de l'extension** (ou clic droit → Options).
2. **Endpoint** : `https://tasks-mcp.duckdns.org/context/chatgpt`
3. **Jeton** : colle le jeton de l'étape 2.
4. **Nom de ce compte** (optionnel) : ex. « ChatGPT principal » — ce label est
   affiché en bas du rappel (`Compte : …`). Chaque profil Brave/Chrome peut
   avoir son propre label. Vide → la ligne `Compte :` est omise.
5. **Enregistrer** → Chrome demande la permission d'accéder à l'endpoint →
   **Autoriser** (une seule fois).
6. **Tester le serveur** → doit afficher 🟢 « Serveur accessible —
   authentification valide » (transport + jeton uniquement).
7. **Tester la conversation courante** (onglet ChatGPT visible sur
   `chatgpt.com/c/…`) → doit afficher 🟢 « conversation détectée — ID présent —
   contexte enregistré ».

### Deux tests distincts — signification des états

**« Tester le serveur »** vérifie seulement que l'endpoint répond et que le
jeton est accepté (transport + authentification). Un 🟢 ici ne dit RIEN sur la
détection d'une conversation.

**« Tester la conversation courante »** vérifie le pipeline complet : onglet
actif sur ChatGPT, URL `/c/<id>`, envoi de la VRAIE URL de l'onglet, puis
confirmation du stockage côté serveur.

| État affiché | Signification |
|---|---|
| 🟢 Serveur accessible — authentification valide | transport + jeton OK (test serveur) |
| 🟢 conversation détectée — ID présent — contexte enregistré | pipeline complet vérifié (test conversation) |
| 🟠 Serveur accessible — aucune conversation `/c/<id>` détectée dans l'onglet actif | serveur OK, mais la page ChatGPT ouverte n'est pas une conversation (accueil, `/share/…`, `/g/…`) — pas un problème réseau |
| 🟠 l'onglet actif n'est pas ChatGPT | le test conversation n'a rien à vérifier sur cet onglet |
| 🔴 Serveur accessible — authentification refusée (401) | jeton invalide ou tourné |
| 🔴 Impossible de joindre Tasks MCP | problème réseau / endpoint injoignable |
| 🔴 URL ChatGPT détectée mais format de conversation invalide (400) | l'ID après `/c/` ne passe pas la validation serveur |

Le serveur ne renvoie l'ID de conversation dans sa réponse **que** si
`TASKS_CONTEXT_ECHO_ID=1` (débogage) ; par défaut la réponse d'un dépôt valide
est `{"statut": "ok", "conversation_detectee": true, "id_present": true,
"ttl_s": …}` — l'ID reste local à l'extension.

### Vue ensemble de la popup (diagnostic sans logs)

La page d'options/popup affiche, sans exposer ni URL ni ID :

```text
Serveur : 🟢 joignable
Conversation : 🟢 active (/c/…) — heartbeat automatique
Contexte : 🟢 actif (âge 12 s)
Dernier envoi : il y a 12 s (réussi)
```

- `Conversation` = état local du worker (onglet propriétaire présent ?) ;
- `Contexte` = diagnostic serveur (`contexte_actif` / `contexte_expire` /
  `contexte_absent` + âge) ;
- `Dernier envoi` = dernier POST du worker (heartbeat), jamais l'URL.

### 4. Vérification réelle

1. Ouvre une conversation ChatGPT (`chatgpt.com/c/...`), onglet visible ~5 s.
2. Options de l'extension → « Dernier envoi : réussi ».
3. Dans ChatGPT : « Je regarderai ça plus tard : <sujet> » → la tâche est créée.
4. iPhone → Rappels → Inbox : la note commence par
   `https://chatgpt.com/c/<id>` (première ligne, tapable) puis la description,
   puis `---\nSource : ChatGPT\nCompte : …\nConversation : …` ; le lien s'ouvre
   sous ton compte.

## Dépannage

| Symptôme | Cause → solution |
|---|---|
| « Échec du chargement de l'extension — locale name must be a string » | version du dépôt antérieure au fix `manifest.json` (clé `default_locale` retirée) → mettre le dépôt à jour puis « Réessayer » ou recharger le dossier |
| « Dernier envoi : échec — config » | endpoint/jeton non enregistrés → refaire l'étape 3 |
| « échec (HTTP 401) » | mauvais jeton ou rotation récente → relire le jeton et le re-saisir |
| « échec (HTTP 429) » | trop de requêtes → attendre ~1 min (l'extension retente seule) |
| Dernier envoi : échec, sans conversation ouverte | normal : pas d'onglet propriétaire → pas d'envoi ; la Vue ensemble (popup) l'explique |
| Rappel créé sans lien | aucune conversation active au moment du `tasks_create` : onglet conversation fermé/quitté, ou aucun onglet `/c/…` activé depuis le démarrage. La popup distingue « aucun contexte » / « expiré » / « actif » |
| Lien d'une ANCIENNE conversation encore ajouté | l'onglet propriétaire est resté ouvert sur `/c/…` (le contexte suit la dernière conversation activée) → ferme/quitte cet onglet pour l'effacer |

## Composants

| Fichier (dépôt tasks-mcp) | Rôle |
|---|---|
| `src/tasks_mcp/contexte.py` | validation stricte URL/titre/libellé, registre mémoire TTL, `composer_notes_avec_contexte` (URL 1re ligne + pied) |
| `src/tasks_mcp/contexte_http.py` | endpoint ASGI `POST /context/chatgpt` (jeton, rate limit, taille, effacement par onglet propriétaire) |
| `src/tasks_mcp/outils.py` | `tasks_create` : appel best-effort à `composer_notes_avec_contexte` si contexte récent |
| `deploy/nginx/tasks-mcp.conf` | vhost NetBird : `location = /context/chatgpt` → upstream 8791 |
| `deploy/nginx/tasks-mcp-public.conf` | vhost public HTTPS (duckdns) : même location |
| `scripts/rotation-jeton-contexte-chatgpt.sh` | rotation du jeton contexte |
| `extension/chatgpt-contexte/` | extension Chrome MV3 (charger ce dossier) |
| `extension/chatgpt-contexte/cerveau.js` | logique pure « onglet propriétaire » (testée en node) |
| `extension/tests/detect.test.cjs` | tests node du parsing URL/titre |
| `extension/tests/cerveau.test.cjs` | tests node du cerveau (A→B→A, anti-effacement croisé, heartbeat) |

L'endpoint vit **dans le processus tasks-mcp** (même registre mémoire que
`tasks_create`) et **jamais** dans la passerelle OAuth `/mcp` : le jeton
contexte ne peut rien faire d'autre que déposer/effacer ce contexte.

## Installation de l'extension (mode développeur)

1. Clone/à jour du dépôt tasks-mcp, dossier `extension/chatgpt-contexte`.
2. Chrome/Chromium/Brave → `chrome://extensions` → activer **Mode développeur**.
3. **Charger l'extension non empaquetée** → sélectionner `extension/chatgpt-contexte`.
4. Cliquer sur l'icône de l'extension → renseigner :
   - **Endpoint** : `https://tasks-mcp.duckdns.org/context/chatgpt`
     (ou `http://10.200.114.203:8793/context/chatgpt` en NetBird-only) ;
   - **Jeton** : le « browser context writer » (voir plus bas).
   - **Nom de ce compte** (optionnel) : label affiché en bas du rappel.
   - **Enregistrer** (Chrome demande alors la permission d'accéder à l'endpoint
     choisi — c'est la seule permission d'hôte supplémentaire demandée).
5. **Tester la connexion** dans la page d'options : `HTTP 200` attendu.

Permissions demandées (minimum) : `storage` (config + état session) +
`alarms` (heartbeat) + accès à `chatgpt.com` (content script) + l'hôte de
l'endpoint choisi. **Aucun** accès aux cookies, à l'historique, à Gmail ou au
contenu des pages : l'extension ne lit que l'URL et le titre des onglets
`chatgpt.com` (le contenu des conversations n'est jamais lu).

## Création du jeton « browser context writer »

Le jeton est **ultra-scopé** : il ne sert qu'à cet endpoint (déposer/effacer le
contexte). Il ne donne aucun droit MCP (il n'est jamais présenté à la passerelle
`/mcp`) et se révoque indépendamment des autres secrets.

```bash
# Sur le VPS (génère un jeton, l'écrit dans secrets/tasks.env sans l'afficher,
# redémarre tasks-mcp) :
sudo bash /srv/tasks/scripts/rotation-jeton-contexte-chatgpt.sh

# Puis lis-le toi-même, dans TON terminal (jamais dans un log) :
sudo bash /srv/tasks/scripts/afficher-secret.sh contexte
```

Saisis ensuite ce jeton dans les options de l'extension.

## Variables d'environnement (secrets/tasks.env — jamais dans Git)

| Variable | Rôle |
|---|---|
| `TASKS_CONTEXT_TOKEN` | jeton Bearer « browser context writer » (absent → endpoint inerte 503) |
| `TASKS_CONTEXT_TTL_S` | durée de validité d'un contexte, secondes (défaut 300) |
| `TASKS_CONTEXT_ECHO_ID` | `1` = renvoyer `conversation_id` dans la réponse du dépôt (débogage local uniquement ; défaut 0, jamais loggé) |

`.env.example` ne contient que des placeholders (documentation).

### Réponses de l'endpoint `/context/chatgpt`

- `POST` avec URL de conversation valide → `200` : `{"statut": "ok",
  "conversation_detectee": true, "id_present": true, "ttl_s": 300}`
  (+ `conversation_id` seulement si `TASKS_CONTEXT_ECHO_ID=1`). Champs
  acceptés du corps : `url` (requis), `title`, `account_label` (label de
  compte local, optionnel), `client_id`, `onglet_id`.
- `POST` `{"actif": false, "client_id": …, "onglet_id": …}` → `200` :
  `{"statut": "ok", "efface": n}`. L'effacement n'est accepté que si
  `onglet_id` correspond à l'onglet qui a déposé le contexte (sinon `0`) :
  une page sans conversation ne peut pas effacer le contexte d'une autre
  conversation. Le bouton « Tester le serveur » (probe `actif:false` sans
  contexte) renvoie `efface: 0` sans rien détruire.
- `GET` (même jeton) → diagnostic interne `{"statut": "ok",
  "contexte_present": bool, "id_present": bool, "age_s": nombre|null,
  "raison": "contexte_actif"|"contexte_expire"|"contexte_absent",
  "dernier_depot_s": nombre|null, "ttl_s": 300}` — ne renvoie jamais l'URL
  ni l'ID de conversation, aucun historique. La raison distingue « aucun
  contexte », « expiré » et « actif » (l'extension l'affiche dans sa popup).
- Erreurs : `400` (URL invalide, ex. `/share/`, autre hôte, ID trop court),
  `401` (jeton), `405` (méthode), `413` (corps), `415` (type), `429` (trop de
  requêtes), `503` (endpoint non configuré).

## Test manuel

1. Ouvre une conversation ChatGPT (`chatgpt.com/c/<uuid>`) ; laisse l'onglet
   visible ~5 s. Page d'options de l'extension → « Dernier envoi : réussi ».
2. Dans ChatGPT (même conversation) : « Je regarderai ça plus tard :
   <sujet> » → ChatGPT appelle `tasks_create`.
3. `tasks_get` (ou l'iPhone après synchro) : les notes commencent par
   `https://chatgpt.com/c/<url>` puis `---\nSource : ChatGPT\nCompte : …`
   et `Conversation : …`. Le lien s'ouvre sous ton compte.
4. Ferme l'onglet de la conversation (ou navigue son onglet vers l'accueil) →
   le contexte est effacé : une création de tâche ultérieure n'aura **pas** de
   lien. Une page accueil ouverte dans un AUTRE onglet n'efface rien.
5. Test de durée : laisse la conversation ouverte (onglet actif ou en fond)
   plus de 5 minutes, puis crée une tâche → le lien est TOUJOURS présent
   (heartbeat ~45 s du worker, indépendant de la visibilité de l'onglet).

## Désinstallation

1. `chrome://extensions` → **Supprimer** l'extension (aucune donnée conservée
   côté serveur : le contexte vit en mémoire et expire en 5 min).
2. (Optionnel) révoquer le jeton : rotation (ci-dessus) ou retrait de la ligne
   `TASKS_CONTEXT_TOKEN=` dans `secrets/tasks.env` + `systemctl restart tasks-mcp`.

## Rotation du jeton

`sudo bash /srv/tasks/scripts/rotation-jeton-contexte-chatgpt.sh` puis mise à
jour du jeton dans les options de l'extension. La rotation précédente cesse
d'être acceptée immédiatement (une seule valeur en vigueur).

## Sécurité (mini threat model)

| Menace | Protection |
|---|---|
| SSRF / URL arbitraire | validation stricte : `https://chatgpt.com/c/<id>` exactement ; http, autres hôtes, ports, query, fragments, userinfo, `/share/`, `/s/`, `/g/` rejetés |
| Injection via titre | caractères de contrôle neutralisés (→ espaces), longueur ≤ 200, jamais interprété (texte seul dans les notes) |
| Fuite de secrets | jeton dédié hors du code (saisi dans les options) ; jamais journalisé ; aucune clé MCP dans l'extension |
| Endpoint public | HTTPS (duckdns), jeton ≥ 32 car. (temps constant), rate limit nginx (10 r/s) + applicatif (30/min jeton, 120/min IP), corps ≤ 4 Ko (nginx 8 Ko) |
| Rejeu / spam | bearer + rate limits + TTL court ; le pire effet d'un abus est un lien erroné dans une note |
| CSRF | en-tête `Authorization` + `Content-Type: application/json` → preflight obligatoire pour un site tiers ; endpoint hors CORS |
| CORS | aucune en-tête CORS : seul le contexte d'extension (permission d'hôte accordée) peut appeler |
| Logs | aucune URL/ID de conversation journalisée (logs nginx = chemin seul ; aucun log applicatif du corps) |
| Permissions Chrome | `storage` + `alarms` + `chatgpt.com` + hôte endpoint choisi (déclaré optionnel, demandé au premier usage) ; aucun accès cookies/historique/contenu |
| Vie privée | pas d'analytics, pas de tiers, pas de lien `share`, registre en mémoire uniquement |

TTL 300 s : assez long pour couvrir une création de tâche juste après la
demande (et le heartbeat ~45 s du worker le maintient frais pendant une session
active), assez court pour ne jamais associer une tâche à une conversation
abandonnée.

## Évolution (compatibilité)

Le schéma stocké est `source/url/titre/account_label/vu_le/client_id/onglet_id`.
Seule `source=chatgpt` existe aujourd'hui ; un futur client IA n'aura qu'à
publier son propre contexte sur le même endpoint (champs `source` et
`LIBELLES_SOURCE` étendus sans rupture). Pas d'abstraction supplémentaire
aujourd'hui — simple, fiable, sécurisé.
