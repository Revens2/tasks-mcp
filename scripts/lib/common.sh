#!/usr/bin/env bash
# Helpers communs aux scripts de la stack tasks.
# Aucun secret ne doit transiter par argv (visible dans ps/historique) ni être affiché.
# Contrat : les valeurs secrètes passent par stdin.

set -euo pipefail

RACINE="/srv/tasks"
ENV_FICHIER="$RACINE/secrets/tasks.env"
USERS_RADICALE="$RACINE/radicale/config/users"

# --- utilisateurs système (idempotent, root) -----------------------------------------
assurer_utilisateurs() {
    # uid/gid 2999 : compte hôte aligné sur l'image tomsquest/docker-radicale.
    if ! getent group radicale >/dev/null 2>&1; then
        groupadd --gid 2999 radicale
    fi
    if ! getent passwd radicale >/dev/null 2>&1; then
        useradd --system --uid 2999 --gid 2999 --no-create-home \
            --shell /usr/sbin/nologin --home-dir /srv/tasks radicale
    fi
    if ! getent passwd tasks-app >/dev/null 2>&1; then
        useradd --system --no-create-home --shell /usr/sbin/nologin \
            --home-dir /srv/tasks tasks-app
    fi
}

# --- empreinte bcrypt (hash lu sur stdin, jamais en argv) ----------------------------
hacher_bcrypt_stdin() {
    # docker exec : stdin est relayé au python du conteneur (bcrypt inclus dans l'image).
    # rounds=10 : coût de vérification ~60 ms (rounds=12 => ~400 ms/requête CalDAV,
    # pénalisant pour iOS qui multiplie les requêtes). Endpoint privé NetBird + delay=1.
    docker exec -i radicale-tasks /venv/bin/python -c \
        'import sys, bcrypt
m = sys.stdin.buffer.read().strip()
if not m:
    raise SystemExit("mot de passe vide")
print(bcrypt.hashpw(m, bcrypt.gensalt(rounds=10)).decode())'
}

# --- écriture clé=valeur dans un fichier env (valeur sur stdin, jamais en argv) ------
env_set() {
    local fichier="$1" cle="$2"
    local valeur
    valeur="$(cat)"
    mkdir -p "$(dirname "$fichier")"
    touch "$fichier"
    python3 - "$fichier" "$cle" "$valeur" <<'PY'
import pathlib, sys
fichier, cle, valeur = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
# Une valeur avec espaces/metacaracteres doit etre entre guillemets pour rester
# lisible par `source`, par systemd EnvironmentFile et par envfile.py.
if any(c.isspace() or c in '"\'\\$`' for c in valeur) and not (
    len(valeur) >= 2 and valeur[0] == '"' and valeur[-1] == '"'
):
    valeur = '"' + valeur.replace('"', '\\"') + '"'
lignes = fichier.read_text(encoding="utf-8").splitlines()
trouve = False
for i, ligne in enumerate(lignes):
    if ligne.startswith(cle + "="):
        lignes[i] = f"{cle}={valeur}"
        trouve = True
        break
if not trouve:
    lignes.append(f"{cle}={valeur}")
fichier.write_text("\n".join(lignes) + "\n", encoding="utf-8")
PY
}

# --- lecture d'une valeur env (utilisée uniquement pour auto-tests non affichés) ------
env_get() {
    local fichier="$1" cle="$2"
    python3 - "$fichier" "$cle" <<'PY'
import pathlib, sys
fichier, cle = pathlib.Path(sys.argv[1]), sys.argv[2]
for ligne in fichier.read_text(encoding="utf-8").splitlines():
    if ligne.startswith(cle + "="):
        valeur = ligne.split("=", 1)[1]
        if len(valeur) >= 2 and valeur[0] == '"' and valeur[-1] == '"':
            valeur = valeur[1:-1].replace('\\"', '"')
        print(valeur, end="")
        break
PY
}

# --- permissions standard (root) ------------------------------------------------------
assurer_permissions() {
    chown -R radicale:radicale "$RACINE/radicale/data"
    # config/ est lisible par tous (755) pour que le dépôt git (user juliann) reste
    # utilisable ; seul le fichier users (empreintes bcrypt) reste 640 root:radicale.
    chown root:radicale "$RACINE/radicale/config"
    chmod 755 "$RACINE/radicale/config"
    chmod 644 "$RACINE/radicale/config/config"
    if [ -f "$USERS_RADICALE" ]; then
        chown root:radicale "$USERS_RADICALE"
        chmod 640 "$USERS_RADICALE"
    fi
    mkdir -p "$RACINE/data" "$RACINE/secrets" "$RACINE/backups"
    chown tasks-app:tasks-app "$RACINE/data" "$RACINE/secrets"
    chmod 700 "$RACINE/data" "$RACINE/secrets"
    # backups est ecrit par le timer root ; 700 root:root.
    chmod 700 "$RACINE/backups"
}
