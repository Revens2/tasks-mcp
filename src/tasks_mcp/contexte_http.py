"""Endpoint HTTP `/context/chatgpt` (servi par tasks-mcp, jamais par /mcp).

L'extension navigateur locale y publie l'URL de la conversation ChatGPT active.
L'endpoint vit DANS le processus tasks-mcp (127.0.0.1:8791) : le registre
mémoire qu'il alimente est exactement celui que lit `tasks_create` — aucune
synchronisation inter-processus, aucune route ajoutée à la passerelle OAuth.

Méthodes :
- `POST` : dépose le contexte d'une conversation (réponse explicite
  `conversation_detectee` / `id_present`) ou efface (`{"actif": false}`) ;
- `GET` : diagnostic interne (contexte présent + âge, jamais l'URL ni l'ID).

Sécurité :
- jeton dédié `TASKS_CONTEXT_TOKEN` (ultra-scopé : il ne donne AUCUN droit MCP,
  il n'est jamais présenté à la passerelle) ; endpoint inerte (503) si absent ;
- comparaison en temps constant ; rate limit applicatif (IP + jeton) en plus
  des zones nginx ; corps ≤ 4096 octets ;
- validation stricte dans contexte.valider_url / contexte_depuis_payload
  (https://chatgpt.com/c/<id> uniquement, jamais /share/, jamais un autre
  hôte, jamais de query…) ;
- par défaut la réponse n'écho PAS l'ID de conversation (diagnostic : seuls
  les booléens `conversation_detectee` / `id_present`) ; l'écho n'est activé
  qu'en débogage explicite (`echo_id=True`) et n'est jamais loggé ;
- aucun log du corps : ni URL, ni identifiant de conversation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from .contexte import PayloadInvalide, RegistreContexte, contexte_depuis_payload

CHEMIN = "/context/chatgpt"
TAILLE_MAX_CORPS = 4096

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class _Limiteur:
    """Rate limit en mémoire (fenêtre glissante par clé)."""

    def __init__(self, limite: int, fenetre_s: float) -> None:
        self.limite = limite
        self.fenetre_s = fenetre_s
        self._horodatages: dict[str, list[float]] = {}

    def autoriser(self, cle: str) -> bool:
        maintenant = time.monotonic()
        liste = [t for t in self._horodatages.get(cle, []) if t > maintenant - self.fenetre_s]
        if len(liste) >= self.limite:
            self._horodatages[cle] = liste
            return False
        liste.append(maintenant)
        self._horodatages[cle] = liste
        return True


class ContexteEndpoint:
    """Gestionnaire ASGI de `GET|POST /context/chatgpt`."""

    def __init__(
        self,
        jeton: str,
        ttl_s: int = 300,
        registre: RegistreContexte | None = None,
        echo_id: bool = False,
        limite_jeton: int = 30,
        limite_ip: int = 120,
        fenetre_s: float = 60.0,
    ) -> None:
        self.jeton = (jeton or "").strip()
        self.ttl_s = max(1, int(ttl_s))
        self.registre = registre or RegistreContexte()
        self.echo_id = bool(echo_id)
        self._actif = bool(self.jeton)
        self._jeton_empreinte = hashlib.sha256(self.jeton.encode()).hexdigest()
        self._limites = {
            "jeton": _Limiteur(limite_jeton, fenetre_s),
            "ip": _Limiteur(limite_ip, fenetre_s),
        }

    # ------------------------------------------------------------------ helpers

    async def _repondre(self, send: Send, statut: int, donnees: dict[str, Any]) -> None:
        corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": statut,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": corps, "more_body": False})

    def _ip(self, scope: Scope) -> str:
        # nginx pose X-Real-IP (écrase tout en-tête client) ; boucle locale sinon.
        for nom, valeur in scope.get("headers", []):
            if nom.lower() == b"x-real-ip":
                return valeur.decode("latin-1", "replace")[:64] or "inconnue"
        client = scope.get("client")
        return client[0] if client else "inconnue"

    def _autorise(self, scope: Scope) -> bool:
        for nom, valeur in scope.get("headers", []):
            if nom.lower() != b"authorization":
                continue
            brut = valeur.decode("latin-1", "replace")
            if brut.startswith("Bearer "):
                jeton = brut[len("Bearer "):]
                if jeton and hmac.compare_digest(jeton, self.jeton):
                    return True
        return False

    async def _lire_corps(self, receive: Receive) -> bytes:
        morceaux: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            morceau = message.get("body", b"")
            morceaux.append(morceau)
            total += len(morceau)
            if total > TAILLE_MAX_CORPS:
                raise ValueError("corps trop volumineux")
            if not message.get("more_body"):
                return b"".join(morceaux)

    def _type_json(self, scope: Scope) -> bool:
        for nom, valeur in scope.get("headers", []):
            if nom.lower() == b"content-type":
                return valeur.decode("latin-1", "replace").lower().startswith("application/json")
        return False

    # ------------------------------------------------------------------ appel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        methode = scope["method"]
        if methode not in ("GET", "POST"):
            await send(
                {
                    "type": "http.response.start",
                    "status": 405,
                    "headers": [(b"allow", b"GET, POST")],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        if not self._actif:
            # Endpoint non configuré (pas de TASKS_CONTEXT_TOKEN) : inerte.
            await self._repondre(send, 503, {"erreur": "non_configure"})
            return
        if methode == "POST":
            if not self._type_json(scope):
                await self._repondre(send, 415, {"erreur": "type_media_invalide"})
                return
            try:
                corps = await self._lire_corps(receive)
            except ValueError:
                await self._repondre(send, 413, {"erreur": "corps_trop_gros"})
                return
        else:
            corps = b""
        if not self._autorise(scope):
            # Même message pour jeton absent ou invalide (pas d'oracle).
            await self._repondre(send, 401, {"erreur": "non_autorise"})
            return
        if not self._limites["jeton"].autoriser(self._jeton_empreinte) or not self._limites["ip"].autoriser(
            f"ip:{self._ip(scope)}"
        ):
            await self._repondre(send, 429, {"erreur": "trop_de_requetes"})
            return

        if methode == "GET":
            # Diagnostic interne : présence + âge + raison du contexte le plus
            # récent. Jamais l'URL ni l'ID de conversation, aucun historique.
            etat = self.registre.etat(ttl_s=self.ttl_s)
            await self._repondre(
                send,
                200,
                {
                    "statut": "ok",
                    "contexte_present": etat["contexte_present"],
                    "id_present": etat["contexte_present"],
                    "age_s": etat["age_s"],
                    "raison": etat["raison"],
                    "dernier_depot_s": etat["dernier_depot_s"],
                    "ttl_s": self.ttl_s,
                },
            )
            return

        try:
            payload = json.loads(corps.decode("utf-8")) if corps else None
        except (ValueError, UnicodeDecodeError):
            await self._repondre(send, 400, {"erreur": "json_invalide"})
            return

        if isinstance(payload, dict) and payload.get("actif") is False:
            # Effacement SOUS CONDITION DE PROPRIÉTÉ : seul l'onglet qui a déposé
            # le contexte peut l'effacer (onglet_id identique). Un onglet tiers
            # (page ChatGPT sans conversation, accueil…) ne peut jamais effacer le
            # contexte d'une conversation ouverte ailleurs — cause racine du bug
            # « contexte effacé en boucle toutes les ~2 min ».
            client_id = payload.get("client_id")
            onglet_id = payload.get("onglet_id")
            if not (isinstance(client_id, str) and client_id):
                retire = 0
            else:
                oid = onglet_id if isinstance(onglet_id, str) and onglet_id else None
                retire = self.registre.effacer(client_id, oid)
            await self._repondre(send, 200, {"statut": "ok", "efface": retire})
            return

        try:
            contexte = contexte_depuis_payload(payload)
        except PayloadInvalide as exc:
            await self._repondre(send, 400, {"erreur": exc.code, "message": str(exc)})
            return

        self.registre.enregistrer(contexte)
        reponse: dict[str, Any] = {
            "statut": "ok",
            "conversation_detectee": True,
            "id_present": True,
            "ttl_s": self.ttl_s,
        }
        if self.echo_id:
            # Débogage explicite uniquement (TASKS_CONTEXT_ECHO_ID=1).
            reponse["conversation_id"] = contexte.conversation_id
        await self._repondre(send, 200, reponse)


def envelopper_application(interne: Callable, endpoint: ContexteEndpoint) -> Callable:
    """ASGI routeur minimal : /context/chatgpt → endpoint, tout le reste → interne.

    `interne` est l'application MCP (streamable HTTP) éventuellement enveloppée
    du middleware d'acteur : le nouvel endpoint ne passe jamais par lui.
    """

    async def application(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == CHEMIN:
            await endpoint(scope, receive, send)
            return
        await interne(scope, receive, send)

    return application
