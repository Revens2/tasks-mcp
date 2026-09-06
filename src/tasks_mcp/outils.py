"""Outils MCP tasks (schémas fortement typés).

Conventions de résultats (toujours du JSON sérialisable) :
- succès : {"taches": [...], "total": n} | {"tache": {...}} | {"resultat": ...} ;
- conflit : {"conflit": true, "message": ..., "tache_actuelle": {...}} ;
- erreur métier : {"erreur": "code", "message": ..., champs utiles}.
Les valeurs de dates renvoyées sont en Europe/Paris avec décalage (ex. +02:00).
"""

from __future__ import annotations

import json
from typing import Annotated, Optional

from pydantic import Field

from . import acteur, contexte, temps
from .caldav import ErreurCalDAV
from .model import Tache, cle_tri, correspond, est_en_retard
from .service import ConflitModification, Introuvable, ListeInconnue, Service

J = Field


def _json_tache(tache: Tache) -> dict:
    return tache.vers_json()


def _trier(taches: list[Tache]) -> list[Tache]:
    return sorted(taches, key=cle_tri)


def _actives(taches: list[Tache], include_completed: bool) -> list[Tache]:
    if include_completed:
        return taches
    return [t for t in taches if not t.completed]


def _liste_comme_json(taches: list[Tache]) -> dict:
    taches = _trier(taches)
    return {"taches": [_json_tache(t) for t in taches], "total": len(taches)}


def _depuis_parametres(since: str | None = None, hours: int | None = None) -> str | None:
    if since:
        dt = temps.parser_dt(since)
        if dt is None:
            return None
        return temps.iso_utc(dt)
    if hours:
        from datetime import datetime, timedelta, timezone

        return temps.iso_utc(
            datetime.now(timezone.utc) - timedelta(hours=max(0, hours))
        )
    return None


def _garde_terminee(uid: str, actuelle: Tache, inclure_terminee: bool) -> dict | None:
    """Politique V1 : ne pas modifier une tâche déjà terminée (rappel coché).

    Refus par défaut ; la tâche doit d'abord être rouverte (tasks_reopen) ou
    l'appelant doit explicitement passer `inclure_terminee=true` (ex. corriger
    une archive). Renvoie un dict d'erreur, ou None si la modification est permise.
    """
    if actuelle.completed and not inclure_terminee:
        return {
            "erreur": "tache_terminee",
            "message": (
                "tâche déjà terminée : modification refusée (évite de modifier un "
                "rappel coché, invisible côté iPhone). Rouvrez-la avec tasks_reopen "
                "ou passez inclure_terminee=true si la modification est voulue."
            ),
            "uid": uid,
            "tache_actuelle": _json_tache(actuelle),
        }
    return None


def _notes_avec_contexte_recent(service: Service, notes: str | None) -> str | None:
    """Ajoute le bloc « Conversation ChatGPT » si un contexte récent existe.

    Strictement best-effort : si le registre est absent, vide, expiré ou en
    erreur, les notes sont rendues telles quelles — `tasks_create` ne doit
    jamais échouer (ni même ralentir) à cause du contexte.
    """
    try:
        registre = getattr(service, "contexte_registre", None)
        if registre is None:
            return notes
        ttl = int(getattr(service.config, "contexte_ttl_s", 0) or 0)
        actuel = registre.dernier_valide(ttl_s=ttl if ttl > 0 else contexte.TTL_DEFAUT_S)
        if actuel is None:
            return notes
        return contexte.notes_avec_contexte(notes, actuel)
    except Exception:  # noqa: BLE001 - jamais bloquer la création de tâche
        return notes


def _erreur(exc: Exception) -> dict:
    if isinstance(exc, ConflitModification):
        return {
            "conflit": True,
            "message": str(exc),
            "conseil": "relisez la tache, ré-appliquez vos changements sur cette base",
            "tache_actuelle": _json_tache(exc.tache_actuelle),
        }
    if isinstance(exc, Introuvable):
        return {"erreur": "introuvable", "message": str(exc)}
    if isinstance(exc, ListeInconnue):
        return {
            "erreur": "liste_inconnue",
            "message": str(exc),
            "liste": exc.liste,
            "listes_disponibles": exc.disponibles,
        }
    if isinstance(exc, ErreurCalDAV):
        return {
            "erreur": "caldav",
            "message": str(exc),
            "radicale": "indisponible" if exc.statut is None else f"http {exc.statut}",
        }
    return {"erreur": "interne", "message": str(exc)}


