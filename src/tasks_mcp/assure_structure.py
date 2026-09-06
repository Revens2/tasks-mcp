"""Bootstrap CalDAV : principal + collections initiales (Inbox, Corbeille).

Usage :  TASKS_* dans l'environnement, puis
         python -m tasks_mcp.assure_structure [--env /srv/tasks/secrets/tasks.env]
Lit la config dans l'environnement (ou un fichier env via --env). Idempotent.
"""

from __future__ import annotations

import argparse

from .caldav import CalDAV
from .config import Config


def main() -> None:
    analyseur = argparse.ArgumentParser()
    analyseur.add_argument("--env", help="fichier d'environnement (KEY=VALUE)")
    args = analyseur.parse_args()
    if args.env:
        from .envfile import appliquer

        appliquer(args.env)
    config = Config.charger()
    caldav = CalDAV(config.caldav_url, config.caldav_user, config.caldav_password)
    try:
        principal = caldav.principal()
        print(f"principal : {principal}")
        collections = {c.nom for c in caldav.collections()}
        for nom in ("Inbox", config.trash_list):
            if nom in collections:
                print(f"collection présente : {nom}")
                continue
            href = caldav.creer_collection(nom)
            print(f"collection créée : {nom} ({href})")
    finally:
        caldav.fermer()


if __name__ == "__main__":
    main()
