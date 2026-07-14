"""Tests for remote run support (ConfigMap packing + Job generation)."""

import json

import pytest

from pipeline.lib.remote import (
    CONFIGMAP_NAME,
    JOB_NAME,
    _CM_KEY_ALLOWED_RE,
    _decode_filename_from_cm,
    _encode_filename_for_cm,
    build_orchestrator_job,
    build_run_inputs_configmap,
    _configmap_items,
)


@pytest.fixture()
def workspace(tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    run_dir = workspace_dir / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "cluster").mkdir()
    return workspace_dir, run_dir


CLUSTER_ID = "test-cluster"


def _write_cluster_config(workspace_dir, cluster_id=CLUSTER_ID, payload=None):
    cluster_dir = workspace_dir / "clusters" / cluster_id
    cluster_dir.mkdir(parents=True, exist_ok=True)
    body = payload if payload is not None else {"namespaces": ["ns"]}
    (cluster_dir / "cluster_config.json").write_text(json.dumps(body))


def _write_defaults(workspace_dir, run_dir):
    setup = {"namespace": "ns", "pipeline_name": "p"}
    (workspace_dir / "setup_config.json").write_text(json.dumps(setup))
    _write_cluster_config(workspace_dir)
    meta = {"run_name": "test-run"}
    (run_dir / "run_metadata.json").write_text(json.dumps(meta))
    (run_dir / "cluster" / "pipelinerun-smoke-baseline.yaml").write_text("kind: PipelineRun")


def test_setup_config_packed(workspace):
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert cm["apiVersion"] == "v1"
    assert cm["kind"] == "ConfigMap"
    assert cm["metadata"]["name"] == CONFIGMAP_NAME
    assert cm["metadata"]["namespace"] == "ns"
    assert json.loads(cm["data"]["setup_config.json"]) == {
        "namespace": "ns", "pipeline_name": "p",
    }


def test_run_metadata_packed(workspace):
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert json.loads(cm["data"]["run_metadata.json"]) == {"run_name": "test-run"}


def test_cluster_yamls_packed(workspace):
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    (run_dir / "cluster" / "baseline.yaml").write_text("base: true\n")
    (run_dir / "cluster" / "treatment.yaml").write_text("treat: true\n")
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert cm["data"]["cluster--baseline.yaml"] == "base: true\n"
    assert cm["data"]["cluster--treatment.yaml"] == "treat: true\n"


