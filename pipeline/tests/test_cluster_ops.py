"""Tests for pipeline/lib/cluster_ops.py — file-system primitives."""

from __future__ import annotations

import json

import pytest

from pipeline.lib import cluster_ops, layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path, monkeypatch):
    """Every test starts with a fresh workspace rooted at tmp_path.

    cluster_ops resolves paths via layout.cluster_config_path(), which reads
    from layout._EXPERIMENT_ROOT. We set that to tmp_path so cluster files
    land under tmp_path/workspace/clusters/<id>/ without touching the real
    filesystem.
    """
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


# ── read_cluster_config ────────────────────────────────────────────────


class TestReadClusterConfig:
    def test_returns_empty_dict_when_file_absent(self):
        assert cluster_ops.read_cluster_config("ocp-east") == {}

    def test_returns_empty_dict_when_cluster_dir_absent(self):
        # Different cluster id, never written to.
        assert cluster_ops.read_cluster_config("never-provisioned") == {}

    def test_returns_parsed_dict_when_file_present(self, tmp_path):
        path = layout.cluster_config_path("ocp-east")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"cluster_id": "ocp-east", "namespaces": ["a", "b"]}))
        assert cluster_ops.read_cluster_config("ocp-east") == {
            "cluster_id": "ocp-east",
            "namespaces": ["a", "b"],
        }

    def test_round_trip_via_write(self):
        cluster_ops.write_cluster_config("ocp-east", {"cluster_id": "ocp-east"})
        assert cluster_ops.read_cluster_config("ocp-east") == {"cluster_id": "ocp-east"}


# ── write_cluster_config ───────────────────────────────────────────────


class TestWriteClusterConfig:
    def test_creates_cluster_directory_if_absent(self, tmp_path):
        cluster_ops.write_cluster_config("ocp-east", {"cluster_id": "ocp-east"})
        assert layout.cluster_dir("ocp-east").is_dir()
        assert layout.cluster_config_path("ocp-east").is_file()

    def test_writes_json_content(self, tmp_path):
        config = {
            "cluster_id": "ocp-east",
            "namespaces": ["a", "b"],
            "secret_names": {"hf_token": "hf-secret"},
        }
        cluster_ops.write_cluster_config("ocp-east", config)
        path = layout.cluster_config_path("ocp-east")
        assert json.loads(path.read_text()) == config

    def test_overwrites_existing_file(self):
        cluster_ops.write_cluster_config("ocp-east", {"version": 1})
        cluster_ops.write_cluster_config("ocp-east", {"version": 2})
        assert cluster_ops.read_cluster_config("ocp-east") == {"version": 2}

    def test_tmpfile_cleaned_up_after_success(self, tmp_path):
        cluster_ops.write_cluster_config("ocp-east", {"cluster_id": "ocp-east"})
        # Only cluster_config.json should remain — no stray .tmp file.
        contents = list(layout.cluster_dir("ocp-east").iterdir())
        assert contents == [layout.cluster_config_path("ocp-east")]

    def test_tmpfile_cleaned_up_on_failure(self, monkeypatch):
        layout.cluster_dir("ocp-east").mkdir(parents=True)

        def boom(*_a, **_kw):
            raise RuntimeError("simulated dump failure")

        monkeypatch.setattr(cluster_ops.json, "dump", boom)
        with pytest.raises(RuntimeError, match="simulated dump failure"):
            cluster_ops.write_cluster_config("ocp-east", {"x": 1})
        # No .tmp file lingers in the cluster dir, and the real config was
        # never written.
        contents = list(layout.cluster_dir("ocp-east").iterdir())
        assert contents == []

    def test_atomic_rename_no_torn_write(self, monkeypatch):
        # Seed with a valid prior config; simulate a write failure mid-dump
        # and verify the prior config is intact (the rename never happened).
        prior = {"cluster_id": "ocp-east", "version": "prior"}
        cluster_ops.write_cluster_config("ocp-east", prior)

        def boom(*_a, **_kw):
            raise RuntimeError("simulated dump failure")

        monkeypatch.setattr(cluster_ops.json, "dump", boom)
        with pytest.raises(RuntimeError):
            cluster_ops.write_cluster_config("ocp-east", {"version": "broken"})
        # Concurrent reader sees only the prior content, never a torn write.
        assert cluster_ops.read_cluster_config("ocp-east") == prior

    def test_tmpfile_in_same_directory(self, monkeypatch, tmp_path):
        # The tmpfile must be created in the same directory as the target so
        # that Path.replace() is an atomic rename (POSIX requires same fs).
        captured = {}
        real_mkstemp = cluster_ops.tempfile.mkstemp

        def spy(*args, **kwargs):
            captured["dir"] = kwargs.get("dir")
            return real_mkstemp(*args, **kwargs)

        monkeypatch.setattr(cluster_ops.tempfile, "mkstemp", spy)
        cluster_ops.write_cluster_config("ocp-east", {"x": 1})
        assert captured["dir"] == layout.cluster_dir("ocp-east")


# ── update_cluster_config ──────────────────────────────────────────────


