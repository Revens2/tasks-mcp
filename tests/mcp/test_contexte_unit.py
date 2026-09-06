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
    composer_notes_avec_contexte,
    contexte_depuis_payload,
    normaliser_libelle,
    normaliser_titre,
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
        # IDs trop courts : la validation exige une longueur cohérente (>= 8).
        "https://chatgpt.com/c/abc",
        "https://chatgpt.com/c/1234567",
        "https://chatgpt.com/c/" + "a" * 7,
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


def test_id_court_mais_longueur_minimale_accepte():
    # 8 caractères = borne basse (UUID réels = 36) : accepté sans regex UUID.
    id_court = "a" * 8
    assert valider_url(f"https://chatgpt.com/c/{id_court}") == f"https://chatgpt.com/c/{id_court}"


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
    assert c.account_label is None
    assert c.client_id == "defaut"


def test_payload_avec_libelle_compte():
    c = contexte_depuis_payload(
        {"url": URL_VALIDE, "account_label": "ChatGPT principal"}
    )
    assert c.account_label == "ChatGPT principal"


def test_libelle_compte_normalise_et_borne():
    assert normaliser_libelle("  ChatGPT\nsecondaire ") == "ChatGPT secondaire"
    assert normaliser_libelle(None) is None
    assert normaliser_libelle("   ") is None
    assert len(normaliser_libelle("x" * 5000)) == 80


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
    # Dépôts legacy (sans onglet) : effacement legacy du même client accepté.
    assert reg.effacer("a") == 1
    dernier = reg.dernier_valide(ttl_s=300, maintenant=base)
    assert dernier is not None and dernier.client_id == "b"


def test_effacer_onglet_tiers_refuse():
    """RÈGLE ANTI-EFFACEMENT CROISÉ : un onglet tiers (page ChatGPT sans
    conversation) ne peut PAS effacer le contexte déposé par un autre onglet
    — cause racine du bug « contexte effacé en boucle »."""
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "chrome", "onglet_id": "tab42"}),
        maintenant=base,
    )
    # Onglet différent (tab43) ou effacement sans identité : refusés.
    assert reg.effacer("chrome", "tab43") == 0
    assert reg.effacer("chrome") == 0
    assert reg.dernier_valide(ttl_s=300, maintenant=base) is not None


def test_effacer_onglet_proprietaire_accepte():
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "chrome", "onglet_id": "tab42"}),
        maintenant=base,
    )
    assert reg.effacer("chrome", "tab42") == 1
    assert reg.dernier_valide(ttl_s=300, maintenant=base) is None


def test_effacer_onglet_sans_client_refuse():
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "chrome", "onglet_id": "tab42"}),
        maintenant=base,
    )
    assert reg.effacer("autre-client", "tab42") == 0
    assert reg.dernier_valide(ttl_s=300, maintenant=base) is not None


def test_heartbeat_rafraichit_le_ttl():
    """Un dépôt (heartbeat) régulier repousse l'expiration : à T+590 avec un
    TTL de 300 s, le contexte est encore frais grâce au dépôt de T+290."""
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "chrome", "onglet_id": "tab42"}),
        maintenant=base,
    )
    reg.enregistrer(
        contexte_depuis_payload({"url": URL_VALIDE, "client_id": "chrome", "onglet_id": "tab42"}),
        maintenant=base + timedelta(seconds=290),
    )
    assert reg.dernier_valide(ttl_s=300, maintenant=base + timedelta(seconds=590)) is not None


def test_contexte_enregistre_porte_son_onglet():
    c = contexte_depuis_payload(
        {"url": URL_VALIDE, "client_id": "chrome", "onglet_id": "tab42"}
    )
    assert c.onglet_id == "tab42"
    assert contexte_depuis_payload({"url": URL_VALIDE}).onglet_id == ""


def test_onglet_id_invalide_ignore():
    c = contexte_depuis_payload(
        {"url": URL_VALIDE, "onglet_id": "tab:42 avec des espaces!!!"}
    )
    assert c.onglet_id == ""


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


def test_etat_vide():
    reg = RegistreContexte()
    etat = reg.etat(ttl_s=300, maintenant=_maintenant())
    assert etat == {
        "contexte_present": False,
        "age_s": None,
        "raison": "contexte_absent",
        "dernier_depot_s": None,
    }


def test_etat_apres_depot_sans_fuite():
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE}), maintenant=base)
    etat = reg.etat(ttl_s=300, maintenant=base + timedelta(seconds=2))
    assert etat["contexte_present"] is True
    assert etat["raison"] == "contexte_actif"
    assert 0 < etat["age_s"] <= 2.5
    assert etat["dernier_depot_s"] == etat["age_s"]
    # Jamais d'URL/ID dans le diagnostic.
    assert "url" not in etat and "conversation_id" not in etat


def test_etat_expire():
    """Un contexte déposé puis expiré est rapporté « contexte_expire » (et non
    absent) pendant la fenêtre de diagnostic : l'extension peut distinguer
    « aucun contexte » de « contexte expiré »."""
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE}), maintenant=base)
    etat = reg.etat(ttl_s=300, maintenant=base + timedelta(seconds=301))
    assert etat["contexte_present"] is False
    assert etat["age_s"] is None
    assert etat["raison"] == "contexte_expire"
    assert etat["dernier_depot_s"] == 301.0


def test_etat_absent_apres_longue_silence():
    """Passé la fenêtre de diagnostic (2 × TTL), un vieux dépôt redevient
    « contexte_absent » (plus rien d'exploitable)."""
    reg = RegistreContexte()
    base = _maintenant()
    reg.enregistrer(contexte_depuis_payload({"url": URL_VALIDE}), maintenant=base)
    etat = reg.etat(ttl_s=300, maintenant=base + timedelta(seconds=900))
    assert etat == {
        "contexte_present": False,
        "age_s": None,
        "raison": "contexte_absent",
        "dernier_depot_s": 900.0,
    }


