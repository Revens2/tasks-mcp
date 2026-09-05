"""Lecture d'un fichier d'environnement simple (KEY=VALUE) sans jamais l'afficher."""

from __future__ import annotations

from pathlib import Path


def charger(chemin: str | Path) -> dict[str, str]:
    """Retourne les paires KEY=VALUE (première occurrence gagne)."""
    resultat: dict[str, str] = {}
    for ligne in Path(chemin).read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        resultat[cle.strip()] = valeur.strip()
    return resultat


def appliquer(chemin: str | Path) -> dict[str, str]:
    """Charge le fichier et l'injecte dans os.environ (sans écraser l'existant)."""
    import os

    valeurs = charger(chemin)
    for cle, valeur in valeurs.items():
        os.environ.setdefault(cle, valeur)
    return valeurs
