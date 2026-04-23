"""Progress persistence for the parallel pool orchestrator."""
from __future__ import annotations
import json
import tempfile
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
        try:
            return json.loads(self._path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt progress file: {self._path}") from exc

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with open(fd, "w") as f:
                json.dump(data, f, indent=2)
            Path(tmp).replace(self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
