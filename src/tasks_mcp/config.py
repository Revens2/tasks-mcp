"""Configuration du service tasks-mcp (issue de l'environnement)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _requise(nom: str) -> str:
    valeur = os.environ.get(nom, "").strip()
    if not valeur:
        raise RuntimeError(f"{nom} absent de l'environnement")
    return valeur


@dataclass(slots=True)
class Config:
    caldav_url: str
    caldav_user: str
    caldav_password: str
    data_dir: Path = Path("/srv/tasks/data")
    trash_list: str = "Corbeille"
    trash_retention_days: int = 30
    upstream_port: int = 8791
    timeout_s: float = 30.0
    # Contexte ChatGPT (endpoint POST /context/chatgpt) : jeton ultra-scopé et
    # TTL du contexte. Jeton absent => endpoint inerte (503), aucune donnée
    # reçue ; l'enrichissement des notes reste sans effet (aucun contexte).
    contexte_token: str = ""
    contexte_ttl_s: int = 300
    # Diagnostic : renvoyer l'ID de conversation dans la réponse POST
    # (réservé au débogage local ; jamais par défaut, jamais loggé).
    contexte_echo_id: bool = False
    _verrou: object = field(default=None, repr=False, compare=False)

    @property
    def verrou(self):  # pragma: no cover - simple accesseur
        if self._verrou is None:
            import threading

            # RLock : modifier()/deplacer() tiennent le verrou tout en appelant
            # trouver() -> synchroniser() qui le ré-acquièrent.
            self._verrou = threading.RLock()
        return self._verrou

    @property
    def fichier_db(self) -> Path:
        return self.data_dir / "tasks.db"

    @property
    def repertoire_oauth(self) -> Path:
        return self.data_dir / "oauth"

    @classmethod
    def charger(cls) -> "Config":
        return cls(
            caldav_url=_requise("TASKS_CALDAV_URL").rstrip("/"),
            caldav_user=_requise("TASKS_CALDAV_USER"),
            caldav_password=_requise("TASKS_CALDAV_PASSWORD"),
            data_dir=Path(os.environ.get("TASKS_DATA_DIR", "/srv/tasks/data")),
            trash_list=os.environ.get("TASKS_TRASH_LIST", "Corbeille"),
            trash_retention_days=int(os.environ.get("TASKS_TRASH_RETENTION_DAYS", "30")),
            upstream_port=int(os.environ.get("TASKS_UPSTREAM_PORT", "8791")),
            timeout_s=float(os.environ.get("TASKS_CALDAV_TIMEOUT", "30")),
            contexte_token=os.environ.get("TASKS_CONTEXT_TOKEN", "").strip(),
            contexte_ttl_s=int(os.environ.get("TASKS_CONTEXT_TTL_S", "300")),
            contexte_echo_id=os.environ.get("TASKS_CONTEXT_ECHO_ID", "").strip().lower()
            in ("1", "true", "oui", "yes"),
        )
