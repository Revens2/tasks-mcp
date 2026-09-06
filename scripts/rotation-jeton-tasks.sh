#!/usr/bin/env bash
# Rotation du jeton statique tasks-mcp (clients CLI NetBird).
# Le nouveau jeton est généré et écrit sans être affiché ; le récupérer ensuite avec
# afficher-secret.sh token. Les clients configurés doivent être mis à jour.
# Usage : sudo bash /srv/tasks/scripts/rotation-jeton-tasks.sh
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

JETON="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
mkdir -p "$RACINE/secrets"
touch "$ENV_FICHIER"
printf '%s' "$JETON" | env_set "$ENV_FICHIER" "TASKS_MCP_TOKEN"
chown tasks-app:tasks-app "$ENV_FICHIER"
chmod 600 "$ENV_FICHIER"
systemctl restart tasks-gateway.service
echo "Jeton MCP tourné (afficher-secret.sh token pour le lire)."
