"""Tests d'intégration service tasks_mcp <-> Radicale (à exécuter SUR le VPS).

Prérequis : Radicale démarré et compte bootstrapé ; le fichier d'environnement des
secrets est passé par TASKS_ENV_FILE=/srv/tasks/secrets/tasks.env (racine requise
pour le lire). Marqués `integration` (ignorés ailleurs).
"""

from __future__ import annotations

import os
import time

import pytest

from tasks_mcp.caldav import CalDAV
from tasks_mcp.config import Config
from tasks_mcp.envfile import appliquer
from tasks_mcp.model import Tache
from tasks_mcp.service import ConflitModification, Introuvable, Service
from tasks_mcp.store import Magasin
from tasks_mcp.temps import PARIS, maintenant_utc, parser_dt
from datetime import datetime, timedelta

pytestmark = pytest.mark.integration

FICHIER_ENV = os.environ.get("TASKS_ENV_FILE")


@pytest.fixture()
def service(tmp_path):
    if not FICHIER_ENV or not os.path.exists(FICHIER_ENV):
        pytest.skip("TASKS_ENV_FILE absent : tests d'intégration ignorés")
    valeurs = appliquer(FICHIER_ENV)
    config = Config(
        caldav_url=valeurs.get("TASKS_CALDAV_URL", "http://127.0.0.1:5232"),
        caldav_user=valeurs["TASKS_CALDAV_USER"],
        caldav_password=valeurs["TASKS_CALDAV_PASSWORD"],
        data_dir=tmp_path,
        trash_list="ZZCorbeille",
    )
    caldav = CalDAV(config.caldav_url, config.caldav_user, config.caldav_password)
    magasin = Magasin(config.fichier_db)
    service = Service(caldav, magasin, config)
    # listes de test propres (nom unique par test)
    suffixe = str(int(time.time()))
    liste = f"ZZTest-{suffixe}"
    hrefs: list[str] = []
    for nom in (liste, "ZZCorbeille"):
        try:
            hrefs.append(service.caldav.creer_collection(nom))
        except Exception:
            pass
    yield service, liste
    # nettoyage : tâches puis collections de test
    try:
        for tache in service.toutes(vue="toutes"):
            if tache.list in (liste, "ZZCorbeille"):
                service.supprimer(tache.uid, permanent=True)
        for href in hrefs:
            try:
                caldav._requete("DELETE", href)
            except Exception:
                pass
    except Exception:
        pass
    caldav.fermer()
    magasin.fermer()


def test_cycle_complet(service):
    svc, liste = service
    cree = svc.creer("TEST MCP IPHONE", liste=liste, notes="créé par test")
    assert cree.uid
    assert cree.list == liste

    # lecture par uid
    retrouvee = svc.trouver(cree.uid)
    assert retrouvee.title == "TEST MCP IPHONE"

    # modification (renommage)
    modifiee, _ = svc.modifier(cree.uid, title="TEST MCP MODIFIÉ")
    assert modifiee.title == "TEST MCP MODIFIÉ"
    assert svc.trouver(cree.uid).title == "TEST MCP MODIFIÉ"

    # échéance
    due = datetime.now(PARIS) + timedelta(days=1)
    avec_due, _ = svc.modifier(cree.uid, due=due.replace(microsecond=0))
    assert avec_due.due is not None

    # terminer / rouvrir
    terminee = svc.completer(cree.uid)
    assert terminee.completed is True
    rouverte = svc.rouvrir(cree.uid)
    assert rouverte.completed is False

    # déplacement puis soft delete
    corbeille = svc.deplacer(cree.uid, "ZZCorbeille")
    assert corbeille.trashed
    resultat = svc.supprimer(cree.uid)  # déjà en corbeille -> idempotent
    assert resultat["etat"] == "deja-corbeille"
    restauree = svc.deplacer(cree.uid, liste)
    assert restauree.trashed is False
    suppression = svc.supprimer(cree.uid)
    assert suppression["etat"] == "corbeille"
    retrouvee_corbeille = svc.trouver(cree.uid)
    assert retrouvee_corbeille.trashed
    # restauration depuis la corbeille
    svc.deplacer(cree.uid, liste)
    # suppression définitive
    svc.supprimer(cree.uid, permanent=True)
    with pytest.raises(Introuvable):
        svc.trouver(cree.uid)


def test_conflit_etag(service):
    svc, liste = service
    cree = svc.creer("Conflit", liste=liste)
    # changement externe simulé : on réécrit la tâche avec un etag périmé
    fraiche = svc.trouver(cree.uid)
    contenu, etag = svc.caldav.lire(fraiche.href)
    from tasks_mcp import ics

    nouveau = ics.patcher_ics(contenu, title="Changement iPhone")
    etag2 = svc.caldav.ecrire(fraiche.href, nouveau, etag_attendu=etag)
    assert etag2
    # le service doit refuser la modif basée sur l'ancien etag
    with pytest.raises(ConflitModification):
        svc.modifier(cree.uid, etag_attendu=etag, title="Écrasement")
    actuelle = svc.trouver(cree.uid)
    assert actuelle.title == "Changement iPhone"
    svc.supprimer(cree.uid, permanent=True)


def test_detection_changement_externe(service):
    svc, liste = service
    cree = svc.creer("Externe", liste=liste)
    avant = svc.trouver(cree.uid)
    contenu, etag = svc.caldav.lire(avant.href)
    from tasks_mcp import ics

    autre = ics.patcher_ics(contenu, title="Externe renommé")
    svc.caldav.ecrire(avant.href, autre, etag_attendu=etag)
    svc.synchroniser(force=True)
    apres = svc.trouver(cree.uid)
    assert apres.title == "Externe renommé"
    evenements = svc.magasin.journal_uid(cree.uid)
    assert any(e["source"] == "caldav_external" and e["champ"] == "title" for e in evenements)
    svc.supprimer(cree.uid, permanent=True)


def test_recemment_change(service):
    svc, liste = service
    svc.creer("Récente", liste=liste, notes="à retrouver")
    svc.synchroniser(force=True)
    depuis = maintenant_utc() - timedelta(hours=1)
    evenements = svc.magasin.journal_depuis(depuis.isoformat().replace("+00:00", "Z"))
    assert any(e["action"] == "cree" and e["titre"] == "Récente" for e in evenements)
