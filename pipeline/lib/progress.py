"""Progress persistence for the parallel pool orchestrator."""
from __future__ import annotations
import json
import subprocess
import sys
from abc import ABC, abstractmethod


class ProgressStore(ABC):
    @abstractmethod
    def load(self) -> dict:
        """Return current progress dict, or {} if none exists."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Atomically persist the full dict."""


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
