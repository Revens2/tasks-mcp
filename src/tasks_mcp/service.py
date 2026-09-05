"""Couche métier : synchronisation miroir/journal, CRUD conditionnel, vues.

Concurrence (iPhone <-> ChatGPT) :
- lecture systématiquement précédée d'une synchro légère (frais < TTL) ;
- toute écriture utilise If-Match sur l'ETag lu à l'instant ;
- sur 412 : relecture + ConflitModification portant la tâche actuelle. Jamais de
  last-write-wins silencieux : si la demande porte sur des champs qui n'ont pas bougé,
  la valeur est rejouée une fois (merge sûr) ; sinon l'outil remonte un conflit
  explicite à ChatGPT.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import acteur, temps
from .caldav import CalDAV, Collection, ErreurCalDAV
from .ics import OMIS, ErreurICS, creer_ics, patcher_ics, taches_depuis_ics
from .model import Tache
from .store import Magasin

TTL_SYNC_S = 15
SOURCE_EXTERNE = "caldav_external"
SOURCE_MCP = "tasks_mcp"


class ConflitModification(RuntimeError):
    """La tâche a changé côté serveur depuis la lecture de référence."""

    def __init__(self, tache_actuelle: Tache, message: str = ""):
        super().__init__(message or "la tâche a changé depuis la lecture")
        self.tache_actuelle = tache_actuelle


class Introuvable(RuntimeError):
    pass


class ListeInconnue(RuntimeError):
    def __init__(self, liste: str, disponibles: list[str]):
        super().__init__(f"liste inconnue : {liste}")
        self.liste = liste
        self.disponibles = disponibles


class Service:
    def __init__(self, caldav: CalDAV, magasin: Magasin, config):
        self.caldav = caldav
        self.magasin = magasin
        self.config = config
        self._derniere_sync = 0.0

    # ------------------------------------------------------------------ synchronisation

    def synchroniser(self, force: bool = False) -> None:
        with self.config.verrou:
            maintenant = time.monotonic()
            if not force and (maintenant - self._derniere_sync) < TTL_SYNC_S:
                return
            self._sync_complet()
            self._derniere_sync = maintenant

    def _sync_complet(self) -> None:
        magasin = self.magasin
        try:
            collections = self.caldav.collections()
        except ErreurCalDAV:
            return  # Radicale indisponible : le miroir reste tel quel

        lignes = {t.href: t for t in magasin.liste()}
        vus: set[str] = set()
        for collection in collections:
            try:
                etiquettes = self.caldav.etiquettes(collection)
            except ErreurCalDAV:
                continue
            trashed = collection.nom == self.config.trash_list
            for href, etag in etiquettes.items():
                vus.add(href)
                ligne = lignes.get(href)
                if ligne is not None and ligne.etag == etag:
                    continue  # rien de changé
                try:
                    contenu, _ = self.caldav.lire(href)
                except ErreurCalDAV:
                    continue
                try:
                    taches = taches_depuis_ics(contenu, collection.nom, href, etag, trashed=trashed)
                except ErreurICS:
                    continue
                if not taches:
                    continue  # ressource sans VTODO : ignorée, jamais détruite
                tache = taches[0]
                contenu_texte = contenu.decode("utf-8", "replace")
                if ligne is None:
                    existante = magasin.trouver_uid(tache.uid)
                    if existante is not None and existante.href != href:
                        # déplacement externe (iPhone) : même uid, nouveau href
                        magasin.upsert_tache(tache, contenu_texte)
                        magasin.journaliser(
                            "deplace",
                            source=SOURCE_EXTERNE,
                            uid=tache.uid,
                            liste=tache.list,
                            titre=tache.title,
                            champ="liste",
                            ancien=existante.list,
                            nouveau=tache.list,
                            detail={"avant": existante.href, "apres": href},
                        )
                    else:
                        magasin.upsert_tache(tache, contenu_texte)
                        self._journal_diff(None, tache)
                else:
                    avant = ligne
                    magasin.upsert_tache(tache, contenu_texte)
                    self._journal_diff(avant, tache)

        # suppressions côté serveur (liste supprimée, item purgé depuis un autre client).
        # On retire par href (jamais par uid) : un item déplacé sur l'iPhone change de
        # href en gardant son uid — le retrait par uid supprimerait la nouvelle ligne.
        for href, ligne in lignes.items():
            if href in vus:
                continue
            magasin.journaliser(
                "supprime",
                source=SOURCE_EXTERNE,
                uid=ligne.uid,
                liste=ligne.list,
                titre=ligne.title,
                detail={"href": href, "raison": "absent du serveur"},
            )
            magasin.retirer_href(href)

    def _journal_diff(self, avant: Tache | None, apres: Tache) -> None:
        magasin = self.magasin
        if avant is None:
            magasin.journaliser(
                "cree",
                source=SOURCE_EXTERNE,
                uid=apres.uid,
                liste=apres.list,
                titre=apres.title,
                detail={"href": apres.href},
            )
            return
        if avant.href != apres.href:
            magasin.journaliser(
                "deplace",
                source=SOURCE_EXTERNE,
                uid=apres.uid,
                liste=apres.list,
                titre=apres.title,
                champ="liste",
                ancien=avant.list,
                nouveau=apres.list,
                detail={"avant": avant.href, "apres": apres.href},
            )
            return
        if avant.completed != apres.completed:
            magasin.journaliser(
                "termine" if apres.completed else "rouvert",
                source=SOURCE_EXTERNE,
                uid=apres.uid,
                liste=apres.list,
                titre=apres.title,
                champ="completed",
                ancien=bool(avant.completed),
                nouveau=bool(apres.completed),
            )
        for champ, ancien, nouveau in avant.diff_champs(apres):
            if champ == "status" and avant.completed != apres.completed:
                continue  # déjà journalisé via complete/reopen
            magasin.journaliser(
                "modifie",
                source=SOURCE_EXTERNE,
                uid=apres.uid,
                liste=apres.list,
                titre=apres.title,
                champ=champ,
                ancien=ancien,
                nouveau=nouveau,
            )

    # ------------------------------------------------------------------ découverte

    def liste_collections(self) -> list[str]:
        try:
            return [c.nom for c in self.caldav.collections()]
        except ErreurCalDAV:
            return []

    def _collection(self, nom: str) -> Collection:
        for collection in self.caldav.collections():
            if collection.nom == nom:
                return collection
        raise ListeInconnue(nom, self.liste_collections())

    # ------------------------------------------------------------------ lecture

    def _rafraichir_uid(self, uid: str) -> Tache | None:
        tache = self.magasin.trouver_uid(uid)
        if tache is None:
            return None
        try:
            contenu, etag = self.caldav.lire(tache.href)
        except ErreurCalDAV:
            return tache
        try:
            taches = taches_depuis_ics(
                contenu, tache.list, tache.href, etag,
                trashed=tache.list == self.config.trash_list,
            )
        except ErreurICS:
            return tache
        if not taches:
            return tache
        fraiche = taches[0]
        self.magasin.upsert_tache(fraiche, contenu.decode("utf-8", "replace"))
        return fraiche

    def toutes(self, liste: str | None = None, vue: str = "actives") -> list[Tache]:
        """vue : actives (défaut, sans Corbeille) | corbeille | toutes."""
        self.synchroniser()
        taches = self.magasin.liste()
        if liste is not None:
            taches = [t for t in taches if t.list == liste]
        if vue == "actives":
            taches = [t for t in taches if not t.trashed]
        elif vue == "corbeille":
            taches = [t for t in taches if t.trashed]
        return taches

    def trouver(self, uid: str) -> Tache:
        self.synchroniser()
        tache = self.magasin.trouver_uid(uid)
        if tache is None:
            tache = self._chercher_uid(uid)
        if tache is None:
            raise Introuvable(f"tâche inconnue : {uid}")
        return self._rafraichir_uid(uid) or tache

    def _chercher_uid(self, uid: str) -> Tache | None:
        for collection in self.caldav.collections():
            try:
                etiquettes = self.caldav.etiquettes(collection)
            except ErreurCalDAV:
                continue
            for href in etiquettes:
                try:
                    contenu, _ = self.caldav.lire(href)
                except ErreurCalDAV:
                    continue
                try:
                    taches = taches_depuis_ics(contenu, collection.nom, href, None)
                except ErreurICS:
                    continue
                for tache in taches:
                    if tache.uid == uid:
                        self.magasin.upsert_tache(
                            tache, contenu.decode("utf-8", "replace")
                        )
                        return tache
        return None

    # ------------------------------------------------------------------ écritures

    def creer(
        self,
        titre: str,
        liste: str = "Inbox",
        notes: str | None = None,
        due: datetime | None = None,
        start: datetime | None = None,
        priority: int | None = None,
    ) -> Tache:
        titre = (titre or "").strip()
        if not titre:
            raise ValueError("titre vide")
        with self.config.verrou:
            collection = self._collection(liste)
            contenu = creer_ics(titre, notes=notes, due=due, start=start, priority=priority)
            from icalendar import Calendar

            uid = str(Calendar.from_ical(contenu).walk("VTODO")[0].get("UID"))
            href = collection.url + quote(uid, safe="") + ".ics"
            etag = self.caldav.ecrire(href, contenu)
            contenu_texte = contenu.decode("utf-8", "replace")
            tache = taches_depuis_ics(contenu_texte, liste, href, etag)[0]
            self.magasin.upsert_tache(tache, contenu_texte)
            self.magasin.journaliser(
                "cree",
                source=SOURCE_MCP,
                acteur=acteur.acteur_actuel(),
                uid=tache.uid,
                liste=tache.list,
                titre=tache.title,
                detail={"href": href},
            )
            self.magasin.versionner(tache.uid, "cree", contenu_texte)
            return tache

    def modifier(
        self,
        uid: str,
        etag_attendu: str | None = None,
        **changements,
    ) -> tuple[Tache, list[dict]]:
        """Applique des changements atomiques (If-Match). Retourne (tache, diffs)."""
        with self.config.verrou:
            actuel = self.trouver(uid)
            try:
                contenu, etag_serveur = self.caldav.lire(actuel.href)
            except ErreurCalDAV as exc:
                raise ConflitModification(actuel, f"relecture impossible : {exc}") from exc

            if etag_attendu and etag_attendu != etag_serveur:
                raise ConflitModification(self._pars_fraiche(contenu, actuel, etag_serveur))

            def _o(cle: str):
                return changements[cle] if cle in changements else OMIS

            try:
                nouveau = patcher_ics(
                    contenu,
                    title=_o("title"),
                    notes=_o("notes"),
                    due=_o("due"),
                    start=_o("start"),
                    priority=_o("priority"),
                    status=_o("status"),
                    completer=bool(changements.get("completer", False)),
                    rouvrir=bool(changements.get("rouvrir", False)),
                )
            except (ValueError, ErreurICS) as exc:
                raise ValueError(str(exc)) from exc

            try:
                etag = self.caldav.ecrire(actuel.href, nouveau, etag_attendu=etag_serveur)
            except ErreurCalDAV as exc:
                if exc.statut == 412:
                    try:
                        contenu2, _ = self.caldav.lire(actuel.href)
                    except ErreurCalDAV:
                        raise ConflitModification(actuel)
                    raise ConflitModification(self._pars_fraiche(contenu2, actuel))
                raise

            texte = nouveau.decode("utf-8", "replace")
            apres = taches_depuis_ics(
                texte, actuel.list, actuel.href, etag,
                trashed=actuel.list == self.config.trash_list,
            )[0]
            diffs = self._journal_modification(actuel, apres, etag, texte)
            return apres, diffs

    def _pars_fraiche(self, contenu: bytes, actuel: Tache, etag: str | None = None) -> Tache:
        try:
            fraiche = taches_depuis_ics(
                contenu, actuel.list, actuel.href, etag,
                trashed=actuel.list == self.config.trash_list,
            )[0]
        except (ErreurICS, IndexError):
            return actuel
        return fraiche

    def _journal_modification(self, avant: Tache, apres: Tache, etag: str, texte: str) -> list[dict]:
        diffs: list[dict] = []
        if apres.completed != avant.completed:
            action = "termine" if apres.completed else "rouvert"
            diffs.append({"action": action, "champ": "completed",
                          "ancien": avant.completed, "nouveau": apres.completed})
        for champ, ancien, nouveau in avant.diff_champs(apres):
            if champ == "status" and avant.completed != apres.completed:
                continue
            diffs.append({"action": "modifie", "champ": champ, "ancien": ancien, "nouveau": nouveau})
        self.magasin.upsert_tache(apres, texte)
        self.magasin.versionner(apres.uid, "modifie", texte)
        for diff in diffs:
            self.magasin.journaliser(
                diff["action"],
                source=SOURCE_MCP,
                acteur=acteur.acteur_actuel(),
                uid=apres.uid,
                liste=apres.list,
                titre=apres.title,
                champ=diff["champ"],
                ancien=diff.get("ancien"),
                nouveau=diff.get("nouveau"),
                detail={"href": apres.href, "etag": etag},
            )
        return diffs

    # ------------------------------------------------------------------ conflit / erreur

    def completer(self, uid: str) -> Tache:
        tache, _ = self.modifier(uid, completer=True)
        return tache

    def rouvrir(self, uid: str) -> Tache:
        tache, _ = self.modifier(uid, rouvrir=True)
        return tache

    def deplacer(self, uid: str, liste: str) -> Tache:
        with self.config.verrou:
            actuel = self.trouver(uid)
            destination = self._collection(liste)
            if destination.nom == actuel.list:
                return actuel
            try:
                contenu, etag = self.caldav.lire(actuel.href)
            except ErreurCalDAV as exc:
                raise ConflitModification(actuel, str(exc)) from exc
            href_destination = destination.url + quote(actuel.uid, safe="") + ".ics"
            try:
                etag2 = self.caldav.deplacer(actuel.href, href_destination, etag_attendu=etag)
            except ErreurCalDAV as exc:
                if exc.statut == 412:
                    raise ConflitModification(self._rafraichir_uid(uid) or actuel)
                raise
            texte = contenu.decode("utf-8", "replace")
            trashed = destination.nom == self.config.trash_list
            apres = taches_depuis_ics(texte, destination.nom, href_destination, etag2, trashed=trashed)[0]
            self.magasin.upsert_tache(apres, texte)
            self.magasin.versionner(apres.uid, "deplace", texte)
            action = "corbeille" if trashed else ("restaure" if actuel.trashed else "deplace")
            self.magasin.journaliser(
                action,
                source=SOURCE_MCP,
                acteur=acteur.acteur_actuel(),
                uid=apres.uid,
                liste=apres.list,
                titre=apres.title,
                champ="liste",
                ancien=actuel.list,
                nouveau=apres.list,
                detail={"href": apres.href, "etag": etag2},
            )
            return apres

    def supprimer(self, uid: str, permanent: bool = False) -> dict:
        with self.config.verrou:
            actuel = self.trouver(uid)
            try:
                contenu, etag = self.caldav.lire(actuel.href)
            except ErreurCalDAV as exc:
                raise ConflitModification(actuel, str(exc)) from exc
            texte = contenu.decode("utf-8", "replace")
            if not permanent:
                if actuel.list == self.config.trash_list:
                    return {"uid": uid, "etat": "deja-corbeille", "liste": actuel.list}
                corbeille = self._collection(self.config.trash_list)
                href_corbeille = corbeille.url + quote(actuel.uid, safe="") + ".ics"
                try:
                    etag2 = self.caldav.deplacer(actuel.href, href_corbeille, etag_attendu=etag)
                except ErreurCalDAV as exc:
                    if exc.statut == 412:
                        raise ConflitModification(self._rafraichir_uid(uid) or actuel)
                    raise
                apres = taches_depuis_ics(
                    texte, self.config.trash_list, href_corbeille, etag2, trashed=True
                )[0]
                self.magasin.upsert_tache(apres, texte)
                self.magasin.versionner(apres.uid, "corbeille", texte)
                self.magasin.journaliser(
                    "corbeille",
                    source=SOURCE_MCP,
                    acteur=acteur.acteur_actuel(),
                    uid=apres.uid,
                    liste=apres.list,
                    titre=apres.title,
                    champ="liste",
                    ancien=actuel.list,
                    nouveau=self.config.trash_list,
                )
                return {
                    "uid": uid,
                    "etat": "corbeille",
                    "liste": self.config.trash_list,
                    "restauration": f"tasks_move(uid={uid!r}, liste={actuel.list!r})",
                }
            self.magasin.versionner(uid, "supprime", texte)
            try:
                self.caldav.supprimer(actuel.href, etag_attendu=etag)
            except ErreurCalDAV as exc:
                if exc.statut == 412:
                    raise ConflitModification(self._rafraichir_uid(uid) or actuel)
                raise
            self.magasin.journaliser(
                "supprime",
                source=SOURCE_MCP,
                acteur=acteur.acteur_actuel(),
                uid=uid,
                liste=actuel.list,
                titre=actuel.title,
                detail={"href": actuel.href, "permanent": True},
            )
            self.magasin.retirer_tache(uid)
            return {"uid": uid, "etat": "supprime"}

    # ------------------------------------------------------------------ corbeille / purge

    def purger_corbeille(self) -> dict:
        rétention = datetime.now(timezone.utc) - timedelta(
            days=max(0, int(getattr(self.config, "trash_retention_days", 30)))
        )
        purges: list[str] = []
        for tache in self.toutes(vue="corbeille"):
            reference = tache.updated or tache.created
            if reference is None or reference.astimezone(timezone.utc) > rétention:
                continue
            try:
                self.supprimer(tache.uid, permanent=True)
            except (Introuvable, ConflitModification, ErreurCalDAV):
                continue
            purges.append(tache.uid)
        return {"purges": purges}



