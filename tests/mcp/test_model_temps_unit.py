"""Tests unitaires du modèle et des aides temporelles."""

from __future__ import annotations

from datetime import datetime

from tasks_mcp.model import Tache, cle_tri, correspond
from tasks_mcp.temps import PARIS, iso_affichage, parser_dt, vers_paris


def _tache(**kwargs) -> Tache:
    defaut = dict(
        uid="u", list="Inbox", href="/juliann/Inbox/u.ics", etag="e",
        title="", due=None, priority=None,
    )
    defaut.update(kwargs)
    return Tache(**defaut)


def test_tri_echeance_puis_priorite():
    base = datetime(2026, 9, 7, 8, 0, tzinfo=PARIS)
    t1 = _tache(uid="1", due=base, priority=None)
    t2 = _tache(uid="2", due=base, priority=1)
    t3 = _tache(uid="3", due=None, priority=1)
    trie = sorted([t1, t2, t3], key=cle_tri)
    assert [t.uid for t in trie] == ["2", "1", "3"]  # même échéance : priorité avant


def test_recherche_insensible_accents():
    t = _tache(uid="1", title="Créer une présentation", notes="pour jeudi")
    assert correspond(t, "creer", ("title", "notes"))
    assert correspond(t, "PRÉSENTATION", ("title", "notes"))
    assert correspond(t, "jeudi", ("notes",))
    assert not correspond(t, "vendredi", ("title", "notes"))


def test_parser_dt_flottant_paris():
    dt = parser_dt("2026-09-06T18:00:00")
    assert dt is not None
    assert dt.utcoffset().total_seconds() in (7200, 3600)  # fuseau Europe/Paris
    assert iso_affichage(dt).startswith("2026-09-06T18:00:00+02:00")


def test_vers_paris_hiver():
    dt = vers_paris(datetime(2026, 1, 15, 12, 0, tzinfo=PARIS))
    assert iso_affichage(dt).endswith("+01:00")
