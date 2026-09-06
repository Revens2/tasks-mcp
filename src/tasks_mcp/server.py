"""Point d'entrée tasks-mcp (`python -m tasks_mcp.server`).

Écoute sur la boucle locale uniquement (port TASKS_UPSTREAM_PORT, défaut 8791) :
la passerelle tasks-gateway (8792, elle-même derrière nginx) est le seul point
d'entrée. Pas de couche d'auth ici : l'isolation systemd (IPAddressAllow=localhost)
et le fait que la passerelle soit l'unique client rendent l'ensemble sûr. L'acteur
(qui appelle) est propagé par l'en-tête interne x-tasks-mcp-acteur, posé par la
passerelle après authentification — jamais lu depuis l'extérieur.
"""

from __future__ import annotations

import logging
import os

import uvicorn

from . import outils
from .acteur import ActeurMiddleware
from .caldav import CalDAV
from .config import Config
from .service import Service
from .store import Magasin

journal = logging.getLogger("tasks_mcp")


def construire(config: Config | None = None) -> tuple[Service, object]:
    """Construit le service + l'application FastMCP (testable sans uvicorn)."""
    config = config or Config.charger()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    caldav = CalDAV(
        config.caldav_url,
        config.caldav_user,
        config.caldav_password,
        timeout=config.timeout_s,
    )
    magasin = Magasin(config.fichier_db)
    service = Service(caldav, magasin, config)

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        "tasks",
        instructions=(
            "Serveur de tâches/rappels connecté à votre compte Apple Rappels (CalDAV "
            "Radicale, liste principale Inbox). Conventions : échéances en Europe/Paris ; "
            "chaque tâche porte un uid et un etag ; toute modification est conditionnelle "
            "(If-Match) : si la réponse contient conflit:true, la tâche a changé sur "
            "l'iPhone entre-temps — relisez tache_actuelle et ré-appliquez. Suppression "
            "par défaut = Corbeille (restaurable via tasks_move). Journal d'audit : "
            "tasks_recently_changed / tasks_history."
        ),
    )
    outils.enregistrer(mcp, service)
    return service, mcp


def main() -> None:
    config = Config.charger()
    service, mcp = construire(config)
    try:
        service.purger_corbeille()
    except Exception as exc:  # noqa: BLE001 - la purge ne doit pas empêcher le démarrage
        journal.warning("purge de la corbeille impossible au démarrage : %s", exc)

    # streamable_http_app() construit l'application ASGI (sans argument dans ce SDK) ;
    # ActeurMiddleware l'enveloppe pour propager l'acteur depuis l'en-tête interne.
    application = ActeurMiddleware(mcp.streamable_http_app())
    uvicorn.run(
        application,
        host="127.0.0.1",
        port=config.upstream_port,
        log_level=os.environ.get("TASKS_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