class TestUpdateClusterConfig:
    def test_starts_from_empty_when_file_absent(self):
        result = cluster_ops.update_cluster_config("new-cluster", cluster_id="new-cluster")
        assert result == {"cluster_id": "new-cluster"}
        assert cluster_ops.read_cluster_config("new-cluster") == {"cluster_id": "new-cluster"}

    def test_returns_new_config(self):
        cluster_ops.write_cluster_config("ocp-east", {"cluster_id": "ocp-east"})
        result = cluster_ops.update_cluster_config("ocp-east", namespaces=["a", "b"])
        assert result == {"cluster_id": "ocp-east", "namespaces": ["a", "b"]}

    def test_full_replace_for_namespaces_list(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {"cluster_id": "ocp-east", "namespaces": ["old-1", "old-2", "old-3"]},
        )
        cluster_ops.update_cluster_config("ocp-east", namespaces=["new"])
        assert cluster_ops.read_cluster_config("ocp-east")["namespaces"] == ["new"]

    def test_full_replace_for_top_level_scalar(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {"cluster_id": "ocp-east", "storage_class": "gp3"},
        )
        cluster_ops.update_cluster_config("ocp-east", storage_class="gp2")
        assert cluster_ops.read_cluster_config("ocp-east")["storage_class"] == "gp2"

    def test_deep_merge_secret_names_preserves_existing(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {
                "secret_names": {
                    "hf_token": "hf-secret",
                    "registry_creds": "registry-creds",
                    "github_token": "github-token",
                },
            },
        )
        cluster_ops.update_cluster_config(
            "ocp-east",
            secret_names={"dockerhub_creds": "dockerhub-creds"},
        )
        assert cluster_ops.read_cluster_config("ocp-east")["secret_names"] == {
            "hf_token": "hf-secret",
            "registry_creds": "registry-creds",
            "github_token": "github-token",
            "dockerhub_creds": "dockerhub-creds",
        }

    def test_deep_merge_secret_names_overrides_present_keys(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {"secret_names": {"hf_token": "old-name", "registry_creds": "reg-old"}},
        )
        cluster_ops.update_cluster_config(
            "ocp-east",
            secret_names={"hf_token": "new-name"},
        )
        assert cluster_ops.read_cluster_config("ocp-east")["secret_names"] == {
            "hf_token": "new-name",
            "registry_creds": "reg-old",
        }

    def test_deep_merge_workspaces_preserves_existing(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {
                "workspaces": {
                    "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
                    "source": {"persistentVolumeClaim": {"claimName": "source-pvc"}},
                },
            },
        )
        cluster_ops.update_cluster_config(
            "ocp-east",
            workspaces={"cache": {"persistentVolumeClaim": {"claimName": "cache-pvc"}}},
        )
        assert cluster_ops.read_cluster_config("ocp-east")["workspaces"] == {
            "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
            "source": {"persistentVolumeClaim": {"claimName": "source-pvc"}},
            "cache": {"persistentVolumeClaim": {"claimName": "cache-pvc"}},
        }

    def test_deep_merge_workspaces_recurses_into_nested_dict(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {
                "workspaces": {
                    "data-storage": {
                        "persistentVolumeClaim": {"claimName": "old-pvc"},
                        "extra": "keep-me",
                    },
                },
            },
        )
        cluster_ops.update_cluster_config(
            "ocp-east",
            workspaces={"data-storage": {"persistentVolumeClaim": {"claimName": "new-pvc"}}},
        )
        # The deep merge updates claimName but preserves the sibling "extra".
        assert cluster_ops.read_cluster_config("ocp-east")["workspaces"] == {
            "data-storage": {
                "persistentVolumeClaim": {"claimName": "new-pvc"},
                "extra": "keep-me",
            },
        }

    def test_multiple_updates_in_single_call(self):
        cluster_ops.write_cluster_config(
            "ocp-east",
            {
                "cluster_id": "ocp-east",
                "namespaces": ["a"],
                "secret_names": {"hf_token": "hf-secret"},
            },
        )
        cluster_ops.update_cluster_config(
            "ocp-east",
            namespaces=["a", "b"],
            secret_names={"github_token": "github-token"},
            storage_class="gp3",
        )
        assert cluster_ops.read_cluster_config("ocp-east") == {
            "cluster_id": "ocp-east",
            "namespaces": ["a", "b"],
            "secret_names": {
                "hf_token": "hf-secret",
                "github_token": "github-token",
            },
            "storage_class": "gp3",
        }

    def test_deep_merge_key_with_non_dict_existing_is_replaced(self):
        # Defensive: if the existing value for a deep-merge key is somehow
        # not a dict (corrupted config), the update should fall back to
        # full replace rather than crash.
        path = layout.cluster_config_path("ocp-east")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"secret_names": "wrong-type"}))
        cluster_ops.update_cluster_config(
            "ocp-east",
            secret_names={"hf_token": "hf-secret"},
        )
        assert cluster_ops.read_cluster_config("ocp-east")["secret_names"] == {
            "hf_token": "hf-secret",
        }

    def test_deep_merge_key_with_non_dict_update_is_full_replace(self):
        # If the caller passes a non-dict value for a deep-merge key, full
        # replace (no attempt to merge a string into an existing dict).
        cluster_ops.write_cluster_config(
            "ocp-east",
            {"secret_names": {"hf_token": "hf-secret"}},
        )
        cluster_ops.update_cluster_config("ocp-east", secret_names=None)
        assert cluster_ops.read_cluster_config("ocp-east")["secret_names"] is None

    def test_uses_layout_for_path_resolution(self):
        # Acceptance: "Uses pipeline/lib/layout.py for path resolution (no
        # path string-mashing)." Update should write to the path produced
        # by layout.cluster_config_path, no other location.
        cluster_ops.update_cluster_config("ocp-east", cluster_id="ocp-east")
        assert layout.cluster_config_path("ocp-east").exists()

    def test_cluster_id_kwarg_does_not_collide_with_positional(self):
        # The positional cluster_id arg is positional-only (def(..., /, **updates)).
        # A caller can pass cluster_id="..." in **updates without TypeError;
        # the kwarg lands in the config payload as the cluster_id FIELD.
        result = cluster_ops.update_cluster_config(
            "ocp-east",
            cluster_id="ocp-east",
            namespaces=["a"],
        )
        assert result == {"cluster_id": "ocp-east", "namespaces": ["a"]}
