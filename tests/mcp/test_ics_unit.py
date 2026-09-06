"""Tests unitaires de la couche iCalendar (aucun serveur requis)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tasks_mcp import ics
from tasks_mcp.temps import PARIS


def _texte_depuis(contenu: bytes) -> str:
    return contenu.decode("utf-8")


def test_creation_et_relecture_unicode_emoji():
    texte = ics.creer_ics(
        "Acheter câble SATA 🧵",
        notes="Avec les accents français : déjà, à côté, où, crème.",
        due=datetime(2026, 9, 6, 18, 0, tzinfo=PARIS),
    )
    brut = _texte_depuis(texte)
    assert "Acheter câble SATA 🧵" in brut
    taches = ics.taches_depuis_ics(brut, "Inbox", "/juliann/Inbox/x.ics", "etag-1")
    assert len(taches) == 1
    tache = taches[0]
    assert tache.title == "Acheter câble SATA 🧵"
    assert "accent" in (tache.notes or "")
    assert tache.uid
    assert tache.due is not None
    assert tache.due.astimezone(timezone.utc).hour == 16  # 18h Paris == 16h UTC (été)


def test_echange_preserve_proprietes_inconnues():
    original = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Apple Inc.//iOS 18//EN\r\n"
        "BEGIN:VTODO\r\nUID:abc-123\r\nDTSTAMP:20260905T100000Z\r\n"
        "SUMMARY:Test\r\nDESCRIPTION:Notes\r\n"
        "X-APPLE-SORT-ORDER:7\r\nX-CUSTOM-QUELCONQUE:valeur\r\n"
        "CATEGORIES:Perso,Urgent\r\nEND:VTODO\r\n"
        "BEGIN:VTIMEZONE\r\nTZID:Europe/Paris\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0200\r\n"
        "TZOFFSETTO:+0100\r\nDTSTART:19701025T030000\r\nRRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\n"
        "END:STANDARD\r\nEND:VTIMEZONE\r\nEND:VCALENDAR\r\n"
    )
    nouveau = ics.patcher_ics(original.encode(), title="Test modifié")
    brut = _texte_depuis(nouveau)
    assert "X-APPLE-SORT-ORDER:7" in brut
    assert "X-CUSTOM-QUELCONQUE:valeur" in brut
    assert "CATEGORIES:Perso,Urgent" in brut
    assert "TZID:Europe/Paris" in brut
    assert "Test modifié" in brut
    # la date DUE n'est pas touchée car absente
    assert "DUE" not in brut


def test_completer_et_rouvrir():
    original = ics.creer_ics("À terminer", due=datetime(2026, 9, 6, tzinfo=PARIS))
    complete = ics.patcher_ics(original, completer=True)
    brut = _texte_depuis(complete)
    assert "STATUS:COMPLETED" in brut
    assert "COMPLETED:" in brut
    assert "PERCENT-COMPLETE:100" in brut
    rouverte = ics.patcher_ics(complete, rouvrir=True)
    brut2 = _texte_depuis(rouverte)
    assert "STATUS:NEEDS-ACTION" in brut2
    assert "COMPLETED:" not in brut2
    assert "PERCENT-COMPLETE" not in brut2


def test_refus_agregat_multi_vtodo():
    multi = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:a\r\nSUMMARY:A\r\nEND:VTODO\r\n"
        "BEGIN:VTODO\r\nUID:b\r\nSUMMARY:B\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
    )
    with pytest.raises(ics.ErreurICS):
        ics.patcher_ics(multi.encode(), title="X")


def test_date_utc_z():
    texte = ics.creer_ics("Fuseaux", due=datetime(2026, 12, 6, 9, 0, tzinfo=PARIS))
    brut = _texte_depuis(texte)
    # 09h Paris en hiver = 08h UTC -> suffixe Z
    assert "DUE:20261206T080000Z" in brut
