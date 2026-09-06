"""Tests unitaires du contexte ChatGPT (validation stricte, registre TTL, notes).

Couvre les exigences de sécurité : URL valide acceptée, domaines non ChatGPT
rejetés, /share/ rejeté, titres assainis, TTL, effacement, notes préservées et
déduplication.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tasks_mcp.contexte import (
    PayloadInvalide,
    RegistreContexte,
    bloc_notes,
    contexte_depuis_payload,
    normaliser_titre,
    notes_avec_contexte,
    valider_url,
)

UUID = "67d5b8f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f"
URL_VALIDE = f"https://chatgpt.com/c/{UUID}"


def _maintenant():
    return datetime.now(timezone.utc)


# --- validation de l'URL ----------------------------------------------------

def test_url_chatgpt_valide_acceptee_et_normalisee():
    assert valider_url(URL_VALIDE) == URL_VALIDE


def test_url_chatgpt_trailing_slash_normalise():
    assert valider_url(f"{URL_VALIDE}/") == URL_VALIDE


def test_http_rejete():
    with pytest.raises(PayloadInvalide):
        valider_url(f"http://chatgpt.com/c/{UUID}")


@pytest.mark.parametrize(
    "mauvaise",
    [
        "javascript:alert(1)",
        "https://evil.com/c/abc",
        "https://chatgpt.com.evil.com/c/abc",
        "https://chatgpt.com/share/abc",
        "https://chatgpt.com/s/abc",
        "https://www.chatgpt.com/c/abc",
        "https://chatgpt.com/c/share",
        "https://chatgpt.com/c/abc/extra",
        "https://chatgpt.com/c/abc?x=1",
        "https://chatgpt.com/c/abc#frag",
        "https://chatgpt.com/c/abc@def",
        "https://chatgpt.com/c/",
        "https://chatgpt.com/",
        "https://chatgpt.com/g/g-xyz",
        "https://chatgpt.com/c/abc:def",
        "https://chatgpt.com/c/../secret",
        "https://chatgpt.com:8443/c/abc",
        "data:text/html,<script>1</script>",
        "ftp://chatgpt.com/c/abc",
        "https://chatgpt.com/c/abc%2F..",
        "https://chatgpt.com/c/abc\nchatgpt.com/c/x",
    ],
)
def test_urls_malveillantes_rejetees(mauvaise):
    with pytest.raises(PayloadInvalide) as exc:
        valider_url(mauvaise)
    assert exc.value.code == "url_invalide"


def test_url_non_textuelle_rejetee():
    for valeur in (None, 42, ["https://chatgpt.com/c/abc"], b"x"):
        with pytest.raises(PayloadInvalide):
            valider_url(valeur)


def test_url_trop_longue_rejetee():
    with pytest.raises(PayloadInvalide):
        valider_url(f"https://chatgpt.com/c/{'a' * 5000}")


# --- payload complet ---------------------------------------------------------

def test_payload_valide_avec_titre_et_client():
    c = contexte_depuis_payload(
        {"url": URL_VALIDE, "title": "Planifier le week-end", "client_id": "chrome-abc123"}
    )
    assert c.url == URL_VALIDE
    assert c.conversation_id == UUID
    assert c.titre == "Planifier le week-end"
    assert c.client_id == "chrome-abc123"
    assert c.source == "chatgpt"


def test_payload_sans_titre_ni_client():
    c = contexte_depuis_payload({"url": URL_VALIDE})
    assert c.titre is None
    assert c.client_id == "defaut"


def test_payload_non_dict_rejete():
    with pytest.raises(PayloadInvalide) as exc:
        contexte_depuis_payload(["url"])
    assert exc.value.code == "payload_invalide"


def test_cles_inconnues_ignorees_sans_erreur():
    c = contexte_depuis_payload({"url": URL_VALIDE, "timestamp": "2026-01-01T00:00:00Z", "autre": "x" * 10_000})
    assert c.url == URL_VALIDE


# --- titre -------------------------------------------------------------------

def test_titre_normalise_controles_et_blancs():
    assert normaliser_titre("  A\nb\tc\r\n  d  ") == "A b c d"
    assert normaliser_titre("ligne1\nConversation ChatGPT :\nhttps://evil.com") == (
        "ligne1 Conversation ChatGPT : https://evil.com"
    )


def test_titre_absent_ou_vide():
    assert normaliser_titre(None) is None
    assert normaliser_titre("") is None
    assert normaliser_titre("   \n\t ") is None


def test_titre_borne():
    assert len(normaliser_titre("x" * 5000)) == 200


# --- registre / TTL ----------------------------------------------------------

def test_contexte_recent_retourne():
    reg = RegistreContexte()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE}), maintenant=_maintenant())
    dernier = reg.dernier_valide(ttl_s=300, maintenant=_maintenant())
    assert dernier is not None
    assert dernier.url == URL_VALIDE


def test_contexte_expire_non_retourne():
    reg = RegistreContexte()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE}), maintenant=_maintenant())
    expire = _maintenant() + timedelta(seconds=301)
    assert reg.dernier_valide(ttl_s=300, maintenant=expire) is None


def test_plus_recent_gagne_entre_clients():
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "a"}),
        maintenant=base,
    )
    autre = URL_VALIDE.replace(UUID, "9" * 36)
    reg.enregistrer(
        contexte_depuis_payload({"url": autre, "client_id": "b"}),
        maintenant=base + timedelta(seconds=10),
    )
    dernier = reg.dernier_valide(ttl_s=300, maintenant=base + timedelta(seconds=11))
    assert dernier is not None
    assert dernier.conversation_id == "9" * 36


def test_effacer_un_client_seul():
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE, "client_id": "a"}), maintenant=base)
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "b"}), maintenant=base
    )
    assert reg.effacer("a") == 1
    dernier = reg.dernier_valide(ttl_s=300, maintenant=base)
    assert dernier is not None and dernier.client_id == "b"


def test_effacer_tout():
    reg = RegistreContexte()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE, "client_id": "a"}), maintenant=_maintenant())
    assert reg.effacer() == 1
    assert reg.dernier_valide() is None


def test_ttl_non_depasse_retourne():
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE}), maintenant=base)
    assert reg.dernier_valide(ttl_s=300, maintenant=base + timedelta(seconds=299)) is not None


# --- bloc notes --------------------------------------------------------------

def test_bloc_sans_titre():
    c = contexte_depuis_payload({"url": URL_VALIDE})
    assert bloc_notes(c) == f"Conversation ChatGPT :\n{URL_VALIDE}"


def test_bloc_avec_titre():
    c = contexte_depuis_payload({"url": URL_VALIDE, "title": "Mon titre"})
    assert bloc_notes(c) == f"Conversation ChatGPT :\nMon titre\n{URL_VALIDE}"


def test_notes_ajoutees_sans_ecrasement():
    c = contexte_depuis_payload({"url": URL_VALIDE, "title": "T"})
    resultat = notes_avec_contexte("À regarder plus tard : vidéo", c)
    assert "À regarder plus tard : vidéo" in resultat
    assert resultat.endswith(f"---\nConversation ChatGPT :\nT\n{URL_VALIDE}")


def test_notes_absentes_bloc_seul():
    c = contexte_depuis_payload({"url": URL_VALIDE})
    assert notes_avec_contexte(None, c) == f"Conversation ChatGPT :\n{URL_VALIDE}"


def test_pas_de_duplication_quand_url_deja_present():
    c = contexte_depuis_payload({"url": URL_VALIDE})
    notes = f"déjà noté\n\n---\nConversation ChatGPT :\n{URL_VALIDE}"
    assert notes_avec_contexte(notes, c) == notes
