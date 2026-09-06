#!/usr/bin/env bash
# Affiche UNIQUEMENT le secret demandé (à l'utilisateur, dans son propre terminal).
# Usage : sudo bash /srv/tasks/scripts/afficher-secret.sh caldav|token|contexte
# Ne jamais exécuter ce script dans un contexte où la sortie est journalisée.
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

case "${1:-}" in
    caldav) env_get "$ENV_FICHIER" "TASKS_CALDAV_PASSWORD" ;;
    token)  env_get "$ENV_FICHIER" "TASKS_MCP_TOKEN" ;;
    contexte) env_get "$ENV_FICHIER" "TASKS_CONTEXT_TOKEN" ;;
    *) echo "Usage: $0 caldav|token|contexte" >&2; exit 2 ;;
esac
echo
