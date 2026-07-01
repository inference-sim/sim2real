"""Tests for pipeline/lib/cluster_ops.py — file-system + kubectl/oc primitives."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

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


# ── kubectl/oc invocation harness ─────────────────────────────────────


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Build a stand-in for subprocess.CompletedProcess returned by _run."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Records every _run() call; returns canned outputs keyed by command prefix.

    Use ``.set(prefix, return_value)`` to register a response for any command
    whose argv startswith the prefix list. Unmatched calls return success
    (returncode=0, stdout=""), which is the right default for kubectl applies
    where the live state already matches.
    """

    def __init__(self):
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self._responses: list[tuple[list[str], SimpleNamespace]] = []

    def set(self, prefix: list[str], result):
        self._responses.append((prefix, result))

    def __call__(self, cmd, *, check=True, capture=False, input=None):
        self.calls.append(list(cmd))
        self.inputs.append(input)
        for prefix, response in self._responses:
            if cmd[: len(prefix)] == prefix:
                if check and response.returncode != 0:
                    raise subprocess.CalledProcessError(
                        response.returncode, cmd, response.stdout, response.stderr,
                    )
                return response
        return _completed(returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(cluster_ops, "_run", fr)
    return fr


# ── detect_openshift / check_cluster_reachable / secret_exists ────────


class TestDetectOpenshift:
    def test_returns_false_when_oc_not_installed(self, monkeypatch, fake_run):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: False)
        assert cluster_ops.detect_openshift() is False
        # We must NOT invoke oc when it isn't on PATH.
        assert fake_run.calls == []

    def test_returns_true_when_oc_whoami_succeeds(self, monkeypatch, fake_run):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: True)
        fake_run.set(["oc", "whoami"], _completed(returncode=0))
        assert cluster_ops.detect_openshift() is True

    def test_returns_false_when_oc_whoami_fails(self, monkeypatch, fake_run):
        monkeypatch.setattr(cluster_ops, "_which", lambda cmd: True)
        fake_run.set(["oc", "whoami"], _completed(returncode=1, stderr="not logged in"))
        assert cluster_ops.detect_openshift() is False


class TestCheckClusterReachable:
    def test_returns_silently_on_success(self, fake_run):
        fake_run.set(["kubectl", "cluster-info"], _completed(returncode=0))
        cluster_ops.check_cluster_reachable()  # no raise

    def test_forbidden_treated_as_reachable(self, fake_run):
        fake_run.set(["kubectl", "cluster-info"],
                     _completed(returncode=1, stderr="Error: forbidden"))
        cluster_ops.check_cluster_reachable()

    @pytest.mark.parametrize("stderr,reason_fragment", [
        ("dial tcp: lookup foo: no such host", "DNS resolution failed"),
        ("dial tcp 127.0.0.1:8443: connect: connection refused", "Connection refused"),
        ("Unable to connect to the server: i/o timeout", "Connection timed out"),
        ("error: You must be logged in to the server (Unauthorized)", "Authentication failed"),
        ("error: no configuration has been provided", "No kubeconfig found"),
        ("something else broke", "kubectl cluster-info failed"),
    ])
    def test_classifies_failure_and_raises(self, fake_run, stderr, reason_fragment):
        fake_run.set(["kubectl", "cluster-info"],
                     _completed(returncode=1, stderr=stderr))
        with pytest.raises(cluster_ops.ClusterUnreachableError) as exc:
            cluster_ops.check_cluster_reachable()
        assert reason_fragment in exc.value.args[0]


class TestSecretExists:
    def test_returns_true_when_kubectl_get_succeeds(self, fake_run):
        fake_run.set(["kubectl", "get", "secret", "hf-secret"],
                     _completed(returncode=0, stdout="hf-secret"))
        assert cluster_ops.secret_exists("hf-secret", "ns-a") is True
        # Right namespace flag is on the call.
        assert "-n=ns-a" in fake_run.calls[0]

    def test_returns_false_when_kubectl_get_fails(self, fake_run):
        fake_run.set(["kubectl", "get", "secret", "hf-secret"],
                     _completed(returncode=1, stderr="not found"))
        assert cluster_ops.secret_exists("hf-secret", "ns-a") is False


# ── ProvisionResult dataclass ─────────────────────────────────────────


