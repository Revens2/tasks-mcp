"""Contexte « conversation IA » associé aux créations de tâches.

Une seule source en V2 : l'extension navigateur locale qui observe la
conversation ChatGPT active et publie son URL sur `POST /context/chatgpt`
(voir contexte_http.py). Le MCP conserve UNIQUEMENT le dernier contexte valide
reçu par source/client, en mémoire, avec un TTL court : pas d'historique de
navigation.

Quand `tasks_create` dispose d'un contexte récent, la fonction UNIQUE
`composer_notes_avec_contexte` produit les notes finales :

    https://chatgpt.com/c/<id>          ← URL en PREMIÈRE ligne (tapable)
    <description originale>
    ---
    Source : ChatGPT
    Compte : <label optionnel>
    Conversation : <titre optionnel>

Sécurité :
- URL acceptée uniquement si https://chatgpt.com/c/<id> (jamais /share/,
  jamais un autre hôte, jamais de query/fragment/userinfo, port interdit) ;
- le titre et le libellé de compte sont normalisés (contrôles retirés, espaces
  aplatis, bornés) : jamais interprétés, seulement insérés comme texte ;
- le timestamp du client est ignoré : `vu_le` est horodaté côté serveur ;
- rien n'est journalisé avec l'URL ou l'identifiant de conversation.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

# Bornes (tailles maximales volontairement très faibles).
TAILLE_MAX_URL = 2048
TAILLE_MAX_TITRE = 200
TAILLE_MAX_LIBELLE = 80  # libellé de compte (ex. « ChatGPT principal »)
TAILLE_MAX_CLIENT = 64
TTL_DEFAUT_S = 300

# Libellés d'affichage par source (la V2 ne connaît que ChatGPT ; d'autres
# sources pourront s'ajouter sans changer la logique de composition).
LIBELLES_SOURCE = {"chatgpt": "ChatGPT"}

# Identifiant de conversation : chaîne bornée sans séparateur dangereux
# (lettres/chiffres/tiret/souligné), longueur 8..100 — les IDs réels ChatGPT
# sont des UUID de 36 caractères ; aucune regex UUID stricte (risque de faux
# négatifs sur d'anciens formats), mais un segment trop court est refusé.
_MOTIF_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
# client_id : même alphabet, envoyé par l'extension (UUID ou libellé court).
_MOTIF_CLIENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# onglet_id : identité de l'onglet navigateur qui dépose le contexte (id Chrome
# numérique, ou « options » pour le bouton de test). Un effacement n'est accepté
# que s'il vient du MÊME onglet que le dépôt (anti-effacement croisé entre
# onglets — cause racine du bug « contexte effacé en boucle »).
_MOTIF_ONGLET = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOTE_AUTORISE = "chatgpt.com"
CHEMIN_CONVERSATION = "/c/"

# Fenêtre pendant laquelle un contexte expiré est rapporté comme « expiré »
# (raison du diagnostic) plutôt que « absent » (multiple du TTL).
FENETRE_EXPIRE_MULTIPLE = 2


class PayloadInvalide(ValueError):
    """Payload rejeté (code machine pour la réponse HTTP)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class ContexteChatGPT:
    """Contexte minimal et validé d'une conversation active.

    `source` prépare l'avenir (d'autres clients IA que ChatGPT) sans créer
    d'abstraction aujourd'hui : la V1 ne connaît que ``chatgpt``.
    """

    url: str
    conversation_id: str
    source: str = "chatgpt"
    titre: str | None = None
    account_label: str | None = None  # label de compte configuré localement (jamais un secret)
    client_id: str = "defaut"
    onglet_id: str = ""  # onglet navigateur propriétaire (anti-effacement croisé)
    vu_le: datetime | None = None  # horodaté par le serveur à l'enregistrement

    def enregistree_a(self, maintenant: datetime) -> "ContexteChatGPT":
        self.vu_le = maintenant
        return self


# ---------------------------------------------------------------------------
# Validation stricte de l'URL (aucune interprétation du contenu reçu)
# ---------------------------------------------------------------------------


