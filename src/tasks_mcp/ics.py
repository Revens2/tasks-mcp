"""Couche iCalendar : lecture, création et modification chirurgicale de VTODO.

Principe : ne jamais réécrire brutalement un objet. La modification se fait au niveau
propriété (icalendar) sur le composant VTODO ; les propriétés inconnues (X-APPLE-*,
catégories, rappels...) et les composants annexes (VTIMEZONE) sont conservés.
Toute écriture de date se fait en UTC (suffixe Z) : iOS l'affiche dans le fuseau de
l'appareil sans dépendre d'un VTIMEZONE généré par nos soins.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from icalendar import Calendar

from . import temps
from .model import Tache

OMIS = object()  # sentinelle : champ absent = ne pas toucher

_CLE_PROPRIETE = {
    "title": "SUMMARY",
    "notes": "DESCRIPTION",
    "due": "DUE",
    "start": "DTSTART",
    "priority": "PRIORITY",
    "status": "STATUS",
    "percent_complete": "PERCENT-COMPLETE",
    "completed_at": "COMPLETED",
    "created": "CREATED",
    "updated": "LAST-MODIFIED",
    "uid": "UID",
    "categories": "CATEGORIES",
}


class ErreurICS(RuntimeError):
    pass


def _texte(valeur: Any) -> str | None:
    """Normalise une valeur iCalendar en str (vText, bytes, liste...)."""
    if valeur is None:
        return None
    if isinstance(valeur, list):
        if not valeur:
            return None
        return _texte(valeur[0])
    if isinstance(valeur, bytes):
        return valeur.decode("utf-8", errors="replace")
    return str(valeur)


def _texte_liste(valeur: Any) -> list[str]:
    if valeur is None:
        return []
    if isinstance(valeur, list):
        return [v for v in (_texte(item) for item in valeur) if v]
    texte = _texte(valeur)
    if not texte:
        return []
    return [v.strip() for v in texte.replace("\\,", "\u0000").split(",") if v.strip()]


def _datetime(valeur: Any) -> datetime | None:
    if valeur is None:
        return None
    if isinstance(valeur, list):
        valeur = valeur[0] if valeur else None
    if valeur is None:
        return None
    brut = valeur.dt if hasattr(valeur, "dt") else valeur
    if isinstance(brut, datetime):
        return brut
    if isinstance(brut, date):
        return datetime.combine(brut, datetime.min.time(), tzinfo=temps.PARIS)
    return None


def parser_ics(contenu: bytes | str) -> Calendar:
    try:
        return Calendar.from_ical(contenu)
    except Exception as exc:  # ValueError selon versions
        raise ErreurICS(f"iCalendar illisible : {exc}") from exc


def taches_depuis_ics(
    contenu: bytes | str, nom_liste: str, href: str, etag: str | None, trashed: bool = False
) -> list[Tache]:
    calendrier = parser_ics(contenu)
    resultat: list[Tache] = []
    for composant in calendrier.walk("VTODO"):
        resultat.append(
            tache_depuis_composant(composant, nom_liste, href, etag, trashed=trashed)
        )
    return resultat


def tache_depuis_composant(
    composant: Any, nom_liste: str, href: str, etag: str | None, trashed: bool = False
) -> Tache:
    statut = (_texte(composant.get("STATUS")) or "needs-action").lower()
    termine = statut == "completed" or bool(composant.get("COMPLETED"))
    categories = _texte_liste(composant.get("CATEGORIES"))

    def _n(nom: str) -> datetime | None:
        return _datetime(composant.get(nom))

    titre = _texte(composant.get("SUMMARY")) or ""
    return Tache(
        uid=_texte(composant.get("UID")) or "",
        list=nom_liste,
        href=href,
        etag=etag,
        title=titre,
        notes=_texte(composant.get("DESCRIPTION")),
        status=statut,
        completed=termine,
        completed_at=_n("COMPLETED"),
        priority=_entier(composant.get("PRIORITY")),
        due=_n("DUE"),
        start=_n("DTSTART"),
        created=_n("CREATED") or _n("DTSTAMP"),
        updated=_n("LAST-MODIFIED") or _n("DTSTAMP"),
        categories=categories,
        percent_complete=_entier(composant.get("PERCENT-COMPLETE")),
        trashed=trashed,
    )


def _entier(valeur: Any) -> int | None:
    texte = _texte(valeur)
    if not texte:
        return None
    try:
        return int(texte)
    except ValueError:
        return None


def _enlever(composant: Any, nom: str) -> None:
    try:
        del composant[nom]
    except (KeyError, ValueError):
        pass


def _remplacer(composant: Any, nom: str, valeur: Any) -> None:
    """Remplace une propriété (add() pour la conversion de type iCalendar)."""
    if valeur is OMIS:
        return
    _enlever(composant, nom)
    if valeur is None:
        return
    composant.add(nom, valeur)


def creer_ics(
    titre: str,
    uid: str | None = None,
    notes: str | None = None,
    due: datetime | None = None,
    start: datetime | None = None,
    priority: int | None = None,
    status: str = "needs-action",
) -> bytes:
    """Nouveau VCALENDAR/VTODO (date-heure en UTC/Z)."""
    calendrier = Calendar()
    calendrier.add("VERSION", "2.0")
    calendrier.add("PRODID", "-//tasks-mcp//CalDAV//FR")
    todo = _nouveau_composant(uid)
    todo.add("SUMMARY", titre)
    if notes is not None:
        todo.add("DESCRIPTION", notes)
    if due is not None:
        todo.add("DUE", _utc(due))
    if start is not None:
        todo.add("DTSTART", _utc(start))
    if priority is not None:
        todo.add("PRIORITY", int(priority))
    if status and status.lower() != "needs-action":
        todo.add("STATUS", status.upper())
    calendrier.add_component(todo)
    return calendrier.to_ical()


def _nouveau_composant(uid: str | None = None) -> Any:
    from icalendar import Todo

    todo = Todo()
    todo.add("UID", uid or uuid.uuid4().hex)
    maintenant = temps.maintenant_utc()
    todo.add("DTSTAMP", _utc(maintenant))
    todo.add("CREATED", _utc(maintenant))
    todo.add("LAST-MODIFIED", _utc(maintenant))
    return todo


def _utc(dt: datetime) -> datetime:
    """Date/heure aware UTC (naive -> Europe/Paris puis UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=temps.PARIS)
    return dt.astimezone(timezone.utc)


