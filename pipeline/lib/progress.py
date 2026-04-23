"""Progress persistence for the parallel pool orchestrator."""
from __future__ import annotations
import json
from abc import ABC, abstractmethod
from pathlib import Path


class ProgressStore(ABC):
    @abstractmethod
    def load(self) -> dict:
        """Return current progress dict, or {} if none exists."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Atomically persist the full dict."""


class LocalProgressStore(ProgressStore):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._path)