def _est_ascii(texte: str) -> bool:
    try:
        texte.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def valider_url(brute: object) -> str:
    """Valide et normalise l'URL d'une conversation ChatGPT.

    Renvoie l'URL canonique ``https://chatgpt.com/c/<id>``. Lève
    `PayloadInvalide` (code ``url_invalide``) pour tout le reste :
    javascript:, autre hôte, /share/, sous-chemin, query, port, etc.
    """
    if not isinstance(brute, str):
        raise PayloadInvalide("url_invalide", "url absente ou non textuelle")
    url = brute.strip()
    if not url:
        raise PayloadInvalide("url_invalide", "url vide")
    if len(url) > TAILLE_MAX_URL:
        raise PayloadInvalide("url_invalide", "url trop longue")
    if not _est_ascii(url):
        raise PayloadInvalide("url_invalide", "url non ASCII refusée")

    parties = urlsplit(url)
    # https uniquement — pas de http, pas de javascript:, pas de données.
    if parties.scheme != "https":
        raise PayloadInvalide("url_invalide", "protocole https exigé")
    # Hôte exact (minuscules, point final toléré puis retiré) ; aucun port,
    # aucun userinfo, aucune query ni fragment : le formulaire est strict.
    hote = (parties.hostname or "").lower().rstrip(".")
    if hote != HOTE_AUTORISE:
        raise PayloadInvalide("url_invalide", "hôte non autorisé")
    if parties.port is not None:
        raise PayloadInvalide("url_invalide", "port interdit")
    if parties.username or parties.password:
        raise PayloadInvalide("url_invalide", "userinfo interdit")
    if parties.query or parties.fragment:
        raise PayloadInvalide("url_invalide", "query/fragment interdits")

    chemin = parties.path
    if not chemin.startswith(CHEMIN_CONVERSATION):
        raise PayloadInvalide("url_invalide", "chemin /c/<id> exigé")
    # Exactement deux segments : "c" puis l'identifiant (un slash final
    # résiduel est toléré puis éliminé par la normalisation).
    id_conversation = chemin[len(CHEMIN_CONVERSATION):]
    if not id_conversation:
        raise PayloadInvalide("url_invalide", "identifiant de conversation manquant")
    # /share/… et /s/… ne commencent pas par /c/, mais on refuse aussi
    # explicitement toute autre forme connue de partage.
    bas = id_conversation.rstrip("/")
    if not bas or "/" in bas or not _MOTIF_ID.match(bas):
        raise PayloadInvalide("url_invalide", "identifiant de conversation invalide")
    if bas in ("share", "s", "g"):
        raise PayloadInvalide("url_invalide", "lien de partage refusé")
    return f"https://{HOTE_AUTORISE}{CHEMIN_CONVERSATION}{bas}"


def _assainir_texte(brut: object, taille_max: int) -> str | None:
    """Texte sûr pour les notes : contrôles retirés, blancs aplatis, borné.

    Renvoie None si absent/vide après nettoyage. Ne lève jamais.
    """
    if not isinstance(brut, str):
        return None
    # Tout caractère de contrôle (dont \n et \r) devient une espace : pas
    # d'injection de lignes dans les notes, pas de collage de mots. Puis les
    # blancs sont aplatis.
    nettoye = "".join(" " if unicodedata.category(c) == "Cc" else c for c in brut)
    aplati = " ".join(nettoye.split())
    aplati = aplati.strip()
    if not aplati:
        return None
    return aplati[:taille_max]


def normaliser_titre(brut: object) -> str | None:
    """Titre de conversation sûr pour les notes (borné à TAILLE_MAX_TITRE)."""
    return _assainir_texte(brut, TAILLE_MAX_TITRE)


def normaliser_libelle(brut: object) -> str | None:
    """Libellé de compte sûr (borné à TAILLE_MAX_LIBELLE). Renvoie None si
    absent/vide — un libellé manquant ne bloque jamais la création de tâche."""
    return _assainir_texte(brut, TAILLE_MAX_LIBELLE)


def _normaliser_client_id(brut: object) -> str:
    if not isinstance(brut, str) or not _MOTIF_CLIENT.match(brut):
        return "defaut"
    return brut