class TestProvisionResult:
    def test_default_fields(self):
        r = cluster_ops.ProvisionResult(namespace="ns-a")
        assert r.namespace == "ns-a"
        assert r.steps_ok == []
        assert r.steps_skipped == []
        assert r.steps_failed == []

    def test_diverged_false_when_all_ok(self):
        r = cluster_ops.ProvisionResult(namespace="ns-a", steps_ok=["namespace"])
        assert r.diverged is False

    def test_diverged_true_on_skipped(self):
        r = cluster_ops.ProvisionResult(
            namespace="ns-a",
            steps_ok=["namespace"],
            steps_skipped=[("secrets", "no value")],
        )
        assert r.diverged is True

    def test_diverged_true_on_failed(self):
        r = cluster_ops.ProvisionResult(
            namespace="ns-a",
            steps_failed=[("rbac", "kubectl forbidden")],
        )
        assert r.diverged is True


# ── provision_namespace ───────────────────────────────────────────────


def _baseline_cluster_config(**overrides) -> dict:
    cfg = {
        "is_openshift": False,
        "namespaces": ["ns-a", "ns-b"],
        "storage_class": "",
        "secret_names": {
            "hf_token": "hf-secret",
            "github_token": "github-token",
            "registry_creds": "registry-secret",
        },
        "workspaces": {
            "data-storage": {"persistentVolumeClaim": {"claimName": "data-pvc"}},
            "source": {"persistentVolumeClaim": {"claimName": "source-pvc"}},
        },
    }
    cfg.update(overrides)
    return cfg


def _rbac_yaml_paths_exist(monkeypatch, exists=True):
    # Pretend the RBAC YAML files exist on disk (cluster_ops reads .text on them).
    monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: exists)
    monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: f"# {self.name}\n")


def _tekton_yamls_present(monkeypatch, files=None):
    """Stub Path.exists/glob so _step_tekton sees a couple of YAMLs."""
    files = files or ["step-a.yaml", "step-b.yaml"]

    real_path = cluster_ops.Path

    def fake_glob(self, pattern):
        if pattern == "*.yaml":
            return [real_path(f"/fake/{name}") for name in files]
        return []

    monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
    monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# fake\n")
    monkeypatch.setattr(cluster_ops.Path, "glob", fake_glob)


