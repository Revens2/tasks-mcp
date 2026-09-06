#!/usr/bin/env python3
"""Passage de finition sur tasks_gateway (après copie-adaptation). Non idempotent. """
from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
PAQUET = RACINE / "src" / "tasks_gateway"
TESTS = RACINE / "tests" / "gateway"

REMPLACEMENTS = [
    # résidus cosmétiques (mots entiers uniquement, aucun identifiant)
    ("\nGoogle. Saisissez votre phrase", "\nSaisissez votre phrase"),
    ("a vos calendriers\n", "sur vos\n"),
    ("passerelle Calendar", "passerelle tasks"),
    ("des comptes Google", "d'un outil d'administration"),
    ("de la passerelle tasks.", "de la passerelle tasks."),
    ("site `calendar-mcp`, 443 public", "vhost tasks-mcp (NetBird + futur public)"),
]


def normaliser_et_remplacer(fichier: Path) -> None:
    texte = fichier.read_text(encoding="utf-8-sig")
    texte = texte.replace("\r\n", "\n").replace("\r", "\n")
    for ancien, nouveau in REMPLACEMENTS:
        texte = texte.replace(ancien, nouveau)
    fichier.write_text(texte, encoding="utf-8")


for fichier in list(PAQUET.glob("*.py")) + list(TESTS.glob("*.py")):
    normaliser_et_remplacer(fichier)
    print("nettoyé :", fichier.name)

# ---------------------------------------------------------------------------
# __init__.py : description propre (l'upstream n'est plus le conteneur Google).
# ---------------------------------------------------------------------------
(PAQUET / "__init__.py").write_text(
    '''"""Passerelle d'authentification du MCP tâches et rappels (CalDAV).

Copie-adaptation du pattern `vault-mcp` / `calendar-mcp-gateway` (adr/0015, adr/0016) :
serveur d'autorisation OAuth 2.1 colocalisé (SDK python `mcp`), page de consentement,
jeton Bearer statique pour les CLI, et proxy transparent vers l'upstream tasks-mcp
(127.0.0.1:8791) sur `/mcp`. La passerelle n'est PAS un serveur MCP : elle valide le
jeton puis relaie le trafic Streamable HTTP tel quel.
"""

__version__ = "1.0.0"
''',
    encoding="utf-8",
)

# ---------------------------------------------------------------------------
# upstream.py : docstring + injection de l'acteur authentifié.
# ---------------------------------------------------------------------------
fichier_upstream = PAQUET / "upstream.py"
texte = fichier_upstream.read_text(encoding="utf-8")
texte = texte.replace(
    '"""Proxy transparent vers le serveur MCP upstream (conteneur `calendar-mcp`, 127.0.0.1:8791).\n\nLa passerelle n\'est PAS un serveur MCP de plein exercice : elle valide le jeton d\'acces\n(OAuth ou Bearer statique) puis relaie tel quel le trafic Streamable HTTP vers l\'upstream.\nLes sessions MCP restent la propriete de l\'upstream : l\'en-tete `mcp-session-id` est\ntransmis sans modification dans les deux sens, ce qui rend le proxy invisible pour le\nprotocole.\n\nSeule reecriture volontaire : la reponse `tools/list` est filtree pour retirer\n`manage-accounts` (outil d\'administration des comptes Google, enregistre par l\'upstream\nindependamment de `ENABLED_TOOLS`). Tout le reste passe verbatim.\n"""',
    '"""Proxy transparent vers le serveur MCP upstream (tasks-mcp, 127.0.0.1:8791).\n\nLa passerelle n\'est PAS un serveur MCP de plein exercice : elle valide le jeton d\'acces\n(OAuth ou Bearer statique) puis relaie tel quel le trafic Streamable HTTP vers l\'upstream.\nLes sessions MCP restent la propriete de l\'upstream : l\'en-tete `mcp-session-id` est\ntransmis sans modification dans les deux sens, ce qui rend le proxy invisible pour le\nprotocole.\n\nDeux ajouts volontaires :\n- injection des en-tetes internes `x-tasks-mcp-acteur` / `x-tasks-mcp-mode` (client_id\n  du jeton valide) pour que l\'upstream journalise qui a agi ; jamais transmis depuis\n  l\'exterieur (la liste blanche d\'en-tetes ne les accepte pas en entree) ;\n- filtre optionnel `tools/list` (inactif en V1 : OUTILS_RETIRES est vide).\n"""',
)
texte = texte.replace(
    "async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:\n        if scope[\"type\"] != \"http\":\n            return\n        methode = scope[\"method\"]\n        chemin = scope.get(\"path\", \"/mcp\")\n        query = scope.get(\"query_string\", b\"\").decode(\"latin-1\")\n        url = f\"{self._base_url}{chemin}\" + (f\"?{query}\" if query else \"\")\n",
    "    @staticmethod\n    def _identite(scope: Scope) -> str | None:\n        \"\"\"client_id du jeton valide (peuplé par AuthenticationMiddleware).\"\"\"\n        utilisateur = scope.get(\"user\")\n        jeton = getattr(utilisateur, \"access_token\", None)\n        client_id = getattr(jeton, \"client_id\", None)\n        if not isinstance(client_id, str) or not client_id:\n            return None\n        return client_id[:128]\n\n    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:\n        if scope[\"type\"] != \"http\":\n            return\n        methode = scope[\"method\"]\n        chemin = scope.get(\"path\", \"/mcp\")\n        query = scope.get(\"query_string\", b\"\").decode(\"latin-1\")\n        url = f\"{self._base_url}{chemin}\" + (f\"?{query}\" if query else \"\")\n",
)
texte = texte.replace(
    "        try:\n            requete = self._http().build_request(\n                methode, url, headers=_entetes(scope), content=corps\n            )\n",
    "        entetes = _entetes(scope)\n        identite = self._identite(scope)\n        if identite:\n            entetes[\"x-tasks-mcp-acteur\"] = identite\n            entetes[\"x-tasks-mcp-mode\"] = \"cli\" if identite == \"tasks-mcp-cli-statique\" else \"oauth\"\n\n        try:\n            requete = self._http().build_request(\n                methode, url, headers=entetes, content=corps\n            )\n",
)
fichier_upstream.write_text(texte, encoding="utf-8")
print("upstream.py : acteur injecté.")

# ---------------------------------------------------------------------------
# server.py : docstring d'entrée adaptée.
# ---------------------------------------------------------------------------
fichier_server = PAQUET / "server.py"
texte = fichier_server.read_text(encoding="utf-8")
texte = texte.replace(
    "Ecoute sur la boucle locale uniquement : nginx (vhost tasks-mcp (NetBird + futur public)) est le\nseul point d'entree depuis l'exterieur.",
    "Ecoute sur la boucle locale uniquement : nginx (vhost tasks-mcp) est le\nseul point d'entree depuis l'exterieur.",
)
texte = texte.replace("L'upstream (conteneur) n'est joint qu'en\n127.0.0.1.", "L'upstream tasks-mcp n'est joint qu'en\n127.0.0.1 (8791).")
fichier_server.write_text(texte, encoding="utf-8")

print("finition terminée.")