def _normaliser_onglet_id(brut: object) -> str:
    if not isinstance(brut, str) or not _MOTIF_ONGLET.match(brut):
        return ""
    return brut


def contexte_depuis_payload(payload: object) -> ContexteChatGPT:
    """Contexte validé depuis le corps JSON reçu (dict).

    Lève `PayloadInvalide` avec un code machine. Les clés inconnues du corps
    sont ignorées (tolérance raisonnable), le contenu reçu n'est jamais exécuté
    ni interprété au-delà de la validation ci-dessus.
    """
    if not isinstance(payload, dict):
        raise PayloadInvalide("payload_invalide", "objet JSON attendu")
    url = valider_url(payload.get("url"))
    titre = normaliser_titre(payload.get("title"))
    account_label = normaliser_libelle(payload.get("account_label"))
    client_id = _normaliser_client_id(payload.get("client_id"))
    onglet_id = _normaliser_onglet_id(payload.get("onglet_id"))
    # Extraire l'id depuis l'URL canonique (source unique de vérité).
    id_conversation = url.rsplit("/", 1)[1]
    return ContexteChatGPT(
        url=url,
        conversation_id=id_conversation,
        titre=titre,
        account_label=account_label,
        client_id=client_id,
        onglet_id=onglet_id,
    )


# ---------------------------------------------------------------------------
# Registre mémoire (un seul « contexte récent », pas d'historique)
# ---------------------------------------------------------------------------


