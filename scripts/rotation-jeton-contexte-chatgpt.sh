#!/usr/bin/env bash
# Rotation du jeton « browser context writer » (TASKS_CONTEXT_TOKEN).
# Ce jeton ne sert QU'À l'endpoint POST /context/chatgpt de l'extension
# navigateur : il ne donne aucun droit MCP. Le nouveau jeton est généré et
# écrit sans être affiché ; le récupérer ensuite avec afficher-secret.sh
# contexte (ou directement dans secrets/tasks.env par root), puis le saisir
# dans les options de l'extension Chrome.
# Usage : sudo bash /srv/tasks/scripts/rotation-jeton-contexte-chatgpt.sh
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

JETON="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
mkdir -p "$RACINE/secrets"
touch "$ENV_FICHIER"
printf '%s' "$JETON" | env_set "$ENV_FICHIER" "TASKS_CONTEXT_TOKEN"
chown tasks-app:tasks-app "$ENV_FICHIER"
chmod 600 "$ENV_FICHIER"
systemctl restart tasks-mcp.service
echo "Jeton contexte ChatGPT tourné (le lire soi-même puis mettre à jour l'extension)."
