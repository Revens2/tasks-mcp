#!/usr/bin/env python3
"""Transformation ponctuelle : calendar_gateway -> tasks_gateway (copie-adaptation).

Non réexécutable tel quel sur des fichiers déjà transformés (idempotence non visée).
Conservé pour mémoire dans le dépôt ; les sources sont dans src/tasks_gateway/.
"""
from __future__ import annotations

from pathlib import Path

RACINE = Path(__file__).resolve().parents[2] / "src" / "tasks_gateway"
TEST = Path(__file__).resolve().parents[2] / "tests" / "gateway" / "test_gateway.py"

REMPLACEMENTS = [
    ("calendar_gateway", "tasks_gateway"),
    ("CALENDAR_MCP_", "TASKS_MCP_"),
    ("calendar:ecriture", "tasks:ecriture"),
    ("calendar:lecture", "tasks:lecture"),
    ("/opt/calendar-mcp/oauth", "/srv/tasks/data/oauth"),
    ("calendar-mcp-cli-statique", "tasks-mcp-cli-statique"),
    ("calendar-mcp-gateway", "tasks-mcp-gateway"),
    ("calendriers Google", "tâches et rappels"),
    ("Google Calendar", "tâches et rappels"),
    ("MCP Google Calendar", "MCP tâches (CalDAV)"),
    ("127.0.0.1:3000", "127.0.0.1:8791"),
    ("PORT_PAR_DEFAUT = 8790", "PORT_PAR_DEFAUT = 8792"),
    (
        'OUTILS_RETIRES = {"manage-accounts"}  # reserve a l\'administration locale',
        "OUTILS_RETIRES: set[str] = set()  # aucun outil masque (V1)",
    ),
    (
        'resource_name="tâches et rappels MCP (passerelle)",',
        'resource_name="tâches et rappels MCP (passerelle CalDAV)",',
    ),
]

for fichier in list(RACINE.glob("*.py")) + [TEST]:
    texte = fichier.read_text(encoding="utf-8")
    for ancien, nouveau in REMPLACEMENTS:
        texte = texte.replace(ancien, nouveau)
    fichier.write_text(texte, encoding="utf-8")
    print(f"transformé : {fichier.name}")

# Ajustements ciblés non couverts par les remplacements globaux.
app = RACINE / "app.py"
texte = app.read_text(encoding="utf-8")
texte = texte.replace(
    "Passerelle d'authentification du MCP tâches et rappels (CalDAV).\n\n"
    "Le serveur upstream `nspady/google-calendar-mcp` (conteneur non modifie) n'a aucune",
    "PAS DE SOURCE",
)
app.write_text(texte, encoding="utf-8")
print("remarque : vérifier les docstrings restantes manuellement")