class RegistreContexte:
    """Dernier contexte valide par client + TTL.

    Conçu pour un processus unique (uvicorn worker unique du service
    tasks-mcp) : aucune persistance, aucune donnée de navigation conservée
    au-delà du TTL. Fil d'exécution protégé par un verrou.

    Propriété : chaque contexte est déposé par UN onglet (`onglet_id`). Un
    effacement n'est accepté que s'il provient du même onglet : une page
    ChatGPT sans conversation (accueil…) ne peut donc JAMAIS effacer le
    contexte d'une conversation ouverte dans un autre onglet.
    """

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._par_client: dict[str, ContexteChatGPT] = {}
        # Horodatages (unix) des dépôts par client, bornés : servent UNIQUEMENT
        # au diagnostic (raison absent/expiré, âge du dernier dépôt) — aucune
        # URL, aucun identifiant de conversation conservés ici.
        self._derniers_depots: dict[str, float] = {}
        self._max_depots_traces = 64

    def enregistrer(self, contexte: ContexteChatGPT, maintenant: datetime | None = None) -> None:
        avec_heure = contexte.enregistree_a(maintenant or datetime.now(timezone.utc))
        with self._verrou:
            self._par_client[avec_heure.client_id] = avec_heure
            self._derniers_depots[avec_heure.client_id] = avec_heure.vu_le.timestamp()
            if len(self._derniers_depots) > self._max_depots_traces:
                # Éviction du plus ancien (dict ordonné par insertion).
                self._derniers_depots.pop(next(iter(self._derniers_depots)))

    def effacer(self, client_id: str | None = None, onglet_id: str | None = None) -> int:
        """Efface le contexte d'un client, sous condition de propriété.

        - ``client_id`` est None → efface TOUT (usage interne uniquement,
          jamais exposé par l'endpoint HTTP) ;
        - sinon le contexte n'est retiré QUE si l'onglet demandeur est le
          propriétaire du dépôt (``onglet_id`` identique à celui enregistré) ;
        - un effacement sans ``onglet_id`` est refusé (0) lorsque le dépôt
          porte un onglet, et accepté seulement pour un dépôt legacy sans
          onglet — un onglet tiers ne peut jamais effacer le contexte d'un
          autre onglet.

        Retourne le nombre d'entrées retirées.
        """
        with self._verrou:
            if client_id is None:
                n = len(self._par_client)
                self._par_client.clear()
                return n
            contexte = self._par_client.get(client_id)
            if contexte is None:
                return 0
            if onglet_id is None:
                # Dépôt legacy sans onglet → effacement legacy toléré ; dépôt
                # porté par un onglet → refus (il faut l'onglet propriétaire).
                if contexte.onglet_id:
                    return 0
                del self._par_client[client_id]
                return 1
            if contexte.onglet_id and contexte.onglet_id != onglet_id:
                return 0
            del self._par_client[client_id]
            return 1

    def _purger(self, ttl_s: float, maintenant: datetime) -> None:
        limite = maintenant.timestamp() - max(0.0, ttl_s)
        perimes = [
            cid for cid, c in self._par_client.items()
            if c.vu_le is None or c.vu_le.timestamp() < limite
        ]
        for cid in perimes:
            self._par_client.pop(cid, None)

    def dernier_valide(self, ttl_s: float = TTL_DEFAUT_S, maintenant: datetime | None = None) -> ContexteChatGPT | None:
        """Contexte le plus récent encore frais, quel que soit le client.

        Si le contexte est trop vieux (TTL dépassé) → None : mieux vaut ne pas
        ajouter de lien qu'ajouter un mauvais lien. Le temps est mesuré côté
        serveur, jamais côté client.
        """
        maintenant = maintenant or datetime.now(timezone.utc)
        with self._verrou:
            self._purger(ttl_s, maintenant)
            if not self._par_client:
                return None
            return max(self._par_client.values(), key=lambda c: c.vu_le.timestamp())

    def _diagnostic(self, ttl_s: float, maintenant: datetime) -> dict:
        """État du registre pour le diagnostic — jamais d'URL ni d'ID.

        Retourne : contexte_present, age_s (contexte frais), raison
        (contexte_actif | contexte_expire | contexte_absent) et
        dernier_depot_s (âge du dernier dépôt reçu, toute source confondue).
        """
        maintenant = maintenant or datetime.now(timezone.utc)
        ttl = max(0.0, float(ttl_s))
        with self._verrou:
            self._purger(ttl, maintenant)
            maintenant_ts = maintenant.timestamp()
            dernier_depot_ts = max(self._derniers_depots.values(), default=None)
            dernier_depot_s = (
                round(max(0.0, maintenant_ts - dernier_depot_ts), 1)
                if dernier_depot_ts is not None else None
            )
            if self._par_client:
                plus_recent = max(self._par_client.values(), key=lambda c: c.vu_le.timestamp())
                age = max(0.0, maintenant_ts - plus_recent.vu_le.timestamp())
                return {
                    "contexte_present": True,
                    "age_s": round(age, 1),
                    "raison": "contexte_actif",
                    "dernier_depot_s": dernier_depot_s,
                }
            if dernier_depot_s is not None and dernier_depot_s <= ttl * FENETRE_EXPIRE_MULTIPLE:
                return {
                    "contexte_present": False,
                    "age_s": None,
                    "raison": "contexte_expire",
                    "dernier_depot_s": dernier_depot_s,
                }
            return {
                "contexte_present": False,
                "age_s": None,
                "raison": "contexte_absent",
                "dernier_depot_s": dernier_depot_s,
            }

    def etat(self, ttl_s: float = TTL_DEFAUT_S, maintenant: datetime | None = None) -> dict:
        """Diagnostic interne (GET /context/chatgpt) : présence, âge et raison.

        Ne renvoie NI l'URL NI l'ID de conversation, et aucun historique.
        """
        diag = self._diagnostic(ttl_s=ttl_s, maintenant=maintenant)
        return {
            "contexte_present": diag["contexte_present"],
            "age_s": diag["age_s"],
            "raison": diag["raison"],
            "dernier_depot_s": diag["dernier_depot_s"],
        }

    def raison(self, ttl_s: float = TTL_DEFAUT_S, maintenant: datetime | None = None) -> str:
        """Raison de l'absence d'un contexte frais, pour le journal interne :
        ``contexte_actif``, ``contexte_expire`` ou ``contexte_absent``."""
        return self._diagnostic(ttl_s=ttl_s, maintenant=maintenant)["raison"]


# ---------------------------------------------------------------------------
# Composition UNIQUE des notes finales (URL en tête + pied Source/Compte/Conversation)
# ---------------------------------------------------------------------------

SEPARATEUR = "---"