def enregistrer(mcp, service: Service) -> None:
    magasin = service.magasin

    # ------------------------------------------------------------------ lecture

    @mcp.tool(name="tasks_list")
    def tasks_list(
        list: Annotated[Optional[str], J(description="Nom de la liste (ex. Inbox). Absent = toutes les listes actives.")] = None,
        include_completed: Annotated[
            bool, J(description="Inclure les tâches terminées.")] = False,
        include_trash: Annotated[
            bool, J(description="Inclure la Corbeille (soft-delete).")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum de résultats.")] = 100,
    ) -> dict:
        """Liste les tâches (lecture fraîche : synchro miroir avant réponse)."""
        try:
            vue = "toutes" if include_trash else "actives"
            taches = service.toutes(liste=list, vue=vue)
            taches = _actives(taches, include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            resultat = _liste_comme_json(taches)
            resultat["lists"] = service.liste_collections()
            return resultat
        except Exception as exc:  # noqa: BLE001 - contrat JSON pour ChatGPT
            return _erreur(exc)

    @mcp.tool(name="tasks_get")
    def tasks_get(uid: Annotated[str, J(description="UID de la tâche.")]) -> dict:
        """Retourne une tâche précise (état le plus frais possible)."""
        try:
            return {"tache": _json_tache(service.trouver(uid))}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_search")
    def tasks_search(
        query: Annotated[str, J(description="Texte cherché (insensible aux accents/casse).")],
        list: Annotated[Optional[str], J(description="Restreindre à une liste.")] = None,
        include_completed: Annotated[bool, J(description="Inclure les tâches terminées.")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum de résultats.")] = 50,
    ) -> dict:
        """Cherche dans les titres, notes et catégories."""
        try:
            taches = service.toutes(liste=list)
            taches = [
                t for t in taches
                if not t.trashed and correspond(t, query, ("title", "notes", "categories"))
            ]
            taches = _actives(taches, include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            return _liste_comme_json(taches)
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    # ------------------------------------------------------------------ écriture

    @mcp.tool(name="tasks_create")
    def tasks_create(
        title: Annotated[str, J(description="Titre de la tâche (SUMMARY).")],
        list: Annotated[str, J(description="Liste de destination (défaut : Inbox).")] = "Inbox",
        notes: Annotated[Optional[str], J(description="Notes détaillées (DESCRIPTION).")] = None,
        due: Annotated[
            Optional[str],
            J(description="Échéance ISO (ex. 2026-09-06T18:00 ou avec décalage)."),
        ] = None,
        start: Annotated[Optional[str], J(description="Début ISO (DTSTART).")] = None,
        priority: Annotated[Optional[int], J(description="Priorité 1..9 (1 = haute).")] = None,
    ) -> dict:
        """Crée une tâche (apparaît dans Apple Rappels après synchro)."""
        try:
            due_dt = temps.parser_dt(due)
            start_dt = temps.parser_dt(start)
            if due and due_dt is None:
                return {"erreur": "date_invalide", "message": f"échéance illisible : {due!r}"}
            if priority is not None and not (1 <= int(priority) <= 9):
                return {"erreur": "priorite_invalide", "message": "priorité hors bornes 1..9"}
            notes_finales = _notes_avec_contexte_recent(service, notes)
            tache = service.creer(
                title, liste=list, notes=notes_finales, due=due_dt, start=start_dt,
                priority=int(priority) if priority is not None else None,
            )
            return {"tache": _json_tache(tache)}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_update")
    def tasks_update(
        uid: Annotated[str, J(description="UID de la tâche à modifier.")],
        title: Annotated[Optional[str], J(description="Nouveau titre (ou omit).")] = None,
        notes: Annotated[Optional[str], J(description="Nouvelles notes (ou omit).")] = None,
        due: Annotated[Optional[str], J(description="Nouvelle échéance ISO.")] = None,
        start: Annotated[Optional[str], J(description="Nouveau début ISO.")] = None,
        priority: Annotated[Optional[int], J(description="Priorité 1..9.")] = None,
        clear_notes: Annotated[bool, J(description="Effacer les notes.")] = False,
        clear_due: Annotated[bool, J(description="Enlever l'échéance.")] = False,
        clear_start: Annotated[bool, J(description="Enlever le début.")] = False,
        clear_priority: Annotated[bool, J(description="Enlever la priorité.")] = False,
        inclure_terminee: Annotated[
            bool,
            J(description="Autoriser la modification d'une tâche déjà terminée. "
                          "False (défaut) : refusée tant que la tâche n'est pas rouverte "
                          "(tasks_reopen)."),
        ] = False,
        etag_attendu: Annotated[
            Optional[str],
            J(description="ETag de la version que vous avez lue : si la tâche a changé "
                          "entre-temps (iPhone), la modification est refusée sans écrasement."),
        ] = None,
    ) -> dict:
        """Modifie une tâche. Refuse par défaut les tâches déjà terminées.
        Écriture conditionnelle If-Match ; jamais d'écrasement silencieux."""
        try:
            changements: dict = {}
            if title is not None:
                changements["title"] = title
            if notes is not None:
                changements["notes"] = notes
            if clear_notes:
                changements["notes"] = None
            due_dt = temps.parser_dt(due) if due else None
            if due and due_dt is None:
                return {"erreur": "date_invalide", "message": f"échéance illisible : {due!r}"}
            if clear_due:
                changements["due"] = None
            elif due_dt is not None:
                changements["due"] = due_dt
            start_dt = temps.parser_dt(start) if start else None
            if start and start_dt is None:
                return {"erreur": "date_invalide", "message": f"début illisible : {start!r}"}
            if clear_start:
                changements["start"] = None
            elif start_dt is not None:
                changements["start"] = start_dt
            if clear_priority:
                changements["priority"] = None
            elif priority is not None:
                if not (1 <= int(priority) <= 9):
                    return {"erreur": "priorite_invalide", "message": "priorité hors bornes 1..9"}
                changements["priority"] = int(priority)
            if not changements:
                return {"erreur": "aucun_changement", "message": "rien à modifier"}
            actuelle = service.trouver(uid)
            refus = _garde_terminee(uid, actuelle, inclure_terminee)
            if refus is not None:
                return refus
            tache, diffs = service.modifier(uid, etag_attendu=etag_attendu, **changements)
            return {"tache": _json_tache(tache), "modifications": diffs}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_complete")
    def tasks_complete(uid: Annotated[str, J(description="UID de la tâche.")]) -> dict:
        """Marque la tâche terminée (visible dans Apple Rappels)."""
        try:
            return {"tache": _json_tache(service.completer(uid))}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_reopen")
    def tasks_reopen(uid: Annotated[str, J(description="UID de la tâche.")]) -> dict:
        """Rouvre une tâche terminée."""
        try:
            return {"tache": _json_tache(service.rouvrir(uid))}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_move")
    def tasks_move(
        uid: Annotated[str, J(description="UID de la tâche.")],
        list: Annotated[str, J(description="Liste de destination (restaurer = liste d'origine).")],
    ) -> dict:
        """Déplace une tâche vers une autre liste (restaure une tâche corbeillée)."""
        try:
            return {"tache": _json_tache(service.deplacer(uid, liste=list))}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_delete")
    def tasks_delete(
        uid: Annotated[str, J(description="UID de la tâche.")],
        permanent: Annotated[
            bool,
            J(description="False (défaut) : soft delete vers la Corbeille (rétention "
                          "configurée, restauration possible). True : suppression définitive."),
        ] = False,
    ) -> dict:
        """Supprime une tâche. Par défaut : Corbeille + journal + snapshots (restaurable)."""
        try:
            return {"resultat": service.supprimer(uid, permanent=permanent)}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    # ------------------------------------------------------------------ vues

    @mcp.tool(name="tasks_inbox")
    def tasks_inbox(
        include_completed: Annotated[bool, J(description="Inclure les terminées.")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum.")] = 100,
    ) -> dict:
        """Boîte de capture (liste Inbox)."""
        try:
            taches = _actives(service.toutes(liste="Inbox"), include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            return _liste_comme_json(taches)
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_today")
    def tasks_today(
        include_completed: Annotated[bool, J(description="Inclure les terminées.")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum.")] = 100,
    ) -> dict:
        """Tâches dont l'échéance tombe aujourd'hui (Europe/Paris)."""
        try:
            jour = temps.aujourdhui_paris()
            taches = [
                t for t in service.toutes()
                if t.due and temps.date_paris(t.due) == jour
            ]
            taches = _actives(taches, include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            return _liste_comme_json(taches)
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_overdue")
    def tasks_overdue(
        include_completed: Annotated[bool, J(description="Inclure les terminées.")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum.")] = 100,
    ) -> dict:
        """Tâches en retard (échéance passée, non terminées)."""
        try:
            maintenant = temps.maintenant_utc()
            taches = [t for t in service.toutes() if est_en_retard(t, maintenant)]
            taches = _actives(taches, include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            return _liste_comme_json(taches)
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_upcoming")
    def tasks_upcoming(
        days: Annotated[int, J(description="Horizon en jours (défaut 7).")] = 7,
        include_completed: Annotated[bool, J(description="Inclure les terminées.")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum.")] = 100,
    ) -> dict:
        """Tâches échéant dans les N prochains jours (dès aujourd'hui)."""
        try:
            from datetime import timedelta

            debut = temps.debut_jour_paris(temps.aujourdhui_paris())
            fin = temps.vers_paris(debut + timedelta(days=max(0, days)))
            taches = [
                t for t in service.toutes()
                if t.due and debut <= temps.vers_paris(t.due) < fin
            ]
            taches = _actives(taches, include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            return _liste_comme_json(taches)
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_unscheduled")
    def tasks_unscheduled(
        include_completed: Annotated[bool, J(description="Inclure les terminées.")] = False,
        limit: Annotated[Optional[int], J(description="Nombre maximum.")] = 100,
    ) -> dict:
        """Tâches sans échéance (à dater lors du passage du planner)."""
        try:
            taches = [t for t in service.toutes() if t.due is None]
            taches = _actives(taches, include_completed)
            if limit:
                taches = taches[: max(0, limit)]
            return _liste_comme_json(taches)
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    # ------------------------------------------------------------------ récent / historique

    @mcp.tool(name="tasks_recently_changed")
    def tasks_recently_changed(
        since: Annotated[
            Optional[str],
            J(description="Depuis (ISO avec décalage ou naive = Europe/Paris). "
                          "Ex. 2026-09-05T00:00:00+02:00."),
        ] = None,
        hours: Annotated[Optional[int], J(description="Alternative : les N dernières heures.")] = None,
        limit: Annotated[Optional[int], J(description="Nombre maximum d'événements.")] = 200,
    ) -> dict:
        """Tout ce qui a changé depuis une date (iPhone inclus, détecté par synchro)."""
        try:
            service.synchroniser(force=True)
            depuis = _depuis_parametres(since=since, hours=hours)
            evenements = magasin.journal_depuis(depuis, limite=limit or 200)
            resume: dict[str, int] = {}
            for ev in evenements:
                resume[ev["action"]] = resume.get(ev["action"], 0) + 1
            return {
                "depuis": depuis or "tous",
                "evenements": evenements,
                "resume": resume,
            }
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)

    @mcp.tool(name="tasks_history")
    def tasks_history(
        uid: Annotated[str, J(description="UID de la tâche.")],
        limit: Annotated[Optional[int], J(description="Nombre maximum d'événements.")] = 100,
    ) -> dict:
        """Historique complet d'une tâche (créations, modifs, déplacements, corbeille)."""
        try:
            evenements = magasin.journal_uid(uid, limite=limit or 100)
            return {"uid": uid, "evenements": evenements}
        except Exception as exc:  # noqa: BLE001
            return _erreur(exc)
