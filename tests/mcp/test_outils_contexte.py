"""Tests du lien `tasks_create` ↔ contexte ChatGPT récent.

Vérifie au niveau de l'outil réel (enregistré sur une instance MCPServer) que :
- avec un contexte récent et valide, les notes finales commencent par l'URL de
  la conversation (PREMIÈRE ligne) puis décrivent Source/Compte/Conversation ;
- sans contexte (ou contexte expiré) la tâche est créée normalement, notes
  telles quelles — `tasks_create` ne doit jamais être bloqué par le contexte ;
- les notes originales sont préservées ; pas de duplication de l'URL ni de
  l'ancien bloc « Conversation ChatGPT : » ;
- aucune défaillance possible à cause du contexte (registre absent, erreur).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from mcp.server.mcpserver import MCPServer

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


def _service_avec_contexte(
    vu_il_y_a_s: float | None = None,
    titre: str = "Ma conversation",
    compte: str | None = None,
) -> _ServiceStub:
    service = _ServiceStub()
    payload = {"url": URL, "title": titre}
    if compte:
        payload["account_label"] = compte
    contexte = contexte_depuis_payload(payload)
    maintenant = datetime.now(timezone.utc)
    service.contexte_registre.enregistrer(
        contexte,
        maintenant=maintenant - timedelta(seconds=vu_il_y_a_s) if vu_il_y_a_s is not None else maintenant,
    )
    return service


def _appeler(mcp: MCPServer, nom: str, arguments: dict) -> dict:
    """Appelle un outil et ramène son dict de résultat.

    Le SDK enveloppe le dict renvoyé par l'outil dans du contenu texte JSON :
    on le re-décode pour travailler sur l'objet (comme le ferait un client).
    """
    import json

    brut = asyncio.run(mcp.call_tool(nom, arguments))
    if isinstance(brut, dict):
        return brut
    # SDK v2 : CallToolResult (plus une liste de morceaux) ; le dict renvoye
    # par l'outil est enveloppe en contenu texte JSON.
    contenu = getattr(brut, "content", brut)
    texte = "".join(getattr(morceau, "text", "") for morceau in contenu)
    return json.loads(texte)


def _creer(service, notes="notes") -> dict:
    mcp = MCPServer("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    return _appeler(mcp, "tasks_create", {"title": "Tâche de test", "notes": notes})


def test_contexte_recent_url_en_premiere_ligne() -> None:
    service = _service_avec_contexte(vu_il_y_a_s=10)
    resultat = _creer(service)
    assert service.notes_capturees is not None
    assert service.notes_capturees.startswith("https://chatgpt.com/c/")
    assert service.notes_capturees == (
        f"{URL}\n\nnotes\n\n---\nSource : ChatGPT\nConversation : Ma conversation"
    )
    assert resultat["tache"]["notes"] == service.notes_capturees


def test_contexte_avec_compte_affiche_ligne_compte() -> None:
    service = _service_avec_contexte(vu_il_y_a_s=5, titre="Câble iPhone", compte="ChatGPT principal")
    _creer(service, notes="Acheter un câble USB-C.")
    assert service.notes_capturees == (
        f"{URL}\n\nAcheter un câble USB-C.\n\n"
        "---\nSource : ChatGPT\nCompte : ChatGPT principal\nConversation : Câble iPhone"
    )


def test_sans_contexte_tache_creée_normalement() -> None:
    service = _ServiceStub()  # registre vide
    resultat = _creer(service)
    assert service.notes_capturees == "notes"
    assert resultat["tache"]["title"] == "Tâche de test"


def test_contexte_expire_non_ajoute() -> None:
    service = _service_avec_contexte(vu_il_y_a_s=301)  # TTL 300 s dépassé
    _creer(service)
    assert service.notes_capturees == "notes"


def test_sans_notes_le_texte_est_url_plus_pied() -> None:
    service = _service_avec_contexte(vu_il_y_a_s=5)
    mcp = MCPServer("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    _appeler(mcp, "tasks_create", {"title": "Tâche"})
    assert service.notes_capturees == f"{URL}\n\n---\nSource : ChatGPT\nConversation : Ma conversation"


def test_notes_existantes_preservees() -> None:
    service = _service_avec_contexte(vu_il_y_a_s=5, titre="T")
    notes = "Ligne 1\nLigne 2"
    mcp = MCPServer("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    _appeler(mcp, "tasks_create", {"title": "Tâche", "notes": notes})
    assert service.notes_capturees == f"{URL}\n\nLigne 1\nLigne 2\n\n---\nSource : ChatGPT\nConversation : T"


def test_pas_de_duplication_du_bloc() -> None:
    """Des notes contenant déjà l'ancien bloc (avec la même URL) → une seule
    URL (en tête), aucun reliquat de l'ancien format."""
    service = _service_avec_contexte(vu_il_y_a_s=5, titre="Nouveau titre")
    notes_deja = f"déjà noté\n\n---\nConversation ChatGPT :\n{URL}"
    mcp = MCPServer("test-contexte", log_level="ERROR")
    enregistrer(mcp, service)
    _appeler(mcp, "tasks_create", {"title": "Tâche", "notes": notes_deja})
    assert service.notes_capturees.count(URL) == 1
    assert "Conversation ChatGPT :" not in (service.notes_capturees or "")
    assert service.notes_capturees == (
        f"{URL}\n\ndéjà noté\n\n---\nSource : ChatGPT\nConversation : Nouveau titre"
    )


def test_registre_absent_ne_fait_jamais_echouer() -> None:
    service = _ServiceStub()
    service.contexte_registre = None  # scénario dégradé
    resultat = _creer(service)
    assert service.notes_capturees == "notes"
    assert resultat["tache"]["title"] == "Tâche de test"


def test_registre_en_erreur_ne_fait_jamais_echouer() -> None:
    service = _ServiceStub()

    class _Casse:
        def dernier_valide(self, **kwargs):
            raise RuntimeError("panne")

    service.contexte_registre = _Casse()
    resultat = _creer(service)
    assert resultat["tache"]["title"] == "Tâche de test"
