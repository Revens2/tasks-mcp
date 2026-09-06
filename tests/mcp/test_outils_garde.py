"""Tests unitaires du garde-fou « tâches terminées » (outils MCP)."""

from __future__ import annotations

from tasks_mcp.model import Tache
from tasks_mcp.outils import _garde_terminee


def _tache(**kwargs) -> Tache:
    defaut = dict(
        uid="u", list="Inbox", href="/juliann/Inbox/u.ics", etag="e",
        title="Faire X", completed=False,
    )
    defaut.update(kwargs)
    return Tache(**defaut)


def test_refus_si_terminee_par_defaut():
    t = _tache(completed=True, title="Terminée")
    refus = _garde_terminee(t.uid, t, inclure_terminee=False)
    assert refus is not None
    assert refus["erreur"] == "tache_terminee"
    assert refus["uid"] == "u"
    assert refus["message"]
    assert refus["tache_actuelle"]["title"] == "Terminée"
    assert refus["tache_actuelle"]["completed"] is True


def test_permis_si_inclure_terminee():
    t = _tache(completed=True)
    assert _garde_terminee(t.uid, t, inclure_terminee=True) is None


def test_permis_si_tache_active():
    t = _tache(completed=False)
    assert _garde_terminee(t.uid, t, inclure_terminee=False) is None
