"""Progress persistence for the parallel pool orchestrator."""
from __future__ import annotations
import json
import re
import subprocess
from abc import ABC, abstractmethod


class ProgressStore(ABC):
    @abstractmethod
    def load(self) -> dict:
        """Return current progress dict, or {} if none exists."""

    @abstractmethod
    def save(self, data: dict) -> None:
        """Atomically persist the full dict."""


class ConfigMapProgressStore(ProgressStore):
    """Read/write progress as a Kubernetes ConfigMap.

    Naming (issue #551): ``sim2real-progress-<scenario>-<run>`` when both are
    supplied. The scenario segment scopes progress state per experiment root,
    preventing cross-root collision when two experiment repos share a run
    name. Backward-compat: ``run_name`` alone still yields the pre-#551
    ``sim2real-progress-<run>`` name (the legacy shape that ``load()`` also
    migrates on first read).
    """

    BASE_NAME = "sim2real-progress"
    DATA_KEY = "progress"

    _K8S_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")

    def __init__(
        self,
        namespace: str,
        *,
        run_name: str = "",
        scenario: str = "",
    ) -> None:
        if not namespace:
            raise ValueError("ConfigMapProgressStore requires a non-empty namespace")
        self._namespace = namespace
        self._run_name = run_name
        self._scenario = scenario

        sanitized_run = self._sanitize(run_name) if run_name else ""
        sanitized_scenario = self._sanitize(scenario) if scenario else ""

        parts = [self.BASE_NAME]
        if sanitized_scenario:
            parts.append(sanitized_scenario)
        if sanitized_run:
            parts.append(sanitized_run)
        candidate = "-".join(parts)

        if len(candidate) > 253 or not self._K8S_NAME_RE.match(candidate):
            raise ValueError(
                f"scenario={scenario!r}, run_name={run_name!r} produces "
                f"invalid ConfigMap name {candidate!r} — must be lowercase "
                f"alphanumeric, hyphens, or dots, max 253 chars"
            )
        self.configmap_name = candidate

        # Legacy name (pre-#551): sim2real-progress-<run>. Set only when
        # scenario AND run_name are both supplied — otherwise there is no
        # legacy shape to migrate from (either the new-name and legacy-name
        # would collide, or both would be BASE_NAME).
        if sanitized_scenario and sanitized_run:
            self._legacy_configmap_name: str | None = (
                f"{self.BASE_NAME}-{sanitized_run}"
            )
        else:
            self._legacy_configmap_name = None

        self._sanitized_scenario = sanitized_scenario
        self._sanitized_run = sanitized_run

    @staticmethod
    def _sanitize(value: str) -> str:
        """Sanitize a name fragment for a Kubernetes resource name.

        Lowercases, replaces disallowed characters with hyphens, and strips
        leading/trailing hyphens. Result is validated as part of the full
        ConfigMap name in ``__init__``.
        """
        return re.sub(r"[^a-z0-9.\-]", "-", value.lower()).strip("-")

    def load(self) -> dict:
        raw = self._get_cm(self.configmap_name)
        if raw is not None:
            return self._parse_data(raw, self.configmap_name)

        # New-name CM is NotFound. Fall back to legacy if applicable (#551).
        if self._legacy_configmap_name:
            legacy_raw = self._get_cm(self._legacy_configmap_name)
            if legacy_raw is not None:
                data = self._parse_data(legacy_raw, self._legacy_configmap_name)
                # Order matters: if the delete step fails, the next load()
                # will find the new-name CM and skip the legacy check —
                # the legacy CM leaks but no data is lost.
                self.save(data)
                self._delete_cm(self._legacy_configmap_name)
                return data

        return {}

    def _get_cm(self, name: str) -> str | None:
        """Return raw data-key contents, or None if the ConfigMap is NotFound.

        Raises RuntimeError on any other kubectl error.
        """
        try:
            result = subprocess.run(
                ["kubectl", "get", "configmap", name,
                 "-n", self._namespace,
                 "-o", f"jsonpath={{.data.{self.DATA_KEY}}}"],
                check=False, text=True, capture_output=True,
            )
        except OSError as exc:
            raise RuntimeError(f"kubectl not available: {exc}") from exc
        if result.returncode != 0:
            if "(NotFound)" in result.stderr:
                return None
            raise RuntimeError(
                f"kubectl get configmap {name} failed: {result.stderr.strip()}"
            )
        return result.stdout

    def _parse_data(self, raw: str, source_name: str) -> dict:
        raw = raw.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Corrupt ConfigMap {source_name} in {self._namespace}"
            ) from exc

    def _delete_cm(self, name: str) -> None:
        try:
            result = subprocess.run(
                ["kubectl", "delete", "configmap", name,
                 "-n", self._namespace, "--ignore-not-found"],
                check=False, text=True, capture_output=True,
            )
        except OSError as exc:
            raise RuntimeError(
                f"kubectl delete configmap {name} failed: {exc}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"kubectl delete configmap {name} failed: {result.stderr.strip()}"
            )

    def save(self, data: dict) -> None:
        metadata: dict = {
            "name": self.configmap_name,
            "namespace": self._namespace,
        }
        if self._sanitized_scenario and self._sanitized_run:
            metadata["labels"] = {
                "sim2real.scenario": self._sanitized_scenario,
                "sim2real.run": self._sanitized_run,
            }
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": metadata,
            "data": {
                self.DATA_KEY: json.dumps(data, indent=2),
            },
        }
        try:
            result = subprocess.run(
                ["kubectl", "apply", "-f", "-"],
                input=json.dumps(cm),
                check=False, text=True, capture_output=True,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Failed to update ConfigMap {self.configmap_name}: {exc}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to update ConfigMap {self.configmap_name}: "
                f"{result.stderr.strip()}"
            )
