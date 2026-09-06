"""Contexte « conversation IA » associé aux créations de tâches.

V1 : une seule source — l'extension navigateur locale qui observe la
conversation ChatGPT active et publie son URL sur `POST /context/chatgpt`
(voir contexte_http.py). Le MCP conserve UNIQUEMENT le dernier contexte valide
reçu par source/client, en mémoire, avec un TTL court : pas d'historique de
navigation. Quand `tasks_create` reçoit un titre/des notes et qu'un contexte
récent existe, un bloc `Conversation ChatGPT : …` est ajouté aux notes.

Sécurité :
- URL acceptée uniquement si https://chatgpt.com/c/<id> (jamais /share/,
  jamais un autre hôte, jamais de query/fragment/userinfo, port interdit) ;
- le titre est normalisé (contrôles retirés, espaces aplatis, borné) : il n'est
  jamais interprété, seulement inséré comme ligne de texte dans les notes ;
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
TAILLE_MAX_CLIENT = 64
TTL_DEFAUT_S = 300

# Identifiant de conversation : chaîne bornée sans séparateur dangereux
# (lettres/chiffres/tiret/souligné). Le chemin complet est vérifié plus bas.
_MOTIF_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
# client_id : même alphabet, envoyé par l'extension (UUID ou libellé court).
_MOTIF_CLIENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOTE_AUTORISE = "chatgpt.com"
CHEMIN_CONVERSATION = "/c/"


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
    client_id: str = "defaut"
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


def normaliser_titre(brut: object) -> str | None:
    """Titre sûr pour les notes : contrôles retirés, blancs aplatis, borné.

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
    return aplati[:TAILLE_MAX_TITRE]


def _normaliser_client_id(brut: object) -> str:
    if not isinstance(brut, str) or not _MOTIF_CLIENT.match(brut):
        return "defaut"
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
    client_id = _normaliser_client_id(payload.get("client_id"))
    # Extraire l'id depuis l'URL canonique (source unique de vérité).
    id_conversation = url.rsplit("/", 1)[1]
    return ContexteChatGPT(
        url=url,
        conversation_id=id_conversation,
        titre=titre,
        client_id=client_id,
    )


# ---------------------------------------------------------------------------
# Registre mémoire (un seul « contexte récent », pas d'historique)
# ---------------------------------------------------------------------------


class RegistreContexte:
    """Dernier contexte valide par client + TTL.

    Conçu pour un processus unique (uvicorn worker unique du service
    tasks-mcp) : aucune persistance, aucune donnée de navigation conservée
    au-delà du TTL. Fil d'exécution protégé par un verrou.
    """

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._par_client: dict[str, ContexteChatGPT] = {}

    def enregistrer(self, contexte: ContexteChatGPT, maintenant: datetime | None = None) -> None:
        avec_heure = contexte.enregistree_a(maintenant or datetime.now(timezone.utc))
        with self._verrou:
            self._par_client[avec_heure.client_id] = avec_heure

    def effacer(self, client_id: str | None = None) -> int:
        """Efface le contexte d'un client (ou de tous si client_id est None).

        Retourne le nombre d'entrées retirées. Utilisé quand l'extension
        constate que l'onglet actif a quitté une conversation.
        """
        with self._verrou:
            if client_id is None:
                n = len(self._par_client)
                self._par_client.clear()
                return n
            n = 1 if self._par_client.pop(client_id, None) is not None else 0
            return n

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


# ---------------------------------------------------------------------------
# Bloc « Conversation ChatGPT » ajouté aux notes
# ---------------------------------------------------------------------------

SEPARATEUR = "---"
MARQUEUR = "Conversation ChatGPT :"


def bloc_notes(contexte: ContexteChatGPT) -> str:
    """Bloc texte ajouté aux notes (jamais de lien `share`)."""
    lignes = [MARQUEUR]
    if contexte.titre:
        lignes.append(contexte.titre)
    lignes.append(contexte.url)
    return "\n".join(lignes)


def notes_avec_contexte(notes: str | None, contexte: ContexteChatGPT) -> str:
    """Ajoute le bloc aux notes sans écraser ni dupliquer.

    - notes existantes → notes + ``\\n\\n---\\n<bloc>`` ;
    - pas de notes → bloc seul ;
    - si les notes contiennent déjà l'URL de CE contexte (bloc présent),
      elles sont rendues telles quelles (aucune duplication).
    """
    bloc = bloc_notes(contexte)
    if notes:
        if contexte.url in notes:
            return notes
        return f"{notes}\n\n{SEPARATEUR}\n{bloc}"
    return bloc
