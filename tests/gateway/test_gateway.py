"""Tests de la passerelle tasks (pattern vault-mcp).

Couvre le contrat que ChatGPT/claude.ai exigent :
- decouverte (RFC 8414 + RFC 9728) ;
- enregistrement dynamique (RFC 7591) ;
- consentement humain (phrase de passe) puis echange du code (PKCE S256) ;
- /mcp anonyme -> 401 avec resource_metadata ;
- /mcp avec jeton -> proxy transparent vers l'upstream, outils filtres.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import threading
import uuid

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from tasks_gateway.app import construire_application
from tasks_gateway.oauth import PORTEE, hacher_phrase

EMETTEUR = "https://tasks.example.test"
JETON_STATIQUE = "j" * 40
PHRASE = "phrase-de-test-2026"


def _normaliser_issuer(valeur: str) -> str:
    return valeur.rstrip("/")


@pytest.fixture()
def environ(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_MCP_ISSUER", EMETTEUR)
    monkeypatch.setenv("TASKS_MCP_UPSTREAM", "http://127.0.0.1:9")  # port ferme
    monkeypatch.setenv("TASKS_MCP_OAUTH_DIR", str(tmp_path))
    monkeypatch.setenv("TASKS_MCP_TOKEN", JETON_STATIQUE)
    monkeypatch.setenv("TASKS_MCP_CONSENT_HASH", hacher_phrase(PHRASE))
    return tmp_path


def _client() -> httpx.AsyncClient:
    app = construire_application()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=EMETTEUR)


def _json_rpc(methode: str, identifiant: int, params: dict | None = None) -> bytes:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": identifiant,
            "method": methode,
            "params": params or {},
        }
    ).encode()


def _verifier_s256(longueur: int = 48) -> tuple[str, str]:
    """(code_verifier, code_challenge S256) conformes RFC 7636."""
    verifier = base64.urlsafe_b64encode(os.urandom(longueur)).rstrip(b"=").decode()
    empreinte = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(empreinte).rstrip(b"=").decode()
    return verifier, challenge


def _courir(coro):
    return asyncio.run(coro)


# --- Decouverte -----------------------------------------------------------------------
def test_metadonnees_serveur_autorisation(environ):
    async def _t():
        async with _client() as c:
            r = await c.get("/.well-known/oauth-authorization-server")
            assert r.status_code == 200
            d = r.json()
            assert _normaliser_issuer(d["issuer"]) == EMETTEUR
            assert d["registration_endpoint"].startswith(EMETTEUR)
            assert "S256" in d["code_challenge_methods_supported"]
            assert "client_secret_post" in d["token_endpoint_auth_methods_supported"]
            portees = d.get("scopes_supported") or []
            assert PORTEE in portees

    _courir(_t())


def test_metadonnees_ressource_protegee(environ):
    async def _t():
        async with _client() as c:
            r = await c.get("/.well-known/oauth-protected-resource/mcp")
            assert r.status_code == 200
            d = r.json()
            assert d["resource"] == f"{EMETTEUR}/mcp"
            assert [_normaliser_issuer(x) for x in d["authorization_servers"]] == [EMETTEUR]
            assert "tasks:ecriture" in d["scopes_supported"]

    _courir(_t())


# --- Acces /mcp sans jeton -------------------------------------------------------------
def test_mcp_anonyme_refuse_post(environ):
    async def _t():
        async with _client() as c:
            r = await c.post(
                "/mcp",
                content=_json_rpc("initialize", 1),
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            )
            assert r.status_code == 401
            assert "resource_metadata" in r.headers.get("www-authenticate", "")

    _courir(_t())


def test_mcp_anonyme_refuse_get(environ):
    async def _t():
        async with _client() as c:
            r = await c.get("/mcp")
            assert r.status_code == 401

    _courir(_t())


def test_mcp_chemin_inconnu(environ):
    async def _t():
        async with _client() as c:
            r = await c.get("/")
            assert r.status_code == 404

    _courir(_t())


# --- Enregistrement + consentement + token --------------------------------------------
def test_flux_oauth_complet(environ):
    async def _t():
        async with _client() as c:
            # 1. Enregistrement dynamique (RFC 7591)
            r = await c.post(
                "/register",
                json={
                    "client_name": "chatgpt-test",
                    "redirect_uris": ["https://chatgpt.com/aip/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                    "scope": PORTEE,
                },
            )
            assert r.status_code == 201, r.text
            client = r.json()
            assert client["client_id"] and client["client_secret"]

            # 2. Demande d'autorisation (PKCE S256)
            verifier, challenge = _verifier_s256()
            params = {
                "client_id": client["client_id"],
                "redirect_uri": client["redirect_uris"][0],
                "response_type": "code",
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": "etat-123",
                "scope": PORTEE,
            }
            r = await c.get("/authorize", params=params)
            assert r.status_code in (302, 307), r.text
            location = r.headers["location"]
            assert location.startswith(f"{EMETTEUR}/consentement?demande=")
            demande = location.split("demande=", 1)[1]

            # 3. Page de consentement affichee
            r = await c.get(f"/consentement?demande={demande}")
            assert r.status_code == 200
            assert "tâches et rappels" in r.text

            # 4. Mauvaise phrase -> 401 ; bonne phrase -> code
            r = await c.post(
                "/consentement",
                data={"demande": demande, "phrase": "mauvaise"},
            )
            assert r.status_code == 401
            r = await c.post(
                "/consentement",
                data={"demande": demande, "phrase": PHRASE},
            )
            assert r.status_code == 302, r.text
            cible = r.headers["location"]
            assert "code=" in cible and "state=etat-123" in cible
            code = cible.split("code=", 1)[1].split("&", 1)[0]

            # 5. Echange du code
            r = await c.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": client["redirect_uris"][0],
                    "client_id": client["client_id"],
                    "client_secret": client["client_secret"],
                    "code_verifier": verifier,
                },
            )
            assert r.status_code == 200, r.text
            jetons = r.json()
            assert jetons["token_type"] == "Bearer"
            assert jetons["access_token"] and jetons["refresh_token"]
            portees = jetons["scope"].split()
            assert PORTEE in portees and "tasks:ecriture" in portees

            # 6. Un code ne s'echange qu'une fois
            r = await c.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": client["redirect_uris"][0],
                    "client_id": client["client_id"],
                    "client_secret": client["client_secret"],
                    "code_verifier": verifier,
                },
            )
            assert r.status_code == 400
            assert "invalid_grant" in r.text

    _courir(_t())


# --- Jeton statique CLI + proxy -------------------------------------------------------
def _serveur_stub() -> tuple[uvicorn.Server, str, object]:
    """Upstream factice : initialize avec session, tools/list avec manage-accounts."""
    import socket
    import time

    async def _post(request):
        corps = await request.body()
        donnees = json.loads(corps)
        methode = donnees.get("method")
        id_ = donnees.get("id")
        if methode == "initialize":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": id_, "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "google-calendar", "version": "2.6.3"}}},
                headers={"mcp-session-id": str(uuid.uuid4())},
            )
        if methode == "tools/list":
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": id_,
                    "result": {
                        "tools": [
                            {"name": "manage-accounts", "description": "admin"},
                            {"name": "list-events", "description": "liste"},
                        ]
                    },
                },
                headers={"mcp-session-id": request.headers.get("mcp-session-id", "")},
            )
        return JSONResponse(
            {"jsonrpc": "2.0", "id": id_, "result": {"content": [{"type": "text", "text": "ok"}]}},
            headers={"mcp-session-id": request.headers.get("mcp-session-id", "")},
        )

    app = Starlette(routes=[Route("/mcp", _post, methods=["POST"])])
    socket_ecoute = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    socket_ecoute.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    socket_ecoute.bind(("127.0.0.1", 0))
    port = socket_ecoute.getsockname()[1]
    socket_ecoute.listen(128)
    config = uvicorn.Config(app, log_level="error")
    serveur = uvicorn.Server(config)
    thread = threading.Thread(target=serveur.run, kwargs={"sockets": [socket_ecoute]}, daemon=True)
    thread.start()
    for _ in range(300):
        if serveur.started:
            break
        time.sleep(0.02)
    return serveur, f"http://127.0.0.1:{port}", socket_ecoute


def test_proxy_initialize_et_verbatim(environ):
    """V1 tasks : aucun outil masqué (CRUD complet pour tout client authentifié).

    Les outils annoncés par l'upstream passent tels quels ; le mécanisme de filtrage
    reste disponible pour de futures politiques (lecture seule, interdiction de
    suppression) mais n'est pas actif.
    """

    async def _t():
        serveur, url, _socket_ecoute = _serveur_stub()
        try:
            os.environ["TASKS_MCP_UPSTREAM"] = url
            app = construire_application()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=EMETTEUR) as c:
                entetes = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {JETON_STATIQUE}",
                }
                r = await c.post("/mcp", content=_json_rpc("initialize", 1), headers=entetes)
                assert r.status_code == 200, r.text
                # la session creee par l'upstream est relayee telle quelle
                assert r.headers.get("mcp-session-id")
                session = r.headers["mcp-session-id"]

                r = await c.post(
                    "/mcp",
                    content=_json_rpc("tools/list", 2),
                    headers={**entetes, "mcp-session-id": session},
                )
                assert r.status_code == 200
                outils = [t["name"] for t in r.json()["result"]["tools"]]
                # aucun outil retiré en V1
                assert "manage-accounts" in outils
                assert "list-events" in outils

                # call_tool passe verbatim
                r = await c.post(
                    "/mcp",
                    content=_json_rpc("tools/call", 3, {"name": "list-events", "arguments": {}}),
                    headers={**entetes, "mcp-session-id": session},
                )
                assert r.status_code == 200
                assert "ok" in r.text
        finally:
            serveur.should_exit = True
            if _socket_ecoute:
                _socket_ecoute.close()

    _courir(_t())


def test_proxy_upstream_indisponible(environ):
    async def _t():
        async with _client() as c:
            r = await c.post(
                "/mcp",
                content=_json_rpc("initialize", 1),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {JETON_STATIQUE}",
                },
            )
            assert r.status_code == 502

    _courir(_t())


def test_filtrage_sset_json(environ):
    """Le filtre tools/list fonctionne en JSON nu et en enveloppe SSE (upstream v2.6.3)."""
    from tasks_gateway.upstream import ProxyMCP

    proxy = ProxyMCP("http://127.0.0.1:9", outils_retires={"manage-accounts"})
    outils = [
        {"name": "manage-accounts", "description": "admin"},
        {"name": "list-events", "description": "liste"},
    ]
    # JSON nu
    corps = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": outils}}).encode()
    filtre = proxy.filtrer(corps, "application/json")
    noms = [t["name"] for t in json.loads(filtre)["result"]["tools"]]
    assert noms == ["list-events"]
    # Enveloppe SSE
    sse = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": outils}}) + "\n\n").encode()
    filtre = proxy.filtrer(sse, "text/event-stream")
    assert b"manage-accounts" not in filtre
    assert b"list-events" in filtre
    # Corps sans outils : inchange
    assert proxy.filtrer(sse.replace(b"manage-accounts", b"x"), "text/event-stream") == sse.replace(b"manage-accounts", b"x")


def test_sante(environ):
    async def _t():
        async with _client() as c:
            r = await c.get("/health")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    _courir(_t())
