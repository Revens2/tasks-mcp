#!/usr/bin/env bash
# Préparation idempotente de l'arborescence /srv/tasks (root).
# Usage : sudo bash /srv/tasks/scripts/setup-dirs.sh
set -euo pipefail
RACINE="/srv/tasks"
source "$RACINE/scripts/lib/common.sh"

mkdir -p "$RACINE"/{radicale/{config,data},data,secrets,backups,scripts,pki,pki/caldav,src,deploy/{systemd,nginx},tests,docs}
assurer_utilisateurs
assurer_permissions
echo "OK : arborescence, utilisateurs et permissions en place."
