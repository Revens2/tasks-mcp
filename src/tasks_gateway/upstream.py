"""Proxy transparent vers le serveur MCP upstream (conteneur `calendar-mcp`, 127.0.0.1:8791).

La passerelle n'est PAS un serveur MCP de plein exercice : elle valide le jeton d'acces
(OAuth ou Bearer statique) puis relaie tel quel le trafic Streamable HTTP vers l'upstream.
Les sessions MCP restent la propriete de l'upstream : l'en-tete `mcp-session-id` est
transmis sans modification dans les deux sens, ce qui rend le proxy invisible pour le
protocole.

Seule reecriture volontaire : la reponse `tools/list` est filtree pour retirer
`manage-accounts` (outil d'administration d'un outil d'administration, enregistre par l'upstream
independamment de `ENABLED_TOOLS`). Tout le reste passe verbatim.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

# En-tetes de requete retransmis a l'upstream. Pas de host/content-length (httpx les
# gere), pas d'accept-encoding (on evite la compression intermediaire), pas d'origin
# (l'upstream le validerait comme non-localhost et repondrait 403).
_ENTETES_REQUETE = {
    "content-type",
    "accept",
    "mcp-session-id",
    "mcp-protocol-version",
    "last-event-id",
    "user-agent",
}

# En-tetes de reponse retransmis au client. content-length et transfer-encoding sont
# laisses a uvicorn (chunked), les autres (date, server...) n'ont pas lieu d'etre.
_ENTETES_REPONSE = {
    "content-type",
    "mcp-session-id",
    "cache-control",
}

_LIMITE_CORPS = 5 * 1024 * 1024  # 5 Mo : garde-fou contre un corps de requete aberrant.


def _lire_corps(receive: Receive) -> bytes:
    """Assemble le corps de la requete ASGI (avec garde-fou de taille)."""

    async def _lire() -> bytes:
        morceaux: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            morceau = message.get("body", b"")
            morceaux.append(morceau)
            total += len(morceau)
            if total > _LIMITE_CORPS:
                raise ValueError("corps de requete trop volumineux")
            if not message.get("more_body"):
                break
        return b"".join(morceaux)

    return _lire()


def _entetes(scope: Scope) -> dict[str, str]:
    """En-tetes de requete autorises, depuis la liste ASGI (bytes -> str)."""
    resultat: dict[str, str] = {}
    for cle, valeur in scope.get("headers", []):
        nom = cle.decode("latin-1").lower()
        if nom in _ENTETES_REQUETE:
            resultat[nom] = valeur.decode("latin-1")
    return resultat


async def _envoyer_reponse_json(
    send: Send, statut: int, donnees: dict[str, Any]
) -> None:
    corps = json.dumps(donnees, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": statut,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": corps, "more_body": False})


async def _envoyer_corps(
    send: Send, statut: int, entetes: httpx.Headers, corps: bytes
) -> None:
    entetes_asgi = [
        (cle.encode("latin-1"), valeur.encode("latin-1"))
        for cle, valeur in entetes.items()
        if cle.lower() in _ENTETES_REPONSE
    ]
    await send({"type": "http.response.start", "status": statut, "headers": entetes_asgi})
    await send({"type": "http.response.body", "body": corps, "more_body": False})


async def _relayer_flux(send: Send, reponse: httpx.Response) -> None:
    """Relai d'un corps eventuellement infini (SSE) morceau par morceau."""
    entetes_asgi = [
        (cle.encode("latin-1"), valeur.encode("latin-1"))
        for cle, valeur in reponse.headers.items()
        if cle.lower() in _ENTETES_REPONSE
    ]
    await send(
        {"type": "http.response.start", "status": reponse.status_code, "headers": entetes_asgi}
    )
    try:
        async for morceau in reponse.aiter_raw():
            await send({"type": "http.response.body", "body": morceau, "more_body": True})
    except (httpx.HTTPError, OSError) as exc:  # flux coupe cote upstream
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        return
    await send({"type": "http.response.body", "body": b"", "more_body": False})


