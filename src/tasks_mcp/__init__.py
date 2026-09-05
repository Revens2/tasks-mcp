"""tasks-mcp : pont MCP <-> CalDAV (Radicale) pour les rappels Apple / tâches.

Serveur upstream streamable-http, sans couche d'auth (boucle locale uniquement) :
la passerelle tasks-gateway (OAuth colocalisé + jeton statique) est le seul point
d'entrée. Ce paquet porte :
- le client CalDAV (Radicale) ;
- la normalisation iCalendar -> modèle de tâche ;
- le miroir SQLite + journal d'audit (qui a changé quoi, quand, depuis quelle source) ;
- les outils MCP typés (lecture, écriture, vues, récent, historique).
"""

__version__ = "0.1.0"
