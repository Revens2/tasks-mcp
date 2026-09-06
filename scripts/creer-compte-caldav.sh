#!/usr/bin/env bash
# Crée / remplace le mot de passe du compte CalDAV 'juliann' (Radicale).
# Saisie interactive SANS écho ; l'agent n'intervient pas. Si l'utilisateur le choisit,
# le même mot de passe est écrit pour le service tasks-mcp (TASKS_CALDAV_PASSWORD).
# Usage : sudo bash /srv/tasks/scripts/creer-compte-caldav.sh
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

assurer_utilisateurs

echo "Nouveau mot de passe du compte CalDAV 'juliann' (aucun écho, >= 12 caractères)."
read -r -s -p "Mot de passe : " P1; echo
read -r -s -p "Confirmation : " P2; echo
if [ "$P1" != "$P2" ]; then
    echo "Erreur : les deux saisies diffèrent." >&2; exit 1
fi
if [ "${#P1}" -lt 12 ]; then
    echo "Erreur : 12 caractères minimum." >&2; exit 1
fi

HASH="$(printf '%s' "$P1" | hacher_bcrypt_stdin)"
printf 'juliann:%s\n' "$HASH" > "$USERS_RADICALE"
assurer_permissions

echo
echo -n "Réutiliser ce mot de passe pour le service tasks-mcp (recommandé) ? [O/n] "
read -r -s REP; REP="${REP:-O}"; echo
case "$REP" in
    O|o|"")
        mkdir -p "$RACINE/secrets"; touch "$ENV_FICHIER"
        printf '%s' "$P1" | env_set "$ENV_FICHIER" "TASKS_CALDAV_PASSWORD"
        chown tasks-app:tasks-app "$ENV_FICHIER"; chmod 600 "$ENV_FICHIER"
        ;;
    *) echo "Le service tasks-mcp conserve son mot de passe actuel." ;;
esac

# Radicale relit le fichier au changement (cache sur mtime) ; on force un rechargement
# propre. tasks-mcp garde les identifiants en mémoire dans son client HTTP : restart
# obligatoire pour reprendre le nouveau mot de passe (sinon 401 sur toutes ses requêtes).
docker restart radicale-tasks >/dev/null
if systemctl list-unit-files tasks-mcp.service >/dev/null 2>&1; then
    systemctl restart tasks-mcp.service
fi
echo "OK : compte CalDAV 'juliann' mis à jour."