class TestProvisionNamespace:
    def test_happy_path_all_steps_ok_kubectl(self, fake_run, monkeypatch):
        """Default cluster (not OpenShift): namespace + rbac + secrets + pvc + tekton all apply cleanly."""
        _tekton_yamls_present(monkeypatch)
        # Namespace doesn't exist yet → create succeeds.
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=1))
        fake_run.set(["kubectl", "create", "ns", "ns-a"], _completed(returncode=0))
        # PVC pre-checks miss; apply succeeds (default).
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "hf-XXX",
                "github_token": "ghp-XXX",
                "registry_creds": {"server": "quay.io", "user": "u", "token": "t"},
            },
        )
        assert result.namespace == "ns-a"
        assert result.steps_ok == ["namespace", "rbac", "secrets", "pvc", "tekton"]
        assert result.steps_skipped == []
        assert result.steps_failed == []
        assert result.diverged is False

    def test_openshift_uses_oc_for_namespace(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["oc", "get", "project", "ns-a"], _completed(returncode=1))
        fake_run.set(["oc", "new-project", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(is_openshift=True),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        # An oc new-project call was issued (and no kubectl create ns).
        cmds = [tuple(c[:3]) for c in fake_run.calls]
        assert ("oc", "new-project", "ns-a") in cmds
        assert ("kubectl", "create", "ns") not in [tuple(c[:3]) for c in fake_run.calls]

    def test_existing_namespace_is_noop(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        # Namespace pre-check returns success → no create attempted.
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        cmds = [tuple(c[:3]) for c in fake_run.calls]
        assert ("kubectl", "create", "ns") not in cmds

    def test_namespace_already_exists_race_is_ok(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=1))
        fake_run.set(["kubectl", "create", "ns", "ns-a"],
                     _completed(returncode=1, stderr='Error from server (AlreadyExists)'))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        assert "namespace" in result.steps_ok

    def test_namespace_create_failure_recorded(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=1))
        fake_run.set(["kubectl", "create", "ns", "ns-a"],
                     _completed(returncode=1, stderr="quota exceeded"))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        assert ("namespace", "quota exceeded") in result.steps_failed
        # Later steps still run — provision_namespace doesn't short-circuit
        # on per-step failures; surfacing every divergence is the design.
        assert result.diverged is True

    def test_no_short_circuit_on_early_failure(self, fake_run, monkeypatch):
        """provision_namespace must attempt every sub-step even after an
        earlier one fails. The invariant is 'every sub-step lands in
        exactly one of steps_ok / steps_skipped / steps_failed' — a
        regression that adds an early return on first failure would lose
        coverage of the later steps in the ProvisionResult.
        """
        _tekton_yamls_present(monkeypatch)
        # Namespace step fails on a non-AlreadyExists error.
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=1))
        fake_run.set(["kubectl", "create", "ns", "ns-a"],
                     _completed(returncode=1, stderr="quota exceeded"))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        # Every one of the five sub-steps lands in exactly one of the three
        # lists. No exception escapes provision_namespace.
        all_attempted = (
            set(result.steps_ok)
            | {s for s, _ in result.steps_skipped}
            | {s for s, _ in result.steps_failed}
        )
        assert all_attempted == set(cluster_ops._PROVISION_STEPS)
        assert (
            len(result.steps_ok)
            + len(result.steps_skipped)
            + len(result.steps_failed)
            == len(cluster_ops._PROVISION_STEPS)
        )
        # The failed step is recorded; later steps did run (rbac apply
        # default-returns 0, so it lands in steps_ok).
        assert ("namespace", "quota exceeded") in result.steps_failed
        assert "rbac" in result.steps_ok

    def test_skip_arg_suppresses_named_steps(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
            skip=["namespace", "rbac", "secrets", "pvc", "tekton"],
        )
        assert result.steps_ok == []
        assert [s for s, _ in result.steps_skipped] == list(cluster_ops._PROVISION_STEPS)
        # No kubectl/oc calls were issued — skip suppresses execution.
        assert fake_run.calls == []

    def test_skip_unknown_step_is_ignored(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # Unknown step in skip is a no-op — for forward compat with future steps.
        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
            skip=["nonexistent-step"],
        )
        assert "namespace" in result.steps_ok  # known steps still ran

    def test_secret_with_value_is_applied(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # kubectl create secret --dry-run produces the manifest YAML.
        fake_run.set(["kubectl", "create", "secret", "generic", "hf-secret"],
                     _completed(returncode=0, stdout="apiVersion: v1\nkind: Secret\n"))

        cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "hf-XXX",
                "github_token": "ghp-XXX",
                "registry_creds": {"server": "quay.io", "user": "u", "token": "t"},
            },
        )
        # The --from-literal carries the value through.
        create_calls = [c for c in fake_run.calls
                        if c[:4] == ["kubectl", "create", "secret", "generic"]]
        assert any("--from-literal=HF_TOKEN=hf-XXX" in c for c in create_calls)

    def test_secret_value_absent_but_secret_exists_is_reused(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # Pre-installed secrets exist:
        fake_run.set(["kubectl", "get", "secret", "hf-secret"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "secret", "github-token"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "secret", "registry-secret"], _completed(returncode=0))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={},  # no values supplied
        )
        # secrets step is OK (reuse is the idempotent happy path).
        assert "secrets" in result.steps_ok
        # No create-secret calls were issued.
        create_calls = [c for c in fake_run.calls if c[:3] == ["kubectl", "create", "secret"]]
        assert create_calls == []

    def test_secret_value_absent_and_secret_missing_is_skipped(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # All secret pre-checks miss.
        fake_run.set(["kubectl", "get", "secret"], _completed(returncode=1))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={},
        )
        skipped_steps = [s for s, _ in result.steps_skipped]
        assert "secrets" in skipped_steps
        # The reason mentions every missing secret.
        secrets_reason = next(reason for s, reason in result.steps_skipped if s == "secrets")
        assert "hf_token" in secrets_reason
        assert "github_token" in secrets_reason
        assert "registry_creds" in secrets_reason

    def test_secret_partial_value_skipped(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # github-token Secret pre-exists; hf-secret and registry-secret missing.
        fake_run.set(["kubectl", "get", "secret", "github-token"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "secret"], _completed(returncode=1))
        fake_run.set(["kubectl", "create", "secret", "generic", "hf-secret"],
                     _completed(returncode=0, stdout="apiVersion: v1\nkind: Secret\n"))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={"hf_token": "hf-XXX"},
        )
        # secrets step partially applied: hf applied, github reused, registry missing.
        skipped_reasons = dict(result.steps_skipped)
        assert "secrets" in skipped_reasons
        assert "registry_creds" in skipped_reasons["secrets"]

    def test_unknown_secret_key_distinct_error(self, fake_run, monkeypatch):
        # cluster_config declares a secret key the builder doesn't know about.
        # Failure reason must name the key, not conflate with "incomplete dict".
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        cfg = _baseline_cluster_config()
        cfg["secret_names"] = {"futuristic_token": "future-secret"}

        result = cluster_ops.provision_namespace(
            "ns-a", cfg,
            secret_values={"futuristic_token": "some-value"},
        )
        failed = dict(result.steps_failed)
        assert "secrets" in failed
        assert "unknown secret key 'futuristic_token'" in failed["secrets"]

    def test_incomplete_docker_creds_distinct_error(self, fake_run, monkeypatch):
        # registry_creds dict is missing 'token'. Operator-facing error must
        # name the missing field, not say "unknown secret key".
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        cfg = _baseline_cluster_config()
        cfg["secret_names"] = {"registry_creds": "registry-secret"}

        result = cluster_ops.provision_namespace(
            "ns-a", cfg,
            secret_values={"registry_creds": {"server": "quay.io", "user": "u"}},
        )
        failed = dict(result.steps_failed)
        assert "secrets" in failed
        assert "missing required fields: token" in failed["secrets"]
        # And NOT the unknown-key message:
        assert "unknown secret key" not in failed["secrets"]

    def test_non_dict_docker_creds_distinct_error(self, fake_run, monkeypatch):
        # Operator passes a string when a dict is expected.
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        cfg = _baseline_cluster_config()
        cfg["secret_names"] = {"registry_creds": "registry-secret"}

        result = cluster_ops.provision_namespace(
            "ns-a", cfg,
            secret_values={"registry_creds": "not-a-dict"},
        )
        failed = dict(result.steps_failed)
        assert "secrets" in failed
        assert "not a dict" in failed["secrets"]

    def test_dry_run_failure_routes_through_provision_result(self, fake_run, monkeypatch):
        # If kubectl create --dry-run somehow returns non-zero, the failure
        # must land on ProvisionResult.steps_failed, not raise out as
        # CalledProcessError. The invariant is "every sub-step lands in
        # steps_ok|skipped|failed" — no exception escapes.
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        fake_run.set(
            ["kubectl", "create", "secret", "generic", "hf-secret"],
            _completed(returncode=1, stderr="dry-run failed for reasons"),
        )
        cfg = _baseline_cluster_config()
        cfg["secret_names"] = {"hf_token": "hf-secret"}

        result = cluster_ops.provision_namespace(
            "ns-a", cfg, secret_values={"hf_token": "hf-XXX"},
        )
        failed = dict(result.steps_failed)
        assert "secrets" in failed
        assert "dry-run failed" in failed["secrets"]

    def test_rbac_cluster_forbidden_skipped_not_failed(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # Every kubectl apply -f - call gets a Forbidden mentioning ClusterRole;
        # the matcher only suppresses on best-effort YAMLs, so the required ones
        # would normally fail. Force them through by giving required path applies
        # a 0 and only the optional ones the Forbidden response.
        applies = []

        def custom_run(cmd, *, check=True, capture=False, input=None):
            applies.append((list(cmd), input))
            if cmd[:2] == ["kubectl", "apply"] and input and "roles-cluster" in input:
                return _completed(returncode=1,
                                  stderr="Error: forbidden: User cannot create resource clusterrole")
            if cmd[:2] == ["kubectl", "apply"] and input and "sim2real-runner-cluster" in input:
                return _completed(returncode=1,
                                  stderr="Error: forbidden: User cannot create resource clusterrolebinding")
            return _completed(returncode=0)

        monkeypatch.setattr(cluster_ops, "_run", custom_run)
        # Also need filesystem stubs:
        _rbac_yaml_paths_exist(monkeypatch)

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        # rbac is skipped (not failed) because every Forbidden was on a
        # best-effort cluster-scoped YAML.
        skipped_reasons = dict(result.steps_skipped)
        assert "rbac" in skipped_reasons
        assert "cluster" in skipped_reasons["rbac"].lower()

    def test_rbac_other_failure_is_failed(self, fake_run, monkeypatch):
        _rbac_yaml_paths_exist(monkeypatch)
        # Required YAML apply fails for a non-cluster-RBAC reason.
        def custom_run(cmd, *, check=True, capture=False, input=None):
            if cmd[:2] == ["kubectl", "apply"] and input:
                return _completed(returncode=1, stderr="error: quota exceeded")
            if cmd[:3] == ["kubectl", "get"] and "ns" in cmd[2:]:
                return _completed(returncode=0)
            return _completed(returncode=0)
        monkeypatch.setattr(cluster_ops, "_run", custom_run)
        # We need _step_tekton not to find any YAMLs to apply for this test.
        monkeypatch.setattr(cluster_ops.Path, "glob", lambda self, pattern: [])

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        failed_steps = [s for s, _ in result.steps_failed]
        assert "rbac" in failed_steps

    def test_rbac_read_text_oserror_routed_to_failed(self, fake_run, monkeypatch):
        # The "no exception escapes provision_namespace" invariant requires
        # that an OSError on read_text() lands in steps_failed, not propagates.
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        def boom_read(self, *_a, **_kw):
            raise PermissionError(13, "Permission denied", str(self))
        monkeypatch.setattr(cluster_ops.Path, "read_text", boom_read)

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        failed = dict(result.steps_failed)
        assert "rbac" in failed
        assert "read failed" in failed["rbac"]
        # The dispatch loop kept going — every sub-step is in exactly one list.
        all_attempted = (
            set(result.steps_ok)
            | {s for s, _ in result.steps_skipped}
            | {s for s, _ in result.steps_failed}
        )
        assert all_attempted == set(cluster_ops._PROVISION_STEPS)

    def test_tekton_glob_oserror_routed_to_failed(self, fake_run, monkeypatch):
        # PermissionError on Path.glob (e.g. submodule dir the operator can't
        # descend into) must surface as steps_failed, not raise out.
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        monkeypatch.setattr(cluster_ops.Path, "read_text", lambda self: "# fake\n")

        def boom_glob(self, pattern):
            raise PermissionError(13, "Permission denied", str(self))
        monkeypatch.setattr(cluster_ops.Path, "glob", boom_glob)

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        failed = dict(result.steps_failed)
        assert "tekton" in failed
        assert "cannot list" in failed["tekton"]

    def test_pvc_existing_skipped(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        # All PVC pre-checks hit.
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=0))

        cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        # No kubectl apply on a manifest containing the PVC kind happened.
        pvc_applies = [
            (cmd, inp) for cmd, inp in zip(fake_run.calls, fake_run.inputs)
            if cmd[:2] == ["kubectl", "apply"] and inp and "PersistentVolumeClaim" in inp
        ]
        assert pvc_applies == []

    def test_pvc_creates_with_storage_class(self, fake_run, monkeypatch):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(storage_class="gp3"),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        pvc_manifests = [
            inp for cmd, inp in zip(fake_run.calls, fake_run.inputs)
            if cmd[:2] == ["kubectl", "apply"] and inp and "PersistentVolumeClaim" in inp
        ]
        assert pvc_manifests, "expected at least one PVC apply"
        for manifest in pvc_manifests:
            assert "storageClassName: gp3" in manifest
            assert "ReadWriteMany" in manifest
            assert "50Gi" in manifest

    def test_tekton_skipped_when_no_yamls_found(self, fake_run, monkeypatch):
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))
        # tekton dirs don't exist:
        monkeypatch.setattr(cluster_ops.Path, "exists",
                            lambda self: "tekton" not in self.parts)

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        skipped = dict(result.steps_skipped)
        assert "tekton" in skipped


