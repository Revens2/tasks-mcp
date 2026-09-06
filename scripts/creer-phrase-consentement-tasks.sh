#!/usr/bin/env bash
# Pose la phrase de passe du consentement OAuth de tasks-gateway.
# Seule l'empreinte PBKDF2 est écrite (TASKS_MCP_CONSENT_HASH, env 0600).
# Usage : sudo bash /srv/tasks/scripts/creer-phrase-consentement-tasks.sh
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

echo "Phrase de passe du consentement OAuth tasks-mcp (aucun écho, >= 12 caractères)."
echo "C'est elle qui autorisera les connexions ChatGPT/claude.ai à la phase publique."
read -r -s -p "Phrase : " P1; echo
read -r -s -p "Confirmation : " P2; echo
if [ "$P1" != "$P2" ]; then
    echo "Erreur : les deux saisies diffèrent." >&2; exit 1
fi
if [ "${#P1}" -lt 12 ]; then
    echo "Erreur : 12 caractères minimum." >&2; exit 1
fi

HASH="$(printf '%s' "$P1" | "$RACINE/venv/bin/python" -c \
    'import sys
sys.path.insert(0, "/srv/tasks/src")
from tasks_gateway.oauth import hacher_phrase
print(hacher_phrase(sys.stdin.read().strip()))')"

touch "$ENV_FICHIER"
printf '%s' "$HASH" | env_set "$ENV_FICHIER" "TASKS_MCP_CONSENT_HASH"
chown tasks-app:tasks-app "$ENV_FICHIER"
chmod 600 "$ENV_FICHIER"
systemctl restart tasks-gateway.service
echo "Empreinte mise à jour et passerelle redémarrée."
