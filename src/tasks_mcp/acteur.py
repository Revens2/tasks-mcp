"""Propagation de l'acteur (qui appelle le MCP) jusqu'au journal d'audit.

La passerelle tasks-gateway injecte l'en-tête interne `x-tasks-mcp-acteur`
(interdit d'entrée : l'en-tête n'est pas dans la liste blanche retransmise par la
passerelle, elle est donc posée par elle-même après authentification). Ce middleware
la lit côté upstream et la stocke dans un ContextVar ; les outils l'écrivent dans le
journal (`acteur`). Sans en-tête (accès direct à l'upstream sur la boucle locale),
l'acteur vaut "local".
"""

from __future__ import annotations

import re
from contextvars import ContextVar

_acteur_courant: ContextVar[str | None] = ContextVar("acteur_mcp", default=None)

_VALIDE = re.compile(r"^[A-Za-z0-9._:@+-]{1,128}$")


def acteur_actuel() -> str:
    valeur = _acteur_courant.get()
    return valeur or "local"


def _nettoyer(valeur: str | None) -> str | None:
    if not valeur:
        return None
    valeur = valeur.strip()
    if len(valeur) > 128 or not _VALIDE.match(valeur):
        return None
    return valeur


class ActeurMiddleware:
    """ASGI : lit x-tasks-mcp-acteur (boucle locale uniquement) et le pose en contexte."""

    def __init__(self, application):
        self._application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            acteur = None
            for nom, valeur in scope.get("headers", []):
                if nom.lower() == b"x-tasks-mcp-acteur":
                    acteur = _nettoyer(valeur.decode("latin-1"))
                    break
            jeton = _acteur_courant.set(acteur)
            try:
                await self._application(scope, receive, send)
            finally:
                _acteur_courant.reset(jeton)
        else:
            await self._application(scope, receive, send)