# --- composition des notes (URL en tête + pied Source/Compte/Conversation) ----


def _contexte(**extra):
    champ = {"url": URL_VALIDE}
    champ.update(extra)
    return contexte_depuis_payload(champ)


def test_notes_avec_contexte_url_en_premiere_ligne():
    """Critère principal : l'URL est la première ligne des notes."""
    c = _contexte(title="Compatibilité câble iPhone 15 Pro", account_label="ChatGPT principal")
    resultat = composer_notes_avec_contexte("Acheter un câble USB-C adapté.", c)
    assert resultat.startswith("https://chatgpt.com/c/")
    assert resultat == (
        f"{URL_VALIDE}\n\n"
        "Acheter un câble USB-C adapté.\n\n"
        "---\nSource : ChatGPT\nCompte : ChatGPT principal\n"
        "Conversation : Compatibilité câble iPhone 15 Pro"
    )


def test_notes_vides_avec_contexte():
    c = _contexte(title="T", account_label="A")
    assert composer_notes_avec_contexte(None, c) == (
        f"{URL_VALIDE}\n\n---\nSource : ChatGPT\nCompte : A\nConversation : T"
    )


def test_titre_absent_ligne_conversation_omise():
    c = _contexte(account_label="ChatGPT principal")
    resultat = composer_notes_avec_contexte("description", c)
    assert "Conversation :" not in resultat
    assert "Compte : ChatGPT principal" in resultat
    assert resultat.startswith(URL_VALIDE)


def test_compte_absent_ligne_compte_omise():
    c = _contexte(title="Mon titre")
    resultat = composer_notes_avec_contexte("description", c)
    assert "Compte :" not in resultat
    assert "Conversation : Mon titre" in resultat
    assert "Source : ChatGPT" in resultat


def test_titre_et_compte_absents_pied_minimal():
    c = _contexte()
    resultat = composer_notes_avec_contexte("description", c)
    assert resultat == f"{URL_VALIDE}\n\ndescription\n\n---\nSource : ChatGPT"


def test_notes_originales_preservees_et_non_alterees():
    c = _contexte(title="T")
    description = "Ligne 1\n\nLigne 2 — avec ponctuation ! (parenthèses) [x]"
    resultat = composer_notes_avec_contexte(description, c)
    assert "Ligne 1" in resultat and "Ligne 2 — avec ponctuation ! (parenthèses) [x]" in resultat


def test_unicode_accents_conserves_dans_titre_compte_et_notes():
    c = _contexte(
        title="Été : vérifier l'épaisseur du câble USB‑C 🔌",
        account_label="Compte principal éèàçü",
    )
    resultat = composer_notes_avec_contexte("Ça marche déjà très bien : é à ô ü.", c)
    assert "Été : vérifier l'épaisseur du câble USB‑C 🔌" in resultat
    assert "Compte : Compte principal éèàçü" in resultat
    assert "é à ô ü" in resultat


def test_longue_description_conservee():
    c = _contexte(title="T")
    longue = "\n".join(f"Paragraphe {i} : " + "mots ".join(str(j) for j in range(20)) for i in range(50))
    resultat = composer_notes_avec_contexte(longue, c)
    assert resultat.startswith(URL_VALIDE)
    assert "Paragraphe 49 :" in resultat


def test_url_dupliquee_dans_les_notes_retiree_une_seule_occurrence():
    """Pas de double URL (URL URL description) : l'URL n'apparaît qu'en tête."""
    c = _contexte(title="T")
    notes = f"déjà noté\n{URL_VALIDE}\nencore un peu"
    resultat = composer_notes_avec_contexte(notes, c)
    assert resultat.count(URL_VALIDE) == 1
    assert resultat.startswith(URL_VALIDE + "\n\ndéjà noté")
    assert resultat.endswith("encore un peu\n\n---\nSource : ChatGPT\nConversation : T")


def test_ancien_format_legacy_retire_pas_de_doublon():
    """Des notes portant l'ancien bloc (Conversation ChatGPT : … + URL) ne
    produisent pas de double URL ni de reliquat du bloc legacy."""
    c = _contexte(title="T")
    legacy = f"description d'origine\n\n---\nConversation ChatGPT :\nMon ancien titre\n{URL_VALIDE}"
    resultat = composer_notes_avec_contexte(legacy, c)
    assert resultat.count(URL_VALIDE) == 1
    assert "Conversation ChatGPT :" not in resultat
    assert "Mon ancien titre" not in resultat
    assert resultat.startswith(URL_VALIDE + "\n\ndescription d'origine")
    assert "Conversation : T" in resultat


def test_ancien_format_sans_separateur_retire():
    c = _contexte(title="T")
    legacy = f"description\n\nConversation ChatGPT :\n{URL_VALIDE}"
    resultat = composer_notes_avec_contexte(legacy, c)
    assert resultat.count(URL_VALIDE) == 1
    assert "Conversation ChatGPT :" not in resultat
    assert resultat.startswith(f"{URL_VALIDE}\n\ndescription")


def test_url_d_une_autre_conversation_conservee():
    """Seule l'URL DE CE contexte est dédupliquée ; un lien vers une autre
    conversation présent dans les notes n'est pas touché."""
    autre = "https://chatgpt.com/c/aaaaaaaa-1111-2222-3333-444444444444"
    c = _contexte(title="T")
    notes = f"Voir aussi {autre} pour l'autre sujet"
    resultat = composer_notes_avec_contexte(notes, c)
    assert resultat.count(URL_VALIDE) == 1
    assert autre in resultat
    assert resultat.startswith(URL_VALIDE)
