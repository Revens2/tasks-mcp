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

    return envelopper_application(_interne_factice(appels_internes), endpoint), endpoint


def _interne_factice(appels_internes: list | None = None):
    async def interne(scope, receive, send):
        if appels_internes is not None:
            appels_internes.append(scope.get("path"))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return interne


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserveur")


def _entetes(jeton: str | None = JETON) -> dict[str, str]:
    entetes = {"Content-Type": "application/json"}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    return entetes


def test_post_valide_reponse_explicite_sans_echo_id():
    """Un dépôt valide répond conversation_detectee/id_present ; l'ID n'est PAS
    écho par défaut (limiter l'exposition, l'ID reste au client local)."""
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL, "title": "Titre", "client_id": "abc"}, headers=_entetes())
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["statut"] == "ok"
            assert d["conversation_detectee"] is True
            assert d["id_present"] is True
            assert d["ttl_s"] == 300
            assert "conversation_id" not in d, "l'ID ne doit pas être écho par défaut"

    _courir(_t())
    dernier = registre.dernier_valide(ttl_s=300)
    assert dernier is not None
    assert dernier.url == URL
    assert dernier.titre == "Titre"
    assert dernier.client_id == "abc"


def test_post_accepte_libelle_compte_sans_le_loguer():
    """account_label (non secret, config local extension) est stocké avec le
    contexte ; il n'apparaît jamais dans la réponse ni dans le diagnostic."""
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            r = await c.post(
                CHEMIN,
                json={"url": URL, "account_label": "ChatGPT principal", "title": "T"},
                headers=_entetes(),
            )
            assert r.status_code == 200
            assert "account_label" not in r.json()

    _courir(_t())
    dernier = registre.dernier_valide(ttl_s=300)
    assert dernier is not None
    assert dernier.account_label == "ChatGPT principal"
    etat = registre.etat(ttl_s=300)
    assert "account_label" not in etat and "title" not in etat


def test_post_valide_echo_id_en_debug_explicite():
    """En mode débogage explicite (echo_id), l'ID de conversation est renvoyé."""
    registre = RegistreContexte()
    endpoint = ContexteEndpoint(jeton=JETON, ttl_s=300, registre=registre, echo_id=True)
    app = envelopper_application(_interne_factice(), endpoint)

    async def _t():
        async with _client(app) as c:
            r = await c.post(CHEMIN, json={"url": URL}, headers=_entetes())
            assert r.status_code == 200
            assert r.json()["conversation_id"] == UUID

    _courir(_t())


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


def test_methodes_non_autorisees_405():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            for methode in ("put", "delete", "patch"):
                r = await getattr(c, methode)(CHEMIN, headers=_entetes())
                assert r.status_code == 405, methode
            assert (await c.put(CHEMIN)).headers.get("allow") == "GET, POST"

    _courir(_t())


def test_get_diagnostic_necessite_jeton():
    app, _ = _app()

    async def _t():
        async with _client(app) as c:
            r = await c.get(CHEMIN)
            assert r.status_code == 401

    _courir(_t())


def test_get_diagnostic_avant_puis_apres_depot():
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            # Avant tout dépôt : pas de contexte, aucune fuite d'ID/URL.
            r = await c.get(CHEMIN, headers=_entetes())
            assert r.status_code == 200
            d = r.json()
            assert d["contexte_present"] is False
            assert d["id_present"] is False
            assert d["age_s"] is None
            assert d["raison"] == "contexte_absent"
            assert d["dernier_depot_s"] is None
            assert d["ttl_s"] == 300
            for cle in ("url", "conversation_id", "title"):
                assert cle not in d

            # Après dépôt d'une conversation valide : contexte présent, âge >= 0.
            r = await c.post(CHEMIN, json={"url": URL, "client_id": "chrome", "onglet_id": "tab42"}, headers=_entetes())
            assert r.status_code == 200
            r = await c.get(CHEMIN, headers=_entetes())
            assert r.status_code == 200
            d = r.json()
            assert d["contexte_present"] is True
            assert d["id_present"] is True
            assert d["raison"] == "contexte_actif"
            assert d["age_s"] >= 0
            assert "url" not in d and "conversation_id" not in d

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
    """Le propriétaire de l'onglet peut effacer son propre contexte."""
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            dep = await c.post(
                CHEMIN, json={"url": URL, "client_id": "abc", "onglet_id": "tab42"}, headers=_entetes()
            )
            assert dep.status_code == 200
            assert registre.dernier_valide() is not None
            r = await c.post(
                CHEMIN, json={"actif": False, "client_id": "abc", "onglet_id": "tab42"}, headers=_entetes()
            )
            assert r.status_code == 200
            assert r.json() == {"statut": "ok", "efface": 1}
            assert registre.dernier_valide() is None

    _courir(_t())


def test_actif_false_onglet_tiers_nefface_pas():
    """RÉGRESSION (cause racine du bug live) : une page ChatGPT sans
    conversation (accueil) qui envoie `actif:false` ne peut PAS effacer le
    contexte déposé par l'onglet de la conversation — même avec le bon
    client_id partagé."""
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            dep = await c.post(
                CHEMIN, json={"url": URL, "client_id": "chrome", "onglet_id": "tab-conversation"}, headers=_entetes()
            )
            assert dep.status_code == 200
            # Onglet différent (accueil), même client_id : refusé.
            r1 = await c.post(
                CHEMIN, json={"actif": False, "client_id": "chrome", "onglet_id": "tab-accueil"}, headers=_entetes()
            )
            assert r1.status_code == 200
            assert r1.json() == {"statut": "ok", "efface": 0}
            # Effacement sans identité d'onglet : refusé aussi.
            r2 = await c.post(CHEMIN, json={"actif": False, "client_id": "chrome"}, headers=_entetes())
            assert r2.status_code == 200
            assert r2.json() == {"statut": "ok", "efface": 0}
            # Sans client_id : aucun effet global.
            r3 = await c.post(CHEMIN, json={"actif": False}, headers=_entetes())
            assert r3.status_code == 200
            assert r3.json() == {"statut": "ok", "efface": 0}
            # Le contexte est toujours là.
            assert registre.dernier_valide() is not None

    _courir(_t())


def test_actif_false_legacy_sans_onglet_accepte_pour_depot_legacy():
    """Compat : un dépôt legacy (sans onglet) reste effaçable sans onglet."""
    registre = RegistreContexte()
    app, _ = _app(registre=registre)

    async def _t():
        async with _client(app) as c:
            assert (await c.post(CHEMIN, json={"url": URL, "client_id": "abc"}, headers=_entetes())).status_code == 200
            r = await c.post(CHEMIN, json={"actif": False, "client_id": "abc"}, headers=_entetes())
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
