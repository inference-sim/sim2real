"""Tests for pipeline/lib/layout.py — path helpers + experiment-root resolution."""

import os
from pathlib import Path

import pytest

from pipeline.lib import layout


@pytest.fixture(autouse=True)
def _reset_experiment_root():
    """Each test starts with the module-level state cleared.

    layout._EXPERIMENT_ROOT is mutated by set_experiment_root(); restore
    None after every test so tests don't leak state into each other.
    """
    layout._EXPERIMENT_ROOT = None
    yield
    layout._EXPERIMENT_ROOT = None


# ── set_experiment_root / experiment_root ─────────────────────────────


class TestSetExperimentRoot:
    def test_none_falls_back_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolved = layout.set_experiment_root(None)
        assert resolved == tmp_path
        assert layout.experiment_root() == tmp_path

    def test_empty_string_falls_back_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolved = layout.set_experiment_root("")
        assert resolved == tmp_path
        assert layout.experiment_root() == tmp_path

    def test_absolute_string_resolved_to_path(self, tmp_path):
        resolved = layout.set_experiment_root(str(tmp_path))
        assert resolved == tmp_path
        assert layout.experiment_root() == tmp_path

    def test_path_object_resolved(self, tmp_path):
        resolved = layout.set_experiment_root(tmp_path)
        assert resolved == tmp_path
        assert layout.experiment_root() == tmp_path

    def test_relative_path_resolved_absolutely(self, tmp_path, monkeypatch):
        sub = tmp_path / "experiment"
        sub.mkdir()
        monkeypatch.chdir(tmp_path)
        resolved = layout.set_experiment_root("experiment")
        assert resolved.is_absolute()
        assert resolved == sub

    def test_resolves_symlinks_and_dots(self, tmp_path, monkeypatch):
        target = tmp_path / "real"
        target.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("filesystem does not support symlinks")
        monkeypatch.chdir(tmp_path)
        resolved = layout.set_experiment_root("./link/../link")
        # .resolve() resolves both symlinks and .. segments.
        assert resolved == target.resolve()


class TestExperimentRoot:
    def test_defaults_to_cwd_when_unset(self, tmp_path, monkeypatch):
        # Fresh module state (per autouse fixture); no set_experiment_root call.
        monkeypatch.chdir(tmp_path)
        assert layout.experiment_root() == tmp_path

    def test_cwd_fallback_is_lazy(self, tmp_path, monkeypatch):
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        monkeypatch.chdir(first)
        assert layout.experiment_root() == first
        os.chdir(second)
        assert layout.experiment_root() == second

    def test_explicit_set_overrides_cwd(self, tmp_path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.chdir(tmp_path)
        layout.set_experiment_root(str(other))
        # cwd is tmp_path, but experiment_root sticks to the set value.
        assert layout.experiment_root() == other


# ── Workspace path helpers ────────────────────────────────────────────


class TestWorkspacePaths:
    def test_workspace_dir(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.workspace_dir() == tmp_path / "workspace"

    def test_clusters_dir(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.clusters_dir() == tmp_path / "workspace" / "clusters"

    def test_cluster_dir(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.cluster_dir("ocp-east") == (
            tmp_path / "workspace" / "clusters" / "ocp-east"
        )

    def test_cluster_config_path(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.cluster_config_path("ocp-east") == (
            tmp_path / "workspace" / "clusters" / "ocp-east" / "cluster_config.json"
        )

    def test_runs_dir(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.runs_dir() == tmp_path / "workspace" / "runs"

    def test_translations_dir(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.translations_dir() == tmp_path / "workspace" / "translations"

    def test_setup_config_path(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.setup_config_path() == tmp_path / "workspace" / "setup_config.json"

    def test_helpers_track_experiment_root_changes(self, tmp_path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        layout.set_experiment_root(first)
        assert layout.workspace_dir() == first / "workspace"
        layout.set_experiment_root(second)
        assert layout.workspace_dir() == second / "workspace"


# ── list_cluster_ids ──────────────────────────────────────────────────


class TestListClusterIds:
    def test_returns_empty_when_clusters_dir_absent(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        # No workspace/clusters/ created.
        assert layout.list_cluster_ids() == []

    def test_returns_empty_when_clusters_dir_empty(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        layout.clusters_dir().mkdir(parents=True)
        assert layout.list_cluster_ids() == []

    def test_returns_sorted_subdir_names(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        clusters = layout.clusters_dir()
        clusters.mkdir(parents=True)
        (clusters / "ocp-west").mkdir()
        (clusters / "ocp-east").mkdir()
        (clusters / "kind-local").mkdir()
        assert layout.list_cluster_ids() == ["kind-local", "ocp-east", "ocp-west"]

    def test_ignores_files_at_clusters_level(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        clusters = layout.clusters_dir()
        clusters.mkdir(parents=True)
        (clusters / "ocp-east").mkdir()
        (clusters / "stray.json").write_text("{}")
        assert layout.list_cluster_ids() == ["ocp-east"]

    def test_returns_empty_when_clusters_dir_is_a_file(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        layout.workspace_dir().mkdir(parents=True)
        # Adversarial: workspace/clusters exists but as a file, not a directory.
        clusters_path = layout.clusters_dir()
        clusters_path.write_text("not a directory")
        assert layout.list_cluster_ids() == []


# ── Translation path helpers ──────────────────────────────────────────


class TestTranslationPaths:
    def test_translation_dir(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.translation_dir("abc123") == (
            tmp_path / "workspace" / "translations" / "abc123"
        )

    def test_translation_output_path(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.translation_output_path("abc123") == (
            tmp_path / "workspace" / "translations" / "abc123" / "translation_output.json"
        )

    def test_registered_path(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.registered_path("abc123") == (
            tmp_path / "workspace" / "translations" / "abc123" / "registered.json"
        )

    def test_generated_config_path(self, tmp_path):
        layout.set_experiment_root(tmp_path)
        assert layout.generated_config_path("abc123", "softreflective") == (
            tmp_path / "workspace" / "translations" / "abc123"
            / "generated" / "softreflective" / "softreflective_config.yaml"
        )


# ── No content I/O ────────────────────────────────────────────────────


class TestNoContentIO:
    """layout.py is a pure path module — no JSON/YAML imports, no opens."""

    def test_no_json_yaml_imports(self):
        source = Path(layout.__file__).read_text()
        assert "import json" not in source
        assert "import yaml" not in source
        # Permitted filesystem ops; ensure none of these are accidentally
        # content-reading helpers (.read_text / .read_bytes / open()).
        assert ".read_text(" not in source
        assert ".read_bytes(" not in source
        assert "open(" not in source
