#!/usr/bin/env bash
# Sauvegarde quotidienne de la stack tasks (root).
# Contenu : collections Radicale (data), base SQLite tasks-mcp (miroir+journal),
# état OAuth de la passerelle (clients/consentements), fichiers de config Radicale.
# Sortie : /srv/tasks/backups/tasks-<horodatage>.tar.gz  (0700, propriété root).
# Restauration : scripts/tasks_restore.sh <archive>  (testée en environnement temporaire).
set -euo pipefail
RACINE="/srv/tasks"
STAGING="$(mktemp -d /tmp/tasks-backup.XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT

HORODATAGE="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$RACINE/backups"
umask 077

# Copie staging (source cohérente sans arrêter Radicale : les écritures sont atomiques
# au niveau fichier ; la copie rsync + tar est le meilleur compromis sans downtime).
# .Radicale.cache / .Radicale.lock sont des artefacts runtime : exclus (Radicale les
# reconstruit ; les restaurer produirait des caches périmés).
rsync -a --delete \
    --exclude '.Radicale.cache' --exclude '.Radicale.lock' \
    "$RACINE/radicale/data/" "$STAGING/collections/" 2>/dev/null \
    || {
        mkdir -p "$STAGING/collections"
        cp -a "$RACINE/radicale/data/." "$STAGING/collections/"
        find "$STAGING/collections" -name '.Radicale.cache' -o -name '.Radicale.lock' | xargs -r rm -rf
    }

mkdir -p "$STAGING/config"
cp -a "$RACINE/radicale/config/config" "$STAGING/config/config"
if [ -f "$RACINE/radicale/config/users" ]; then
    cp -a "$RACINE/radicale/config/users" "$STAGING/config/users"  # empreintes bcrypt uniquement
fi

if [ -d "$RACINE/data" ]; then
    mkdir -p "$STAGING/mcp"
    # La base SQLite est copiée via le mode backup de sqlite (cohérente même en écriture).
    if [ -f "$RACINE/data/tasks.db" ]; then
        sqlite3 "$RACINE/data/tasks.db" ".backup '$STAGING/mcp/tasks.db'"
    fi
    if [ -d "$RACINE/data/oauth" ]; then
        cp -a "$RACINE/data/oauth" "$STAGING/mcp/oauth"
    fi
fi

ARCHIVE="$RACINE/backups/tasks-${HORODATAGE}.tar.gz"
tar -czf "$ARCHIVE" -C "$STAGING" .
chmod 600 "$ARCHIVE"

# Rétention : 14 archives.
ls -1t "$RACINE"/backups/tasks-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f

echo "OK : $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