# Formes « ancien format » éventuellement encore présentes dans des notes
# fournies par un agent (ex. reprise d'une tâche existante) : on les retire
# pour ne JAMAIS dupliquer l'URL. Motif : [---] “Conversation ChatGPT :”
# [titre] URL, avec ou sans le séparateur.
_MOTIF_URL = r"https://chatgpt\.com/c/[A-Za-z0-9_-]+"
_MOTIF_BLOC_LEGACY = re.compile(
    r"(?:^|\n)[ \t]*---[ \t]*\n"
    r"[ \t]*Conversation ChatGPT[ \t]*:[^\n]*\n"
    r"(?:(?![ \t]*https://chatgpt\.com/c/)[^\n]*\n)?"
    r"[ \t]*" + _MOTIF_URL + r"[ \t]*(?=\n|$)",
    re.MULTILINE,
)
_MOTIF_BLOC_LEGACY_SANS_SEP = re.compile(
    r"(?:^|\n)[ \t]*Conversation ChatGPT[ \t]*:[^\n]*\n"
    r"(?:(?![ \t]*https://chatgpt\.com/c/)[^\n]*\n)?"
    r"[ \t]*" + _MOTIF_URL + r"[ \t]*(?=\n|$)",
    re.MULTILINE,
)
_MOTIF_LIGNE_LEGACY = re.compile(r"(?im)^[ \t]*Conversation ChatGPT[ \t]*:[ \t]*\n?")


def _corps_normalise(texte: str) -> str:
    """Réduit les blancs parasites (lignes vides répétées, extrémités) sans
    toucher au contenu des lignes de l'utilisateur."""
    lignes = [ln.rstrip() for ln in texte.split("\n")]
    sortie: list[str] = []
    vide_attendu = False
    for ln in lignes:
        if not ln.strip():
            vide_attendu = True
        else:
            if vide_attendu and sortie and sortie[-1] != "":
                sortie.append("")
            vide_attendu = False
            sortie.append(ln)
    return "\n".join(sortie).strip()


def _libelle_source(source: str) -> str:
    return LIBELLES_SOURCE.get(source, source or "inconnue")


def composer_notes_avec_contexte(notes: str | None, contexte: ContexteChatGPT) -> str:
    """Notes finales d'une tâche créée avec un contexte valide (fonction UNIQUE,
    utilisée par `tasks_create` — tous les agents obtiennent le même résultat).

    Format :

    ``https://chatgpt.com/c/<id>``         ← PREMIÈRE ligne, tapable côté iPhone

    ``<notes originales>``                  (description préservée, jamais altérée)

    ``---``
    ``Source : ChatGPT``
    ``Compte : <account_label>``            (omis si absent — jamais bloquant)
    ``Conversation : <titre>``              (omis si absent)

    Garanties :
    - l'URL apparaît exactement une fois (les blocs legacy et les doublons de
      l'URL présents dans les notes fournies sont retirés) ;
    - les notes originales sont conservées telles quelles (hors retrait de
      l'URL/du bloc automatique dupliqué) ;
    - notes vides → URL + pied de page.
    """
    corps = ""
    if notes:
        nettoye = _MOTIF_BLOC_LEGACY.sub("\n", notes)
        nettoye = _MOTIF_BLOC_LEGACY_SANS_SEP.sub("\n", nettoye)
        # Doublons bruts de l'URL (ex. agent qui l'a déjà collée) : on ne la
        # garde qu'en première ligne.
        nettoye = nettoye.replace(contexte.url, "")
        nettoye = _MOTIF_LIGNE_LEGACY.sub("", nettoye)
        corps = _corps_normalise(nettoye)

    lignes_pied = [SEPARATEUR, f"Source : {_libelle_source(contexte.source)}"]
    if contexte.account_label:
        lignes_pied.append(f"Compte : {contexte.account_label}")
    if contexte.titre:
        lignes_pied.append(f"Conversation : {contexte.titre}")
    pied = "\n".join(lignes_pied)

    parties = [contexte.url]
    if corps:
        parties.append(corps)
    parties.append(pied)
    return "\n\n".join(parties)