def patcher_ics(
    contenu: bytes | str,
    *,
    title: Any = OMIS,
    notes: Any = OMIS,
    due: Any = OMIS,
    start: Any = OMIS,
    priority: Any = OMIS,
    status: Any = OMIS,
    completer: bool = False,
    rouvrir: bool = False,
) -> bytes:
    """Modifie un VTODO en conservant les propriétés inconnues.

    - `completer=True`  : STATUS=COMPLETED + COMPLETED (maintenant) + PERCENT-COMPLETE=100.
    - `rouvrir=True`    : retire COMPLETED/PERCENT-COMPLETE, STATUS=NEEDS-ACTION.
    - `title/notes/...` : OMIS (défaut) = ne pas toucher ; None = effacer la propriété.
    Lève ErreurICS si le fichier contient plusieurs VTODO (refus d'écraser un agrégat).
    """
    calendrier = parser_ics(contenu)
    todos = calendrier.walk("VTODO")
    if len(todos) != 1:
        raise ErreurICS(
            f"{len(todos)} composants VTODO dans la ressource : modification refusée"
        )
    todo = todos[0]

    if completer and rouvrir:
        raise ValueError("completer et rouvrir sont mutuellement exclusifs")

    _remplacer(todo, "SUMMARY", title if title is not OMIS else OMIS)
    _remplacer(todo, "DESCRIPTION", notes if notes is not OMIS else OMIS)
    if due is not OMIS:
        _remplacer(todo, "DUE", None if due is None else _utc(due))
    if start is not OMIS:
        _remplacer(todo, "DTSTART", None if start is None else _utc(start))
    if priority is not OMIS:
        _remplacer(todo, "PRIORITY", None if priority is None else int(priority))
    if status is not OMIS and not completer and not rouvrir:
        _remplacer(todo, "STATUS", None if status is None else str(status).upper())

    if completer:
        _remplacer(todo, "STATUS", "COMPLETED")
        _remplacer(todo, "COMPLETED", _utc(temps.maintenant_utc()))
        _remplacer(todo, "PERCENT-COMPLETE", 100)
    elif rouvrir:
        _remplacer(todo, "STATUS", "NEEDS-ACTION")
        _enlever(todo, "COMPLETED")
        _enlever(todo, "PERCENT-COMPLETE")

    _remplacer(todo, "LAST-MODIFIED", _utc(temps.maintenant_utc()))
    return calendrier.to_ical()
