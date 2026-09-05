"""Persistance locale tasks-mcp : miroir léger + journal d'audit (SQLite).

- `taches`   : miroir des items Radicaux (uid, liste, etag, champs normalisés, ics brut)
               utilisé pour détecter les changements externes et servir l'historique.
- `journal`  : journal append-only (qui a changé quoi, quand, depuis quelle source).
- `versions` : snapshots ICS bruts (avant/après) pour les actions destructrices,
               permettant une restauration au-delà de la rétention de la Corbeille.
Aucun secret n'y est écrit (les contenus de tâches ne sont pas des secrets).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from . import temps
from .model import Tache

_verrou = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS taches (
    uid TEXT PRIMARY KEY,
    liste TEXT NOT NULL,
    href TEXT NOT NULL,
    etag TEXT,
    titre TEXT NOT NULL DEFAULT '',
    notes TEXT,
    etat TEXT NOT NULL DEFAULT 'needs-action',
    terminee INTEGER NOT NULL DEFAULT 0,
    due TEXT,
    debut TEXT,
    priorite INTEGER,
    termine_a TEXT,
    cree TEXT,
    maj TEXT,
    categories TEXT,
    ics_brut TEXT,
    trashed INTEGER NOT NULL DEFAULT 0,
    vu_le TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_taches_liste ON taches(liste);
CREATE INDEX IF NOT EXISTS idx_taches_uid ON taches(uid);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    acteur TEXT,
    action TEXT NOT NULL,
    uid TEXT,
    liste TEXT,
    titre TEXT,
    champ TEXT,
    ancien TEXT,
    nouveau TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_ts ON journal(ts);
CREATE INDEX IF NOT EXISTS idx_journal_uid ON journal(uid);

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    uid TEXT NOT NULL,
    action TEXT NOT NULL,
    ics TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_uid ON versions(uid);
"""


def _json(valeur: object) -> str:
    return json.dumps(valeur, ensure_ascii=False, default=str)


def _depuis_json(texte: str | None) -> object:
    if not texte:
        return None
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        return None