def test_missing_setup_config(workspace):
    workspace_dir, run_dir = workspace
    (run_dir / "run_metadata.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="setup_config.json"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_missing_run_metadata(workspace):
    workspace_dir, run_dir = workspace
    (workspace_dir / "setup_config.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="run_metadata.json"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_missing_cluster_config_raises(workspace):
    """No clusters/ directory raises FileNotFoundError with provision hint."""
    workspace_dir, run_dir = workspace
    (workspace_dir / "setup_config.json").write_text("{}")
    (run_dir / "run_metadata.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="No cluster registered"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_multiple_clusters_raises(workspace):
    """More than one cluster directory raises RuntimeError (Step 0 single-cluster)."""
    workspace_dir, run_dir = workspace
    (workspace_dir / "setup_config.json").write_text("{}")
    (run_dir / "run_metadata.json").write_text("{}")
    _write_cluster_config(workspace_dir, cluster_id="cluster-a")
    _write_cluster_config(workspace_dir, cluster_id="cluster-b")
    with pytest.raises(RuntimeError, match="Multiple clusters"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_empty_cluster_dir_raises(workspace):
    """Empty cluster/ directory raises FileNotFoundError."""
    workspace_dir, run_dir = workspace
    (workspace_dir / "setup_config.json").write_text("{}")
    (run_dir / "run_metadata.json").write_text("{}")
    _write_cluster_config(workspace_dir)
    with pytest.raises(FileNotFoundError, match="No cluster YAML"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_missing_cluster_dir_raises(tmp_path):
    """Missing cluster/ directory raises FileNotFoundError."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    run_dir = workspace_dir / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (workspace_dir / "setup_config.json").write_text("{}")
    (run_dir / "run_metadata.json").write_text("{}")
    _write_cluster_config(workspace_dir)
    with pytest.raises(FileNotFoundError, match="No cluster YAML"):
        build_run_inputs_configmap(
            run_dir=run_dir, workspace_dir=workspace_dir,
            namespace="ns", run_name="test-run",
        )


def test_cluster_config_packed(workspace):
    """cluster_config.json content is keyed under the cluster id."""
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    key = f"cluster_config--{CLUSTER_ID}"
    assert key in cm["data"]
    assert json.loads(cm["data"][key]) == {"namespaces": ["ns"]}


# --- _configmap_items tests ---


def test_configmap_items_setup_config_at_root():
    """setup_config.json maps to workspace root."""
    data = {"setup_config.json": "{}"}
    items = _configmap_items(data, "run1")
    assert {"key": "setup_config.json", "path": "setup_config.json"} in items


def test_configmap_items_run_metadata_under_run():
    """run_metadata.json maps under runs/<name>/."""
    data = {"run_metadata.json": "{}"}
    items = _configmap_items(data, "run1")
    assert {"key": "run_metadata.json", "path": "runs/run1/run_metadata.json"} in items


def test_configmap_items_cluster_yaml_nested():
    """cluster--foo.yaml maps to runs/<name>/cluster/foo.yaml."""
    data = {"cluster--baseline.yaml": "x", "cluster--pipelinerun-a.yaml": "y"}
    items = _configmap_items(data, "exp-1")
    paths = {i["path"] for i in items}
    assert "runs/exp-1/cluster/baseline.yaml" in paths
    assert "runs/exp-1/cluster/pipelinerun-a.yaml" in paths


def test_configmap_items_cluster_config_under_clusters():
    """cluster_config--<id> maps to clusters/<id>/cluster_config.json."""
    data = {"cluster_config--ocp-east": "{}"}
    items = _configmap_items(data, "run1")
    assert {
        "key": "cluster_config--ocp-east",
        "path": "clusters/ocp-east/cluster_config.json",
    } in items


def test_configmap_items_raises_on_unrecognized_key():
    """Unrecognized keys raise ValueError (producer/consumer mismatch)."""
    data = {"setup_config.json": "{}", "unknown_file.txt": "data"}
    with pytest.raises(ValueError, match="Unrecognized"):
        _configmap_items(data, "run1")


# --- build_orchestrator_job tests ---

RUN_NAME = "exp-001"
NAMESPACE = "sim2real"
IMAGE = "ghcr.io/inference-sim/sim2real-orchestrator:latest"
SAMPLE_DATA = {
    "setup_config.json": "{}",
    "cluster_config--test-cluster": "{}",
    "run_metadata.json": "{}",
    "cluster--baseline.yaml": "x",
}


def _build_job(**overrides):
    defaults = dict(
        namespace=NAMESPACE, image=IMAGE, run_name=RUN_NAME,
        run_flags=["--dry-run"], configmap_data=SAMPLE_DATA,
    )
    defaults.update(overrides)
    return build_orchestrator_job(**defaults)


def test_job_name_matches_constant():
    assert JOB_NAME == "sim2real-orchestrator"


def test_job_structure():
    job = _build_job()
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == JOB_NAME
    assert job["metadata"]["namespace"] == NAMESPACE

    spec = job["spec"]["template"]["spec"]
    assert spec["serviceAccountName"] == "sim2real-runner"
    assert spec["restartPolicy"] == "Never"

    container = spec["containers"][0]
    assert container["image"] == IMAGE
    args = container["args"]
    assert "--experiment-root" in args
    assert "run" in args
    assert "--skip-build" in args
    assert "--dry-run" in args


def test_job_orchestrator_unbuffered():
    job = _build_job()
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["PYTHONUNBUFFERED"] == "1"


def test_job_orchestrator_pod_marker_env_var_set():
    """`SIM2REAL_ORCHESTRATOR_POD=1` is set so deploy.py error paths can emit
    pod-appropriate hints instead of the local "run cluster.py provision"
    message (#562).
    """
    job = _build_job()
    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env.get("SIM2REAL_ORCHESTRATOR_POD") == "1"


def test_job_workspace_is_writable_emptydir():
    """Orchestrator mounts a writable emptyDir at /data/workspace."""
    job = _build_job()
    spec = job["spec"]["template"]["spec"]
    mount = spec["containers"][0]["volumeMounts"][0]
    assert mount["name"] == "workspace"
    assert mount["mountPath"] == "/data/workspace"
    ws_vol = next(v for v in spec["volumes"] if v["name"] == "workspace")
    assert ws_vol == {"name": "workspace", "emptyDir": {}}


def test_job_configmap_volume_is_read_only():
    """ConfigMap is mounted read-only at /data/config with items spec."""
    job = _build_job()
    spec = job["spec"]["template"]["spec"]
    cfg_vol = next(v for v in spec["volumes"] if v["name"] == "config")
    assert cfg_vol["configMap"]["name"] == CONFIGMAP_NAME
    items = cfg_vol["configMap"]["items"]
    paths = {i["path"] for i in items}
    assert "setup_config.json" in paths
    assert "clusters/test-cluster/cluster_config.json" in paths
    assert f"runs/{RUN_NAME}/run_metadata.json" in paths
    assert f"runs/{RUN_NAME}/cluster/baseline.yaml" in paths


def test_job_has_init_container_that_copies_inputs():
    """initContainer copies ConfigMap contents to the writable workspace,
    then symlinks cluster_config.json to the live-cluster-config mount so
    kubelet ConfigMap updates propagate mid-run (issue #571)."""
    job = _build_job()
    spec = job["spec"]["template"]["spec"]
    init = spec["initContainers"][0]
    assert init["name"] == "copy-inputs"
    # sh -c wrapper so cp + ln can chain with set -e.
    assert init["command"][:2] == ["sh", "-c"]
    script = init["command"][2]
    assert "set -e" in script
    assert "cp -r /data/config/. /data/workspace" in script
    assert (
        "ln -sf /data/live/clusters/test-cluster/cluster_config.json "
        "/data/workspace/clusters/test-cluster/cluster_config.json"
        in script
    )
    mounts = {m["name"]: m for m in init["volumeMounts"]}
    assert mounts["config"]["readOnly"] is True
    assert "workspace" in mounts
    # live-cluster-config is NOT mounted in the initContainer — the
    # symlink target only needs to resolve at runtime, not init time.
    assert "live-cluster-config" not in mounts


def test_job_has_live_cluster_config_volume():
    """A second ConfigMap volume projects just the cluster_config key with
    no subPath, so kubelet propagates in-place updates to the runtime
    container (issue #571)."""
    job = _build_job()
    volumes = {v["name"]: v for v in job["spec"]["template"]["spec"]["volumes"]}
    assert "live-cluster-config" in volumes
    live = volumes["live-cluster-config"]
    assert live["configMap"]["name"] == "sim2real-run-inputs"
    items = live["configMap"]["items"]
    assert len(items) == 1
    assert items[0]["key"] == "cluster_config--test-cluster"
    assert items[0]["path"] == "cluster_config.json"
    # Belt-and-suspenders: subPath is not a valid ConfigMap-projection
    # field (subPath belongs on volumeMount, not on volume-items), but
    # assert its absence anyway so a mis-refactor that moves subPath
    # here would fail loudly. The load-bearing "no subPath" check is on
    # the runtime container's volumeMount below.
    assert "subPath" not in items[0]


def test_job_orchestrator_mounts_live_cluster_config():
    """The runtime container mounts live-cluster-config at the path the
    initContainer's symlink points to (issue #571)."""
    job = _build_job()
    orchestrator = job["spec"]["template"]["spec"]["containers"][0]
    mounts = {m["name"]: m for m in orchestrator["volumeMounts"]}
    assert "live-cluster-config" in mounts
    live_mount = mounts["live-cluster-config"]
    assert live_mount["mountPath"] == "/data/live/clusters/test-cluster"
    # Load-bearing invariant: NO subPath on the runtime container's
    # volumeMount. subPath mounts do NOT receive kubelet ConfigMap
    # update propagation, which would break the whole live-refresh
    # design (issue #571). A future refactor that adds subPath here
    # would silently regress remote-mode slot management.
    assert "subPath" not in live_mount


def test_build_orchestrator_job_raises_on_missing_cluster_config_key():
    """Zero cluster_config--* keys in configmap_data is a build-time
    invariant violation. Failing at Job build time (rather than at Pod
    startup) matches the design at pipeline/lib/remote.py:172-177."""
    bad_data = {
        "setup_config.json": "{}",
        # No cluster_config--<id> key.
        "run_metadata.json": "{}",
        "cluster--baseline.yaml": "x",
    }
    with pytest.raises(ValueError, match="Expected exactly one"):
        _build_job(configmap_data=bad_data)


def test_build_orchestrator_job_raises_on_multiple_cluster_config_keys():
    """Two cluster_config--* keys is ambiguous: build_orchestrator_job
    can only mount one at /data/live/clusters/<id>/. Failing loudly
    catches CM-builder invariant drift before Pod startup."""
    bad_data = {
        "setup_config.json": "{}",
        "cluster_config--foo": "{}",
        "cluster_config--bar": "{}",
        "run_metadata.json": "{}",
        "cluster--baseline.yaml": "x",
    }
    with pytest.raises(ValueError, match="Expected exactly one"):
        _build_job(configmap_data=bad_data)


def test_job_experiment_root_matches_mount():
    job = _build_job()
    args = job["spec"]["template"]["spec"]["containers"][0]["args"]
    idx = args.index("--experiment-root")
    assert args[idx + 1] == "/data"


def test_job_backoff_limit_zero():
    job = _build_job()
    assert job["spec"]["backoffLimit"] == 0


def test_job_has_no_active_deadline():
    """No Job-level wall-clock deadline; the orchestrator bounds its own runtime."""
    job = _build_job()
    assert "activeDeadlineSeconds" not in job["spec"]


# --- defaults.yaml bundling tests ---


def test_defaults_yaml_packed(workspace):
    """defaults.yaml content is included in ConfigMap when provided."""
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    defaults_content = "decode:\n  accelerator:\n    count: 2\n"
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
        defaults_content=defaults_content,
    )
    assert cm["data"]["defaults.yaml"] == defaults_content


def test_defaults_yaml_omitted_when_none(workspace):
    """When defaults_content is None, no defaults.yaml key in ConfigMap."""
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    assert "defaults.yaml" not in cm["data"]


def test_configmap_items_defaults_at_root():
    """defaults.yaml maps to workspace root."""
    data = {"defaults.yaml": "content", "setup_config.json": "{}"}
    items = _configmap_items(data, "run1")
    assert {"key": "defaults.yaml", "path": "defaults.yaml"} in items


# --- pipe-shape filename encoding (issue #557) ---


def test_encode_pipe_to_dot7c():
    """`|` is encoded as `.7C` in CM keys."""
    assert (
        _encode_filename_for_cm("pipelinerun-wl-a|baseline|i1.yaml")
        == "pipelinerun-wl-a.7Cbaseline.7Ci1.yaml"
    )


def test_encode_no_pipe_unchanged():
    """Filenames without `|` pass through unchanged."""
    assert _encode_filename_for_cm("baseline.yaml") == "baseline.yaml"
    assert _encode_filename_for_cm("pipelinerun-smoke-baseline.yaml") == (
        "pipelinerun-smoke-baseline.yaml"
    )


def test_decode_dot7c_to_pipe():
    """`.7C` in an encoded key decodes back to `|`."""
    assert (
        _decode_filename_from_cm("pipelinerun-wl-a.7Cbaseline.7Ci1.yaml")
        == "pipelinerun-wl-a|baseline|i1.yaml"
    )


@pytest.mark.parametrize("original", [
    "pipelinerun-code-generation-10|constantceiling|i2.yaml",
    "pipelinerun-interactive-chat-80|softreflective|i1.yaml",
    "pipelinerun-reasoning-0-2|baseline|i2.yaml",
    "baseline.yaml",
    "constantceiling.yaml",
    "pipelinerun-smoke-baseline.yaml",  # legacy dash-shape, no pipes
])
def test_encode_decode_roundtrip(original):
    """Encoding then decoding yields the original filename byte-for-byte."""
    encoded = _encode_filename_for_cm(original)
    assert _CM_KEY_ALLOWED_RE.match(encoded), (
        f"encoded name {encoded!r} contains chars outside "
        f"the ConfigMap key allowed set"
    )
    assert _decode_filename_from_cm(encoded) == original


def test_pipe_shape_filenames_produce_legal_cm_keys(workspace):
    """Pipe-shape pipelinerun filenames yield ConfigMap keys that pass k8s validation.

    Regression for the bug in issue #557: kubectl apply rejected the CM with
    `data[cluster--pipelinerun-code-generation-10|constantceiling|i2.yaml]:
    Invalid value ... regex used for validation is '[-._a-zA-Z0-9]+'` because
    the pair-key grammar (lib/pairkey.py) produces `|`-containing filenames
    that were used verbatim as CM data keys.
    """
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    # Mix of pipe-shape (post-PR-2) and no-pipe (treatment overlay) files as
    # in a real cluster/ directory.
    (run_dir / "cluster" / "baseline.yaml").write_text("t: 1\n")
    (run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml").write_text("p: 1\n")
    (run_dir / "cluster" / "pipelinerun-wl-a|baseline|i2.yaml").write_text("p: 2\n")
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    illegal = [k for k in cm["data"] if not _CM_KEY_ALLOWED_RE.match(k)]
    assert illegal == [], (
        f"kubectl would reject these CM data keys: {illegal}"
    )
    # And no `|` leaked through despite the source filenames containing `|`.
    assert not any("|" in k for k in cm["data"])


def test_pipe_shape_roundtrip_via_configmap_items(workspace):
    """End-to-end: CM key encodes `|`, projected volume `path:` restores `|`.

    This is what the remote orchestrator Job relies on — the projected file
    inside the pod must have the same name as the source on the operator's
    disk so pair-key parsing and workload lookup work identically in-cluster.
    """
    workspace_dir, run_dir = workspace
    _write_defaults(workspace_dir, run_dir)
    # _write_defaults seeds `pipelinerun-smoke-baseline.yaml`; include it in
    # the expected set so the assertion matches the full cluster/ contents.
    extra_filenames = {
        "pipelinerun-code-generation-10|constantceiling|i2.yaml",
        "pipelinerun-interactive-chat-80|softreflective|i1.yaml",
        "baseline.yaml",
    }
    for name in extra_filenames:
        (run_dir / "cluster" / name).write_text(f"# {name}\n")
    source_filenames = extra_filenames | {"pipelinerun-smoke-baseline.yaml"}
    cm = build_run_inputs_configmap(
        run_dir=run_dir, workspace_dir=workspace_dir,
        namespace="ns", run_name="test-run",
    )
    items = _configmap_items(cm["data"], "test-run")
    reconstructed = {
        i["path"].removeprefix("runs/test-run/cluster/")
        for i in items
        if i["path"].startswith("runs/test-run/cluster/")
    }
    assert reconstructed == source_filenames


def test_configmap_items_decodes_pipe_shape_cluster_yaml():
    """`_configmap_items` decodes `.7C` back to `|` when constructing paths."""
    data = {"cluster--pipelinerun-wl-a.7Cbaseline.7Ci1.yaml": "p"}
    items = _configmap_items(data, "run-x")
    assert items == [{
        "key": "cluster--pipelinerun-wl-a.7Cbaseline.7Ci1.yaml",
        "path": "runs/run-x/cluster/pipelinerun-wl-a|baseline|i1.yaml",
    }]


def test_cm_key_allowed_re_matches_k8s_validator():
    """Guard against divergence from Kubernetes' CM-key validation regex.

    The Kubernetes validator's error message quotes the regex as
    ``[-._a-zA-Z0-9]+``. If that ever changes upstream (unlikely), this test
    surfaces the drift.
    """
    # Legal.
    assert _CM_KEY_ALLOWED_RE.match("simple.name")
    assert _CM_KEY_ALLOWED_RE.match("KEY_NAME")
    assert _CM_KEY_ALLOWED_RE.match("key-name")
    assert _CM_KEY_ALLOWED_RE.match("pipelinerun-wl-a.7Cbaseline.7Ci1.yaml")
    # Illegal — anything with `|`.
    assert not _CM_KEY_ALLOWED_RE.match("pipelinerun-wl-a|baseline|i1.yaml")
    # Illegal — spaces, slashes, other punctuation.
    assert not _CM_KEY_ALLOWED_RE.match("with space")
    assert not _CM_KEY_ALLOWED_RE.match("with/slash")
