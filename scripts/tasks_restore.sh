#!/usr/bin/env bash
# Restauration d'une sauvegarde tasks (root).
#   --verifier <archive> : décompacte dans un répertoire temporaire et valide
#                          (collections présentes, base sqlite intègre) SANS rien toucher.
#   --restaurer <archive> : restaure Radicale + base SQLite (services arrêtés le temps
#                           de la copie atomique).
# Usage : sudo bash /srv/tasks/scripts/tasks_restore.sh --verifier|--restaurer <archive>
set -euo pipefail
RACINE="/srv/tasks"

MODE="${1:-}"; ARCHIVE="${2:-}"
if [ -z "$MODE" ] || [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "Usage: $0 --verifier|--restaurer <archive.tar.gz>" >&2
    exit 2
fi

TMP="$(mktemp -d /tmp/tasks-restore.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP"

# Validation structurelle.
[ -d "$TMP/collections" ] || { echo "ÉCHEC : collections absentes" >&2; exit 1; }
N_COLL="$(find "$TMP/collections" -name '*.ics' | wc -l)"
echo "Validation : $N_COLL item(s) CalDAV dans l'archive."
if [ -f "$TMP/mcp/tasks.db" ]; then
    sqlite3 "$TMP/mcp/tasks.db" "PRAGMA integrity_check;" | grep -q ok \
        || { echo "ÉCHEC : base sqlite corrompue" >&2; exit 1; }
    N_JRN="$(sqlite3 "$TMP/mcp/tasks.db" 'SELECT count(*) FROM journal;' 2>/dev/null || echo 0)"
    echo "Validation : base sqlite OK ($N_JRN ligne(s) de journal)."
fi

if [ "$MODE" = "--verifier" ]; then
    echo "OK : l'archive est restaurable (mode vérification, rien n'a été modifié)."
    exit 0
fi

# Restauration réelle.
systemctl stop tasks-mcp.service tasks-gateway.service 2>/dev/null || true
docker stop radicale-tasks >/dev/null 2>&1 || true

rm -rf "$RACINE/radicale/data"
mkdir -p "$RACINE/radicale/data"
cp -a "$TMP/collections/." "$RACINE/radicale/data/"
if [ -d "$TMP/config" ] && [ -f "$TMP/config/config" ]; then
    cp -a "$TMP/config/config" "$RACINE/radicale/config/config"
    if [ -f "$TMP/config/users" ]; then
        cp -a "$TMP/config/users" "$RACINE/radicale/config/users"
    fi
fi
if [ -f "$TMP/mcp/tasks.db" ]; then
    install -o tasks-app -g tasks-app -m 600 "$TMP/mcp/tasks.db" "$RACINE/data/tasks.db"
fi
if [ -d "$TMP/mcp/oauth" ]; then
    rm -rf "$RACINE/data/oauth"
    cp -a "$TMP/mcp/oauth" "$RACINE/data/oauth"
    chown -R tasks-app:tasks-app "$RACINE/data/oauth"
fi

source "$RACINE/scripts/lib/common.sh"
assurer_permissions
docker start radicale-tasks >/dev/null
systemctl start tasks-mcp.service tasks-gateway.service
echo "OK : restauration terminée."
