"""Lecture d'un fichier d'environnement simple (KEY=VALUE) sans jamais l'afficher.

Supporte les valeurs entre guillemets doubles ("une valeur avec espaces") écrites
par scripts/lib/common.sh, lisibles aussi par systemd EnvironmentFile et `source`.
"""

from __future__ import annotations

from pathlib import Path


def _nettoyer(valeur: str) -> str:
    valeur = valeur.strip()
    if len(valeur) >= 2 and valeur[0] == '"' and valeur[-1] == '"':
        valeur = valeur[1:-1].replace('\\"', '"')
    return valeur


def charger(chemin: str | Path) -> dict[str, str]:
    """Retourne les paires KEY=VALUE (première occurrence gagne)."""
    resultat: dict[str, str] = {}
    for ligne in Path(chemin).read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        resultat[cle.strip()] = _nettoyer(valeur)
    return resultat


def appliquer(chemin: str | Path) -> dict[str, str]:
    """Charge le fichier et l'injecte dans os.environ (sans écraser l'existant)."""
    import os

    valeurs = charger(chemin)
    for cle, valeur in valeurs.items():
        os.environ.setdefault(cle, valeur)
    return valeurs