class Magasin:
    def __init__(self, chemin: str | Path):
        self._chemin = Path(chemin)
        self._connexion: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._connexion is None:
            self._chemin.parent.mkdir(parents=True, exist_ok=True)
            self._connexion = sqlite3.connect(self._chemin, check_same_thread=False)
            self._connexion.row_factory = sqlite3.Row
            self._connexion.execute("PRAGMA journal_mode=WAL")
            self._connexion.executescript(_SCHEMA)
        return self._connexion

    def fermer(self) -> None:
        if self._connexion is not None:
            self._connexion.close()
            self._connexion = None

    # --- miroir -------------------------------------------------------------------------

    def upsert_tache(self, tache: Tache, ics_brut: str | None) -> None:
        with _verrou:
            self._db().execute(
                """
                INSERT INTO taches (uid, liste, href, etag, titre, notes, etat, terminee,
                                    due, debut, priorite, termine_a, cree, maj, categories,
                                    ics_brut, trashed, vu_le)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    liste=excluded.liste, href=excluded.href, etag=excluded.etag,
                    titre=excluded.titre, notes=excluded.notes, etat=excluded.etat,
                    terminee=excluded.terminee, due=excluded.due, debut=excluded.debut,
                    priorite=excluded.priorite, termine_a=excluded.termine_a,
                    cree=excluded.cree, maj=excluded.maj, categories=excluded.categories,
                    ics_brut=excluded.ics_brut, trashed=excluded.trashed, vu_le=excluded.vu_le
                """,
                (
                    tache.uid,
                    tache.list,
                    tache.href,
                    tache.etag,
                    tache.title or "",
                    tache.notes,
                    tache.status,
                    int(tache.completed),
                    temps.iso_utc(tache.due) if tache.due else None,
                    temps.iso_utc(tache.start) if tache.start else None,
                    tache.priority,
                    temps.iso_utc(tache.completed_at) if tache.completed_at else None,
                    temps.iso_utc(tache.created) if tache.created else None,
                    temps.iso_utc(tache.updated) if tache.updated else None,
                    json.dumps(tache.categories, ensure_ascii=False),
                    ics_brut,
                    int(tache.trashed),
                    temps.iso_utc(),
                ),
            )
            self._db().commit()

    def retirer_tache(self, uid: str) -> Tache | None:
        """Retire par uid (suppression définitive)."""
        with _verrou:
            ancienne = self.trouver_uid(uid)
            if ancienne is not None:
                self._db().execute("DELETE FROM taches WHERE uid=?", (uid,))
                self._db().commit()
            return ancienne

    def retirer_href(self, href: str) -> Tache | None:
        """Retire par href (un item déplacé conserve son uid mais change de href)."""
        with _verrou:
            curseur = self._db().execute("SELECT * FROM taches WHERE href=?", (href,))
            ligne = curseur.fetchone()
            self._db().execute("DELETE FROM taches WHERE href=?", (href,))
            self._db().commit()
            return _ligne_tache(ligne)

    def trouver_uid(self, uid: str) -> Tache | None:
        curseur = self._db().execute(
            "SELECT * FROM taches WHERE uid=?",
            (uid,),
        )
        return _ligne_tache(curseur.fetchone())

    def liste(self) -> list[Tache]:
        curseur = self._db().execute("SELECT * FROM taches")
        return [_ligne_tache(l) for l in curseur.fetchall() if l]

    def ics_de(self, uid: str) -> str | None:
        curseur = self._db().execute("SELECT ics_brut FROM taches WHERE uid=?", (uid,))
        ligne = curseur.fetchone()
        return ligne[0] if ligne else None

    # --- journal ------------------------------------------------------------------------

    def journaliser(
        self,
        action: str,
        *,
        source: str,
        acteur: str | None = None,
        uid: str | None = None,
        liste: str | None = None,
        titre: str | None = None,
        champ: str | None = None,
        ancien: object = None,
        nouveau: object = None,
        detail: dict | None = None,
    ) -> None:
        with _verrou:
            self._db().execute(
                """
                INSERT INTO journal (ts, source, acteur, action, uid, liste, titre,
                                     champ, ancien, nouveau, detail)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    temps.iso_utc(),
                    source,
                    acteur,
                    action,
                    uid,
                    liste,
                    titre,
                    champ,
                    None if ancien is None else _json(ancien),
                    None if nouveau is None else _json(nouveau),
                    None if detail is None else _json(detail),
                ),
            )
            self._db().commit()

    def journal_depuis(self, depuis: str | None = None, limite: int = 200) -> list[dict]:
        requete = "SELECT * FROM journal"
        parametres: list[object] = []
        if depuis:
            requete += " WHERE ts >= ?"
            parametres.append(depuis)
        requete += " ORDER BY id DESC LIMIT ?"
        parametres.append(limite)
        curseur = self._db().execute(requete, parametres)
        return [_ligne_journal(l) for l in curseur.fetchall()]

    def journal_uid(self, uid: str, limite: int = 100) -> list[dict]:
        curseur = self._db().execute(
            "SELECT * FROM journal WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, limite)
        )
        return [_ligne_journal(l) for l in curseur.fetchall()]

    # --- versions (snapshots ICS) ----------------------------------------------------------

    def versionner(self, uid: str, action: str, ics: str) -> None:
        with _verrou:
            self._db().execute(
                "INSERT INTO versions (ts, uid, action, ics) VALUES (?,?,?,?)",
                (temps.iso_utc(), uid, action, ics),
            )
            self._db().commit()

    def version_avant(self, uid: str) -> str | None:
        curseur = self._db().execute(
            "SELECT ics FROM versions WHERE uid=? ORDER BY id DESC LIMIT 1", (uid,)
        )
        ligne = curseur.fetchone()
        return ligne[0] if ligne else None


def _ligne_tache(ligne: sqlite3.Row | tuple | None) -> Tache | None:
    if ligne is None:
        return None

    def _g(cle: str):
        try:
            return ligne[cle]
        except (IndexError, KeyError):
            return None

    categories = _depuis_json(_g("categories"))
    return Tache(
        uid=_g("uid") or "",
        list=_g("liste") or "",
        href=_g("href") or "",
        etag=_g("etag"),
        title=_g("titre") or "",
        notes=_g("notes"),
        status=_g("etat") or "needs-action",
        completed=bool(_g("terminee")),
        completed_at=_par_depuis(_g("termine_a")),
        priority=_g("priorite"),
        due=_par_depuis(_g("due")),
        start=_par_depuis(_g("debut")),
        created=_par_depuis(_g("cree")),
        updated=_par_depuis(_g("maj")),
        categories=list(categories) if isinstance(categories, list) else [],
        trashed=bool(_g("trashed")),
    )


def _par_depuis(iso: str | None):
    if not iso:
        return None
    try:
        from datetime import datetime, timezone

        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _ligne_journal(ligne: sqlite3.Row) -> dict:
    def _g(cle: str):
        try:
            return ligne[cle]
        except (IndexError, KeyError):
            return None

    return {
        "id": _g("id"),
        "ts": _g("ts"),
        "source": _g("source"),
        "acteur": _g("acteur"),
        "action": _g("action"),
        "uid": _g("uid"),
        "liste": _g("liste"),
        "titre": _g("titre"),
        "champ": _g("champ"),
        "ancien": _depuis_json(_g("ancien")),
        "nouveau": _depuis_json(_g("nouveau")),
        "detail": _depuis_json(_g("detail")),
    }
