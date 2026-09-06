"""Tests HTTP de l'endpoint `POST /context/chatgpt` (tasks-mcp upstream).

Sécurité : jeton dédié obligatoire (401/503), corps borné (413), type JSON
(415), validation stricte des URLs (400), rate limit (429), effacement
(`actif: false`), et routage : seul `/context/chatgpt` atteint l'endpoint.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from tasks_mcp.contexte import RegistreContexte
from tasks_mcp.contexte_http import CHEMIN, ContexteEndpoint, envelopper_application

JETON = "j" * 40
UUID = "67d5b8f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f"
URL = f"https://chatgpt.com/c/{UUID}"


def _courir(coro):
    return asyncio.run(coro)


def _app(
    jeton: str = JETON,
    registre: RegistreContexte | None = None,
    limite_jeton: int = 30,
    limite_ip: int = 120,
    appels_internes: list | None = None,
):
    endpoint = ContexteEndpoint(
        jeton=jeton,
        ttl_s=300,
        registre=registre or RegistreContexte(),
        limite_jeton=limite_jeton,
        limite_ip=limite_ip,
    )

    async def interne(scope, receive, send):
        if appels_internes is not None:
            appels_internes.append(scope.get("path"))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return envelopper_application(interne, endpoint), endpoint


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserveur")


def _entetes(jeton: str | None = JETON) -> dict[str, str]:
    entetes = {"Content-Type": "application/json"}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    return entetes


def test_post_valide_enregistre_le_contexte():
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL, "title": "Titre", "client_id": "abc"}, headers=_entetes())
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["statut"] == "ok"
            assert d["ttl_s"] == 300

    _courir(_t())
    dernier = registre.dernier_valide(ttl_s=300)
    assert dernier is not None
    assert dernier.url == URL
    assert dernier.titre == "Titre"
    assert dernier.client_id == "abc"


def test_sans_jeton_401():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL}, headers=_entetes(jeton=None))
            assert r.status_code == 401
            assert r.json()["erreur"] == "non_autorise"

    _courir(_t())


def test_mauvais_jeton_401():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL}, headers=_entetes(jeton="mauvais"))
            assert r.status_code == 401

    _courir(_t())


def test_endpoint_non_configuré_503():
    app, _ = _app(jeton="")

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL}, headers=_entetes(jeton=None))
            assert r.status_code == 503
            assert r.json()["erreur"] == "non_configure"

    _courir(_t())


def test_methodes_non_post_405():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            for methode in ("get", "put", "delete"):
                r = await getattr(c, methode)(CHEMIN, headers=_entetes())
                assert r.status_code == 405, methode
            assert (await c.get(CHEMIN)).headers.get("allow") == "POST"

    _courir(_t())


def test_type_media_non_json_415():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, content=b'{"url":"x"}', headers={"Content-Type": "text/plain", "Authorization": f"Bearer {JETON}"})
            assert r.status_code == 415

    _courir(_t())


def test_json_invalide_400():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, content=b"{pas du json", headers=_entetes())
            assert r.status_code == 400
            assert r.json()["erreur"] == "json_invalide"

    _courir(_t())

def test_url_invalide_rejetee():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            for mauvaise in (
                "https://evil.com/c/abc",
                "https://chatgpt.com/share/abc",
                "https://chatgpt.com/g/g-xyz",
                "javascript:alert(1)",
                "https://chatgpt.com/",
            ):
                r = await c.post(CHEMIN, json={"url": mauvaise}, headers=_entetes())
                assert r.status_code == 400, mauvaise
                assert r.json()["erreur"] == "url_invalide", mauvaise

    _courir(_t())


def test_corps_trop_gros_413():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            gros = json.dumps({"url": URL, "title": "x" * 10_000})
            r = await c.post(CHEMIN, content=gros.encode(), headers=_entetes())
            assert r.status_code == 413

    _courir(_t())


def test_rate_limit_par_jeton_429():
    registre = RegistreContexte()
    app, _ = _app(registre=registre, limite_jeton=2, limite_ip=100)

    async def _t():
        async with _client(app) as c:
            assert (await c.post(CHEMIN, json={"url": URL}, headers=_entetes())).status_code == 200
            assert (await c.post(CHEMIN, json={"url": URL}, headers=_entetes())).status_code == 200
            r = await c.post(CHEMIN, json={"url": URL}, headers=_entetes())
            assert r.status_code == 429
            assert r.json()["erreur"] == "trop_de_requetes"

    _courir(_t())


def test_actif_false_efface_le_contexte():
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            assert (await c.post(CHEMIN, json={"url": URL, "client_id": "abc"}, headers=_entetes())).status_code == 200
            assert registre.dernier_valide() is not None
            r = await c.post(CHEMIN, json={"actif": False, "client_id": "abc"}, headers=_entetes())
            assert r.status_code == 200
            assert r.json() == {"statut": "ok", "efface": 1}
            assert registre.dernier_valide() is None

    _courir(_t())


def test_seul_le_chemin_contexte_atteint_l_endpoint():
    appels_internes: list = []
    app, _ = _app(appels_internes=appels_internes)

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL}, headers=_entetes())
            assert r.status_code == 200
            # Tout autre chemin retombe sur l'application interne (le MCP).
            r2 = await c.post("/mcp", json={}, headers=_entetes())
            assert r2.status_code == 204  # réponse de l'interne factice
            assert appels_internes == ["/mcp"]

    _courir(_t())