# ── apply_cluster_resources ───────────────────────────────────────────


class TestApplyClusterResources:
    def test_applies_pipeline_yaml_to_each_namespace(self, fake_run, monkeypatch, tmp_path):
        # Stub pipeline.yaml existence.
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        cluster_ops.write_cluster_config(
            "ocp-east", {"namespaces": ["ns-a", "ns-b", "ns-c"]},
        )
        cluster_ops.apply_cluster_resources("ocp-east")
        applies = [c for c in fake_run.calls if c[:3] == ["kubectl", "apply", "-f"]]
        ns_flags = [c[-1] for c in applies]
        assert ns_flags == ["-n=ns-a", "-n=ns-b", "-n=ns-c"]

    def test_pipeline_yaml_missing_raises(self, fake_run, monkeypatch):
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: False)
        with pytest.raises(FileNotFoundError):
            cluster_ops.apply_cluster_resources("ocp-east")

    def test_idempotent_repeat_invocations_succeed(self, fake_run, monkeypatch):
        # kubectl apply is idempotent — same call sequence on the second run.
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        cluster_ops.write_cluster_config("ocp-east", {"namespaces": ["ns-a"]})

        cluster_ops.apply_cluster_resources("ocp-east")
        first_run_calls = list(fake_run.calls)
        cluster_ops.apply_cluster_resources("ocp-east")
        # Same command issued again.
        second_run_calls = fake_run.calls[len(first_run_calls):]
        assert second_run_calls == first_run_calls

    def test_no_namespaces_in_config_is_noop(self, fake_run, monkeypatch):
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        cluster_ops.write_cluster_config("ocp-east", {"namespaces": []})
        cluster_ops.apply_cluster_resources("ocp-east")
        # No kubectl apply -f calls.
        applies = [c for c in fake_run.calls if c[:3] == ["kubectl", "apply", "-f"]]
        assert applies == []

    def test_per_namespace_failure_raises(self, fake_run, monkeypatch):
        monkeypatch.setattr(cluster_ops.Path, "exists", lambda self: True)
        cluster_ops.write_cluster_config("ocp-east", {"namespaces": ["ns-a"]})

        def boom(cmd, *, check=True, capture=False, input=None):
            if cmd[:3] == ["kubectl", "apply", "-f"]:
                raise subprocess.CalledProcessError(1, cmd, "", "stderr")
            return _completed(returncode=0)
        monkeypatch.setattr(cluster_ops, "_run", boom)

        with pytest.raises(subprocess.CalledProcessError):
            cluster_ops.apply_cluster_resources("ocp-east")


