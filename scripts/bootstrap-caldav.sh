#!/usr/bin/env bash
# Bootstrap automatique du compte CalDAV + environnement des services (root).
# Le mot de passe est GÉNÉRÉ ALEATOIREMENT, écrit directement dans ses emplacements
# (empreinte bcrypt pour Radicale, valeur pour tasks-mcp). Il n'est JAMAIS affiché ni
# passé en argv. Récupération par l'utilisateur : sudo bash scripts/afficher-secret.sh
# (ou remplacer ensuite par creer-compte-caldav.sh avec un mot de passe choisi).
# Usage : sudo bash /srv/tasks/scripts/bootstrap-caldav.sh
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

assurer_utilisateurs
mkdir -p "$RACINE/secrets"
umask 077

# 1. Mot de passe aléatoire (jamais affiché).
MDP="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"

# 2. Empreinte bcrypt -> fichier htpasswd Radicale.
HASH="$(printf '%s' "$MDP" | hacher_bcrypt_stdin)"
printf 'juliann:%s\n' "$HASH" > "$USERS_RADICALE"

# 3. Jeton MCP statique (CLI NetBird), régénérable par rotation-jeton-tasks.sh.
JETON="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

# 4. Fichier d'environnement des services (0600, propriété tasks-app).
touch "$ENV_FICHIER"
printf '%s' "http://127.0.0.1:5232" | env_set "$ENV_FICHIER" "TASKS_CALDAV_URL"
printf '%s' "juliann"              | env_set "$ENV_FICHIER" "TASKS_CALDAV_USER"
printf '%s' "$MDP"                 | env_set "$ENV_FICHIER" "TASKS_CALDAV_PASSWORD"
printf '%s' "/srv/tasks/data"      | env_set "$ENV_FICHIER" "TASKS_DATA_DIR"
printf '%s' "Corbeille"            | env_set "$ENV_FICHIER" "TASKS_TRASH_LIST"
printf '%s' "30"                   | env_set "$ENV_FICHIER" "TASKS_TRASH_RETENTION_DAYS"
printf '%s' "8791"                 | env_set "$ENV_FICHIER" "TASKS_UPSTREAM_PORT"
printf '%s' "8792"                 | env_set "$ENV_FICHIER" "TASKS_MCP_PORT"
printf '%s' "http://127.0.0.1:8791" | env_set "$ENV_FICHIER" "TASKS_MCP_UPSTREAM"
printf '%s' "$JETON"               | env_set "$ENV_FICHIER" "TASKS_MCP_TOKEN"
printf '%s' "tasks:lecture tasks:ecriture" | env_set "$ENV_FICHIER" "TASKS_MCP_TOKEN_SCOPES"
printf '%s' "/srv/tasks/data/oauth" | env_set "$ENV_FICHIER" "TASKS_MCP_OAUTH_DIR"
# Issuer : URL publique FUTURE (phase 2 duckdns). Inerte tant que la passerelle n'est
# pas exposée publiquement ; il doit alors être aligné sur l'URL réelle.
printf '%s' "https://mcp.example.org" | env_set "$ENV_FICHIER" "TASKS_MCP_ISSUER"
printf '%s' "" | env_set "$ENV_FICHIER" "TASKS_MCP_CONSENT_HASH"

chown tasks-app:tasks-app "$ENV_FICHIER"
chmod 600 "$ENV_FICHIER"
assurer_permissions
echo "OK : compte CalDAV 'juliann' bootstrapé (mot de passe aléatoire), environnement initialisé."
echo "Pour lire le mot de passe vous-même : sudo bash $RACINE/scripts/afficher-secret.sh"
