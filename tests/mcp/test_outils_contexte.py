"""Tests du lien `tasks_create` ↔ contexte ChatGPT récent.

Vérifie au niveau de l'outil réel (enregistré sur une instance FastMCP) que :
- un contexte récent et valide ajoute le bloc « Conversation ChatGPT » aux notes ;
- sans contexte (ou contexte expiré) la tâche est créée normalement, notes telles quelles ;
- les notes existantes sont préservées ; pas de duplication du bloc ;
- aucune défaillance possible à cause du contexte (registre absent, erreur interne).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

from tasks_mcp.contexte import RegistreContexte, contexte_depuis_payload
from tasks_mcp.model import Tache
from tasks_mcp.outils import enregistrer

UUID = "67d5b8f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f"
URL = f"https://chatgpt.com/c/{UUID}"


class _ServiceStub:
    """Service minimal : capture les notes réellement passées à creer()."""

    def __init__(self):
        self.config = SimpleNamespace(contexte_ttl_s=300)
        self.contexte_registre = RegistreContexte()
        self.magasin = None  # requis par enregistrer() mais inutilisé ici
        self.notes_capturees: str | None = None
        self.dernier_titre: str | None = None

    def creer(self, title, liste="Inbox", notes=None, due=None, start=None, priority=None):
        self.dernier_titre = title
        self.notes_capturees = notes
        return Tache(
            uid="u-e2e", list=liste, href=f"/juliann/Inbox/u-e2e.ics", etag="e",
            title=title, notes=notes,
        )


def _service_avec_contexte(vu_il_y_a_s: float | None = None, titre: str = "Ma conversation") -> _ServiceStub:
    service = _ServiceStub()
    contexte = contexte_depuis_payload({"url": URL, "title": titre})
    maintenant = datetime.now(timezone.utc)
    service.contexte_registre.enregistrer(
        contexte,
        maintenant=maintenant - timedelta(seconds=vu_il_y_a_s) if vu_il_y_a_s is not None else maintenant,
    )
    return service


def _appeler(mcp: FastMCP, nom: str, arguments: dict) -> dict:
    """Appelle un outil et ramène son dict de résultat.

    Le SDK enveloppe le dict renvoyé par l'outil dans du contenu texte JSON :
    on le re-décode pour travailler sur l'objet (comme le ferait un client).
    """
    import json

    brut = asyncio.run(mcp.call_tool(nom, arguments))
    if isinstance(brut, dict):
        return brut
    texte = "".join(getattr(morceau, "text", "") for morceau in brut)
    return json.loads(texte)


def _creer(service) -> dict:
    mcp = FastMCP("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    return _appeler(mcp, "tasks_create", {"title": "Tâche de test", "notes": "notes"})


def test_contexte_recent_ajoute_le_bloc_aux_notes():
    service = _service_avec_contexte(vu_il_y_a_s=10)
    resultat = _creer(service)
    assert service.notes_capturees == (
        "notes\n\n---\nConversation ChatGPT :\nMa conversation\n" + URL
    )
    assert resultat["tache"]["notes"] == service.notes_capturees


def test_sans_contexte_tache_creée_normalement():
    service = _ServiceStub()  # registre vide
    resultat = _creer(service)
    assert service.notes_capturees == "notes"
    assert resultat["tache"]["title"] == "Tâche de test"


def test_contexte_expire_non_ajoute():
    service = _service_avec_contexte(vu_il_y_a_s=301)  # TTL 300 s dépassé
    _creer(service)
    assert service.notes_capturees == "notes"


def test_sans_notes_le_bloc_est_les_notes():
    service = _service_avec_contexte(vu_il_y_a_s=5)
    mcp = FastMCP("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    _appeler(mcp, "tasks_create", {"title": "Tâche"})
    assert service.notes_capturees == f"Conversation ChatGPT :\nMa conversation\n{URL}"


def test_notes_existantes_preservees():
    service = _service_avec_contexte(vu_il_y_a_s=5, titre="T")
    notes = "Ligne 1\nLigne 2"
    mcp = FastMCP("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    _appeler(mcp, "tasks_create", {"title": "Tâche", "notes": notes})
    assert service.notes_capturees == f"{notes}\n\n---\nConversation ChatGPT :\nT\n{URL}"


def test_pas_de_duplication_du_bloc():
    service = _service_avec_contexte(vu_il_y_a_s=5)
    notes_deja = f"déjà noté\n\n---\nConversation ChatGPT :\n{URL}"
    mcp = FastMCP("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    _appeler(mcp, "tasks_create", {"title": "Tâche", "notes": notes_deja})
    assert service.notes_capturees == notes_deja


def test_registre_absent_ne_fait_jamais_echouer():
    service = _ServiceStub()
    service.contexte_registre = None  # scénario dégradé
    resultat = _creer(service)
    assert service.notes_capturees == "notes"
    assert resultat["tache"]["title"] == "Tâche de test"


def test_registre_en_erreur_ne_fait_jamais_echouer():
    service = _ServiceStub()

    class _Casse:
        def dernier_valide(self, **kwargs):
            raise RuntimeError("panne")

    service.contexte_registre = _Casse()
    resultat = _creer(service)
    assert resultat["tache"]["title"] == "Tâche de test"
