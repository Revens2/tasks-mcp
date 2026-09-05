# Opérations

## Santé / statut

```bash
# Services
systemctl status tasks-mcp.service tasks-gateway.service tasks-backup.timer
docker ps --filter name=radicale-tasks            # doit être "Up ... (healthy)"
curl -s http://127.0.0.1:8793/health              # {"status":"ok",...}

# Compteurs utiles
sqlite3 /srv/tasks/data/tasks.db 'SELECT count(*) FROM taches;'          # miroir
sqlite3 /srv/tasks/data/tasks.db 'SELECT count(*) FROM journal;'         # journal
sqlite3 /srv/tasks/data/tasks.db 'SELECT ts,action,source,uid,titre FROM journal ORDER BY id DESC LIMIT 10;'

# Logs (aucun secret n'y figure)
journalctl -u tasks-mcp.service -f
journalctl -u tasks-gateway.service -f
docker logs --tail 50 radicale-tasks
```

## Sauvegarde

Automatique : `tasks-backup.timer` chaque jour à 03:10 (heure du VPS), archives dans
`/srv/tasks/backups/` (0700 root), rétention 14.

```bash
# Manuel
sudo /usr/local/bin/tasks_backup.sh
```

Contenu : collections Radicale (sans les caches runtime), base SQLite (mode backup
sqlite, cohérente même en écriture), config + empreintes users, état OAuth.
**Jamais** de secret en clair (le fichier `users` ne contient que des bcrypt).

## Restauration

```bash
# Vérifier qu'une archive est restaurable (rien n'est modifié)
sudo /usr/local/bin/tasks_restore.sh --verifier  /srv/tasks/backups/tasks-<ts>.tar.gz

# Restauration réelle (arrête brièvement tasks-mcp/gateway/radicale)
sudo /usr/local/bin/tasks_restore.sh --restaurer /srv/tasks/backups/tasks-<ts>.tar.gz
```

Restauration d'**une tâche** précise : dans `data/tasks.db`, la table `versions`
garde les snapshots ICS antérieurs (`SELECT * FROM versions WHERE uid='…'`) ; pour
restaurer un ancien contenu, re-PUT le snapshot choisi dans Radicale avec le bon href
ou déplace depuis la Corbeille via `tasks_move`.

## Rotation du jeton MCP (clients CLI)

```bash
sudo bash /srv/tasks/scripts/rotation-jeton-tasks.sh   # nouveau jeton écrit sans être affiché
sudo bash /srv/tasks/scripts/afficher-secret.sh token   # le lire soi-même, puis MAJ les clients
```

## Mot de passe CalDAV (choisi par toi, jamais affiché par l'assistant)

```bash
# Mot de passe interactif (invite sans écho) :
sudo bash /srv/tasks/scripts/creer-compte-caldav.sh

# Mot de passe courant (bootstrap automatique) :
sudo bash /srv/tasks/scripts/afficher-secret.sh caldav
```

La rotation met à jour l'empreinte bcrypt (Radicale) **et** la valeur pour tasks-mcp
dans `secrets/tasks.env`, puis redémarre les services. L'iPhone doit alors être mis à
jour avec le nouveau mot de passe (Réglages → Rappels → Comptes → Tasks VPS).

## PKI (CA privée CalDAV)

```bash
# Régénérer uniquement le certificat feuille (ex. après changement de SAN) :
sudo bash /srv/tasks/scripts/init-pki.sh --force-leaf
sudo systemctl reload nginx

# La CA (tasks-ca.pem) reste identique ; si elle expire/change, la réinstaller sur l'iPhone.
```

Phase 2 (accès public ChatGPT) : passer le vhost CalDAV en Let's Encrypt duckdns
(pattern vaultwarden) puis réinstaller un certificat public — pas de CA privée à
maintenir côté iPhone.

## Mise à jour / reboot

Les services survivent au reboot (`enable`). Après redémarrage du VPS :

```bash
docker start radicale-tasks    # si le conteneur n'est pas en restart=always — vérifier compose
systemctl status tasks-mcp tasks-gateway tasks-backup.timer
```
