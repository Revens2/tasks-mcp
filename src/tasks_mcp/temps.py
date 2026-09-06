"""Aides temporelles : fuseau Europe/Paris, ISO, flottants traités comme Paris."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def maintenant_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    dt = dt or maintenant_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def vers_paris(dt: datetime) -> datetime:
    """Convertit un datetime aware vers Europe/Paris (un flottant est lu comme Paris)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=PARIS)
    return dt.astimezone(PARIS)


def iso_affichage(dt: datetime | None) -> str | None:
    """ISO local Europe/Paris avec décalage, ex. 2026-09-06T18:00:00+02:00."""
    if dt is None:
        return None
    return vers_paris(dt).isoformat(timespec="seconds")


def parser_dt(valeur: str | None) -> datetime | None:
    """Parse une date/heure ISO (offset requis ou implicite = Europe/Paris)."""
    if valeur is None or not str(valeur).strip():
        return None
    brut = str(valeur).strip()
    if brut.endswith("Z"):
        brut = brut[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(brut)
    except ValueError:
        return None
    return vers_paris(dt)


def date_paris(dt: datetime | None) -> date | None:
    if dt is None:
        return None
    return vers_paris(dt).date()


def aujourdhui_paris() -> date:
    return datetime.now(PARIS).date()


def debut_jour_paris(jour: date) -> datetime:
    return datetime.combine(jour, time.min, tzinfo=PARIS)
