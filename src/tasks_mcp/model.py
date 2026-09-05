"""Modèle de tâche normalisé (indépendant d'iCalendar) et opérations de tri/filtre."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

from . import temps

# Champs exposés à ChatGPT (schéma stable).
CHAMPS = (
    "id",
    "uid",
    "etag",
    "href",
    "title",
    "notes",
    "list",
    "status",
    "completed",
    "completed_at",
    "priority",
    "due",
    "start",
    "created",
    "updated",
    "categories",
    "trashed",
    "percent_complete",
)

STATUTS_VALIDES = {"needs-action", "in-process", "completed", "cancelled"}


@dataclass(slots=True)
class Tache:
    uid: str
    list: str
    href: str
    etag: str | None
    title: str = ""
    notes: str | None = None
    status: str = "needs-action"
    completed: bool = False
    completed_at: datetime | None = None
    priority: int | None = None
    due: datetime | None = None
    start: datetime | None = None
    created: datetime | None = None
    updated: datetime | None = None
    categories: list[str] = field(default_factory=list)
    percent_complete: int | None = None
    trashed: bool = False

    def vers_json(self) -> dict:
        def _champ(nom: str) -> str:
            return nom

        return {
            "id": self.uid,
            "uid": self.uid,
            "etag": self.etag,
            "href": self.href,
            "title": self.title or "",
            "notes": self.notes,
            "list": self.list,
            "status": self.status,
            "completed": bool(self.completed),
            "completed_at": temps.iso_affichage(self.completed_at),
            "priority": self.priority,
            "due": temps.iso_affichage(self.due),
            "start": temps.iso_affichage(self.start),
            "created": temps.iso_affichage(self.created),
            "updated": temps.iso_affichage(self.updated),
            "categories": list(self.categories),
            "trashed": bool(self.trashed),
            "percent_complete": self.percent_complete,
        }

    def diff_champs(self, autre: "Tache") -> list[tuple[str, object, object]]:
        """Diff des champs utiles (pour le journal) : (champ, ancien, nouveau)."""
        resultat: list[tuple[str, object, object]] = []
        for champ in ("title", "notes", "status", "due", "start", "priority"):
            ancien = getattr(self, champ)
            nouveau = getattr(autre, champ)
            if ancien != nouveau:
                resultat.append((champ, _normaliser_diff(ancien), _normaliser_diff(nouveau)))
        if self.completed != autre.completed:
            resultat.append(
                ("completed", self.completed, autre.completed)
            )
        return resultat


def _normaliser_diff(valeur: object) -> object:
    if isinstance(valeur, datetime):
        return temps.iso_affichage(valeur)
    if isinstance(valeur, list):
        return list(valeur)
    return valeur


# --- moteur de recherche ----------------------------------------------------------------

def _normaliser(texte: str) -> str:
    """Casse + accents plats : NFKD puis retrait des marques combinantes (Mn)."""
    normalise = unicodedata.normalize("NFKD", texte).casefold()
    return "".join(c for c in normalise if not unicodedata.combining(c))


def correspond(tache: Tache, requete: str, champs: tuple[str, ...]) -> bool:
    """Correspondance insensible aux accents/casse sur les champs demandés."""
    motif = _normaliser(requete)
    if not motif:
        return True
    for champ in champs:
        valeur = getattr(tache, champ, None)
        if isinstance(valeur, str) and motif in _normaliser(valeur):
            return True
        if isinstance(valeur, list):
            if any(motif in _normaliser(str(v)) for v in valeur):
                return True
    return False


def cle_tri(tache: Tache) -> tuple:
    """Tri : échéance (null en dernier), priorité (null en dernier), titre."""
    due = tache.due or datetime.max.replace(tzinfo=temps.PARIS)
    priorite = tache.priority if tache.priority is not None else 99
    return (due, priorite, _normaliser(tache.title or ""))


def est_en_retard(tache: Tache, maintenant: datetime | None = None) -> bool:
    maintenant = maintenant or temps.maintenant_utc()
    return (
        not tache.completed
        and tache.due is not None
        and temps.vers_paris(tache.due) < temps.vers_paris(maintenant)
    )