class ProxyMCP:
    """Endpoint ASGI : /mcp authentifie (par le middleware) puis relaye vers l'upstream."""

    def __init__(self, base_url: str, outils_retires: set[str] | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._outils_retires = outils_retires or set()
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            # Timeouts longs : une session SSE GET reste ouverte plusieurs heures.
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(3600.0, connect=5.0),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._client

    def _filtrer_json(self, corps: bytes) -> bytes:
        """Retire les outils non souhaites d'un corps JSON-RPC (reponse tools/list)."""
        try:
            donnees = json.loads(corps)
        except (ValueError, UnicodeDecodeError):
            return corps
        if not isinstance(donnees, dict):
            return corps
        resultat = donnees.get("result")
        if not isinstance(resultat, dict):
            return corps
        outils = resultat.get("tools")
        if not isinstance(outils, list):
            return corps
        conserves = [
            outil
            for outil in outils
            if not (isinstance(outil, dict) and outil.get("name") in self._outils_retires)
        ]
        if len(conserves) == len(outils):
            return corps
        resultat["tools"] = conserves
        return json.dumps(donnees, ensure_ascii=False).encode()

    def _filtrer_sse(self, corps: bytes) -> bytes:
        """Filtre tools/list dans une reponse en enveloppe SSE (`event: message` + `data:`).

        L'upstream v2.6.3 repond en SSE aux POST quand le client accepte
        `text/event-stream` (ce que font tous les clients SDK) : le filtre doit donc
        savoir reecrire l'enveloppe, pas seulement le JSON nu.
        """
        if not self._outils_retires:
            return corps
        lignes = corps.split(b"\n")
        modifiees = False
        for i, ligne in enumerate(lignes):
            if not ligne.startswith(b"data: "):
                continue
            payload = ligne[len(b"data: "):]
            try:
                donnees = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                continue
            resultat = donnees.get("result") if isinstance(donnees, dict) else None
            if not isinstance(resultat, dict) or not isinstance(resultat.get("tools"), list):
                continue
            conserves = [
                outil
                for outil in resultat["tools"]
                if not (isinstance(outil, dict) and outil.get("name") in self._outils_retires)
            ]
            if len(conserves) == len(resultat["tools"]):
                continue
            resultat["tools"] = conserves
            lignes[i] = b"data: " + json.dumps(donnees, ensure_ascii=False).encode()
            modifiees = True
        return b"\n".join(lignes) if modifiees else corps

    def filtrer(self, corps: bytes, content_type: str) -> bytes:
        """Point d'entree du filtrage : JSON nu ou enveloppe SSE."""
        if not self._outils_retires:
            return corps
        if "text/event-stream" in content_type.lower():
            return self._filtrer_sse(corps)
        if "application/json" in content_type.lower():
            return self._filtrer_json(corps)
        return corps

    @staticmethod
    def _identite(scope: Scope) -> str | None:
        """client_id du jeton valide (peuplé par AuthenticationMiddleware)."""
        utilisateur = scope.get("user")
        jeton = getattr(utilisateur, "access_token", None)
        client_id = getattr(jeton, "client_id", None)
        if not isinstance(client_id, str) or not client_id:
            return None
        return client_id[:128]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        methode = scope["method"]
        chemin = scope.get("path", "/mcp")
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"{self._base_url}{chemin}" + (f"?{query}" if query else "")

        try:
            corps = await _lire_corps(receive) if methode in ("POST", "PUT", "PATCH") else None
        except ValueError:
            await _envoyer_reponse_json(
                send, 413, {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Payload Too Large"}, "id": None}
            )
            return

        entetes = _entetes(scope)
        identite = self._identite(scope)
        if identite:
            entetes["x-tasks-mcp-acteur"] = identite
            entetes["x-tasks-mcp-mode"] = "cli" if identite == "tasks-mcp-cli-statique" else "oauth"

        try:
            requete = self._http().build_request(
                methode, url, headers=entetes, content=corps
            )
            reponse = await self._http().send(requete, stream=True)
        except httpx.HTTPError as exc:
            await _envoyer_reponse_json(
                send,
                502,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": f"upstream indisponible: {exc.__class__.__name__}"},
                    "id": None,
                },
            )
            return

        try:
            if methode == "GET":
                await _relayer_flux(send, reponse)
            else:
                corps_reponse = await reponse.aread()
                if methode == "POST":
                    corps_reponse = self.filtrer(
                        corps_reponse, reponse.headers.get("content-type", "") or ""
                    )
                await _envoyer_corps(send, reponse.status_code, reponse.headers, corps_reponse)
        finally:
            await reponse.aclose()
