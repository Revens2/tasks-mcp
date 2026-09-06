#!/usr/bin/env bash
# Installation / mise à jour des unités systemd + sites nginx de la stack tasks (root).
# Idempotent : relançable après une mise à jour du dépôt.
# Usage : sudo bash /srv/tasks/deploy/install.sh
set -euo pipefail
RACINE="/srv/tasks"

echo "== unités systemd =="
install -m 644 "$RACINE/deploy/systemd/tasks-mcp.service" /etc/systemd/system/tasks-mcp.service
install -m 644 "$RACINE/deploy/systemd/tasks-gateway.service" /etc/systemd/system/tasks-gateway.service
install -m 644 "$RACINE/deploy/systemd/tasks-backup.service" /etc/systemd/system/tasks-backup.service
install -m 644 "$RACINE/deploy/systemd/tasks-backup.timer" /etc/systemd/system/tasks-backup.timer
systemctl daemon-reload

echo "== scripts (liens) =="
install -m 755 "$RACINE/scripts/tasks_backup.sh" /usr/local/bin/tasks_backup.sh
install -m 755 "$RACINE/scripts/tasks_restore.sh" /usr/local/bin/tasks_restore.sh

echo "== nginx =="
install -m 644 "$RACINE/deploy/nginx/caldav-tasks.conf" /etc/nginx/sites-available/caldav-tasks
install -m 644 "$RACINE/deploy/nginx/tasks-mcp.conf" /etc/nginx/sites-available/tasks-mcp
# Vhost public ChatGPT (phase 2, duckdns + Let's Encrypt) : installé lui aussi
# depuis le dépôt pour rester synchronisé avec les locations /context/chatgpt.
install -m 644 "$RACINE/deploy/nginx/tasks-mcp-public.conf" /etc/nginx/sites-available/tasks-mcp-public.conf
ln -sfn /etc/nginx/sites-available/caldav-tasks /etc/nginx/sites-enabled/caldav-tasks
ln -sfn /etc/nginx/sites-available/tasks-mcp /etc/nginx/sites-enabled/tasks-mcp
ln -sfn /etc/nginx/sites-available/tasks-mcp-public.conf /etc/nginx/sites-enabled/tasks-mcp-public.conf
nginx -t

echo "== services =="
systemctl enable --now tasks-backup.timer
systemctl enable --now tasks-gateway.service tasks-mcp.service
systemctl reload nginx
echo "OK : stack tasks installée (units + nginx + timer)."
