"""Progress persistence for the parallel pool orchestrator."""
from __future__ import annotations
import json
import subprocess
import sys
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


class ConfigMapProgressStore(ProgressStore):
    """Read/write progress as a Kubernetes ConfigMap."""

    CONFIGMAP_NAME = "sim2real-progress"
    DATA_KEY = "progress"

    def __init__(self, namespace: str) -> None:
        if not namespace:
            raise ValueError("ConfigMapProgressStore requires a non-empty namespace")
        self._namespace = namespace

    def load(self) -> dict:
        result = subprocess.run(
            ["kubectl", "get", "configmap", self.CONFIGMAP_NAME,
             "-n", self._namespace,
             "-o", f"jsonpath={{.data.{self.DATA_KEY}}}"],
            check=False, text=True, capture_output=True,
        )
        if result.returncode != 0:
            if "(NotFound)" in result.stderr:
                return {}
            raise RuntimeError(
                f"kubectl get configmap {self.CONFIGMAP_NAME} failed: "
                f"{result.stderr.strip()}"
            )
        raw = result.stdout.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Corrupt ConfigMap {self.CONFIGMAP_NAME} in {self._namespace}"
            ) from exc

    def save(self, data: dict) -> None:
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": self.CONFIGMAP_NAME,
                "namespace": self._namespace,
            },
            "data": {
                self.DATA_KEY: json.dumps(data, indent=2),
            },
        }
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=json.dumps(cm),
            check=False, text=True, capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to update ConfigMap {self.CONFIGMAP_NAME}: "
                f"{result.stderr.strip()}"
            )


class CompositeProgressStore(ProgressStore):
    """Write to all stores; read from the first that returns data.

    Primary store failures propagate. Secondary store failures (both
    save and load) print a warning to stderr but do not raise.
    """

    def __init__(self, primary: ProgressStore, *secondaries: ProgressStore) -> None:
        self._primary = primary
        self._secondaries = secondaries

    def load(self) -> dict:
        data = self._primary.load()
        if data:
            return data
        for store in self._secondaries:
            try:
                data = store.load()
                if data:
                    return data
            except (ValueError, RuntimeError, OSError) as exc:
                print(f"[WARN] Secondary store load failed: {exc}", file=sys.stderr)
                continue
        return {}

    def save(self, data: dict) -> None:
        self._primary.save(data)
        for store in self._secondaries:
            try:
                store.save(data)
            except (ValueError, RuntimeError, OSError) as exc:
                print(f"[WARN] {type(store).__name__} save failed: {exc}", file=sys.stderr)
