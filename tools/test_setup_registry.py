# tools/test_setup_registry.py
"""Unit tests for create_registry_secret registry/repo_name prompt logic."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import scripts/setup.py without executing main()
REPO_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location("setup", REPO_ROOT / "scripts" / "setup.py")
_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_setup)
create_registry_secret = _setup.create_registry_secret


def _args(**kwargs) -> argparse.Namespace:
    defaults = dict(
        registry="", repo_name="llm-d-inference-scheduler",
        registry_user="", registry_token="",
        no_cluster=True, test_push_tag="_test-image-push",
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _meta(tmp_path: Path, registry: str = "", repo_name: str = "") -> Path:
    """Write a minimal run_metadata.json and return the run_dir."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_metadata.json").write_text(
        json.dumps({"registry": registry, "repo_name": repo_name})
    )
    return run_dir


def _prompt_seq(*values):
    """Return a side_effect function that pops from a sequence, returning "" for extras."""
    it = iter(values)
    return lambda *a, **kw: next(it, "")


# ── fresh run ────────────────────────────────────────────────────────────────

class TestFreshRun:
    """No run_metadata.json — user must be prompted for registry and repo_name."""

    def test_prompts_for_registry_and_repo_name(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        # prompt sequence: registry → repo_name → registry_user (returns "" → no token prompt)
        with patch.object(_setup, "prompt", side_effect=_prompt_seq("quay.io/myuser", "my-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            reg, repo = create_registry_secret(_args(), "ns", "podman", run_dir)

        assert reg == "quay.io/myuser"
        assert repo == "my-repo"

    def test_blank_registry_skips_without_prompting_repo(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        with patch.object(_setup, "prompt", return_value=""):
            reg, repo = create_registry_secret(_args(), "ns", "podman", run_dir)

        assert reg == ""
        assert repo == "llm-d-inference-scheduler"  # argparse default


# ── re-run with both values in metadata ──────────────────────────────────────

class TestRerunBothValuesPresent:
    """run_metadata.json has both registry and repo_name — show and offer to change."""

    def test_no_change_returns_existing_values(self, tmp_path):
        run_dir = _meta(tmp_path, registry="quay.io/myuser", repo_name="my-repo")

        with patch.object(_setup, "prompt", return_value="N"):
            reg, repo = create_registry_secret(_args(), "ns", "podman", run_dir)

        assert reg == "quay.io/myuser"
        assert repo == "my-repo"

    def test_no_change_fires_only_the_change_prompt(self, tmp_path):
        run_dir = _meta(tmp_path, registry="quay.io/myuser", repo_name="my-repo")

        prompt_mock = MagicMock(return_value="N")
        with patch.object(_setup, "prompt", prompt_mock):
            create_registry_secret(_args(), "ns", "podman", run_dir)

        assert prompt_mock.call_count == 1
        first_var = prompt_mock.call_args[0][0]
        assert first_var == "change_reg"

    def test_yes_change_prompts_with_existing_as_defaults(self, tmp_path):
        run_dir = _meta(tmp_path, registry="quay.io/old", repo_name="old-repo")

        # sequence: change? → registry (accept old) → repo_name (accept old) → registry_user ""
        with patch.object(_setup, "prompt",
                          side_effect=_prompt_seq("y", "quay.io/new", "new-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            reg, repo = create_registry_secret(_args(), "ns", "podman", run_dir)

        assert reg == "quay.io/new"
        assert repo == "new-repo"

    def test_yes_change_uses_existing_values_as_prompt_defaults(self, tmp_path):
        run_dir = _meta(tmp_path, registry="quay.io/old", repo_name="old-repo")

        prompt_calls = []
        def _prompt(var, msg, default="", **kw):
            prompt_calls.append((var, default))
            if var == "change_reg":
                return "y"
            return default  # accept existing defaults

        with patch.object(_setup, "prompt", side_effect=_prompt), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            create_registry_secret(_args(), "ns", "podman", run_dir)

        defaults = {v: d for v, d in prompt_calls}
        assert defaults.get("registry")  == "quay.io/old"
        assert defaults.get("repo_name") == "old-repo"


# ── partial or missing metadata ───────────────────────────────────────────────

class TestRerunPartialMetadata:
    """Metadata exists but is missing registry or repo_name — fresh prompt path."""

    def test_empty_registry_in_metadata_prompts_fresh(self, tmp_path):
        run_dir = _meta(tmp_path, registry="", repo_name="existing-repo")

        with patch.object(_setup, "prompt",
                          side_effect=_prompt_seq("quay.io/myuser", "existing-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            reg, repo = create_registry_secret(_args(), "ns", "podman", run_dir)

        assert reg == "quay.io/myuser"

    def test_empty_repo_name_in_metadata_prompts_fresh(self, tmp_path):
        run_dir = _meta(tmp_path, registry="", repo_name="")

        with patch.object(_setup, "prompt",
                          side_effect=_prompt_seq("quay.io/myuser", "my-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            reg, repo = create_registry_secret(_args(), "ns", "podman", run_dir)

        assert reg == "quay.io/myuser"
        assert repo == "my-repo"

    def test_args_registry_bypasses_change_gate(self, tmp_path):
        """--registry flag skips the show+change? flow; repo_name is still prompted."""
        run_dir = _meta(tmp_path, registry="", repo_name="")

        # No "change?" prompt should fire; only repo_name is prompted
        with patch.object(_setup, "prompt",
                          side_effect=_prompt_seq("my-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            reg, repo = create_registry_secret(
                _args(registry="quay.io/fromarg"), "ns", "podman", run_dir)

        assert reg == "quay.io/fromarg"
        assert repo == "my-repo"

    def test_args_registry_with_existing_metadata_bypasses_change_gate(self, tmp_path):
        """--registry flag skips the change? gate even when metadata has values."""
        run_dir = _meta(tmp_path, registry="quay.io/old", repo_name="old-repo")

        with patch.object(_setup, "prompt",
                          side_effect=_prompt_seq("my-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run"):
            reg, repo = create_registry_secret(
                _args(registry="quay.io/fromarg"), "ns", "podman", run_dir)

        assert reg == "quay.io/fromarg"
        assert repo == "my-repo"


# ── --no-cluster flag ─────────────────────────────────────────────────────────

class TestNoClusterFlag:
    """--no-cluster: k8s secret creation is skipped in all paths."""

    def test_fresh_run_no_cluster_returns_prompted_values(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        with patch.object(_setup, "prompt",
                          side_effect=_prompt_seq("quay.io/myuser", "my-repo")), \
             patch.object(_setup, "prompt_secret", return_value="tok"), \
             patch.object(_setup, "run") as kubectl_mock:
            reg, repo = create_registry_secret(_args(no_cluster=True), "ns", "podman", run_dir)

        # kubectl must not have been called
        for c in kubectl_mock.call_args_list:
            assert "kubectl" not in str(c)
        assert reg == "quay.io/myuser"
        assert repo == "my-repo"

    def test_rerun_no_change_no_cluster_returns_immediately(self, tmp_path):
        run_dir = _meta(tmp_path, registry="quay.io/myuser", repo_name="my-repo")

        with patch.object(_setup, "prompt", return_value="N"):
            reg, repo = create_registry_secret(_args(no_cluster=True), "ns", "podman", run_dir)

        assert reg == "quay.io/myuser"
        assert repo == "my-repo"