# ── _envsubst helper (pure-Python envsubst replacement) ───────────────


class TestEnvsubst:
    def test_substitutes_dollar_var(self):
        assert cluster_ops._envsubst("ns: $NAMESPACE\n", {"NAMESPACE": "ns-a"}) == "ns: ns-a\n"

    def test_substitutes_braced_var(self):
        assert cluster_ops._envsubst("ns: ${NAMESPACE}\n", {"NAMESPACE": "ns-a"}) == "ns: ns-a\n"

    def test_missing_key_left_intact(self):
        assert cluster_ops._envsubst("ns: $UNSET", {}) == "ns: $UNSET"

    def test_multiple_vars(self):
        out = cluster_ops._envsubst(
            "primary: $PRIMARY_NAMESPACE\nthis: $NAMESPACE\n",
            {"PRIMARY_NAMESPACE": "ns-a", "NAMESPACE": "ns-b"},
        )
        assert "primary: ns-a" in out and "this: ns-b" in out


class TestProvisionNamespaceProgressOutput:
    """Regression for #441.

    provision_namespace used to run every sub-step (namespace, RBAC,
    secrets, PVC, tekton) silently. Operators saw a single summary
    line at the end and could not tell whether the command was making
    progress or hanging. The fix emits an info() log per sub-step
    (start + result) and per major kubectl apply.
    """

    def test_happy_path_emits_progress_per_step(self, fake_run, monkeypatch, capsys):
        _tekton_yamls_present(monkeypatch)
        fake_run.set(["kubectl", "get", "ns", "ns-a"], _completed(returncode=1))
        fake_run.set(["kubectl", "create", "ns", "ns-a"], _completed(returncode=0))
        fake_run.set(["kubectl", "get", "pvc"], _completed(returncode=1))

        result = cluster_ops.provision_namespace(
            "ns-a",
            _baseline_cluster_config(),
            secret_values={
                "hf_token": "v", "github_token": "v",
                "registry_creds": {"server": "s", "user": "u", "token": "t"},
            },
        )
        assert result.diverged is False

        out = capsys.readouterr().out
        # Namespace banner appears.
        assert "provisioning namespace: ns-a" in out
        # Every sub-step names itself in a start line ("step..." then
        # "step: reason" on success). Assert both present per step.
        for step in cluster_ops._PROVISION_STEPS:
            assert f"{step}..." in out, f"missing start log for {step}"
            assert f"{step}:" in out, f"missing result log for {step}"
