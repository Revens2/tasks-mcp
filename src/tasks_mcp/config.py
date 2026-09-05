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
        )
