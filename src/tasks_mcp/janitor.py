"""Nettoyage de la Corbeille au-delà de la rétention (purge définitive journalisée).

Usage :  TASKS_* dans l'environnement, puis
         python -m tasks_mcp.janitor [--env /srv/tasks/secrets/tasks.env]
Timer système conseillé : quotidien (voir deploy/systemd/).
"""

from __future__ import annotations

import argparse

from .caldav import CalDAV
from .config import Config
from .service import Service
from .store import Magasin


def main() -> None:
    analyseur = argparse.ArgumentParser()
    analyseur.add_argument("--env", help="fichier d'environnement (KEY=VALUE)")
    args = analyseur.parse_args()
    if args.env:
        from .envfile import appliquer

        appliquer(args.env)
    config = Config.charger()
    caldav = CalDAV(config.caldav_url, config.caldav_user, config.caldav_password)
    magasin = Magasin(config.fichier_db)
    service = Service(caldav, magasin, config)
    try:
        resultat = service.purger_corbeille()
        print(f"corbeille purgée : {len(resultat['purges'])} tâche(s)")
    finally:
        caldav.fermer()
        magasin.fermer()


if __name__ == "__main__":
    main()
