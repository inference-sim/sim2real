"""Tests for deploy.py --remote run mode."""

import argparse
import json

import pytest
from unittest.mock import patch

import pipeline.deploy as mod


def _make_run_args(*, remote=False, workload=None, only=None, package=None,
                   status=None, force=False, skip_build=False,
                   skip_teardown=False,
                   max_retries=2, poll_interval=30, gpu_resource_type=None,
                   default_gpu_cost=1, pending_threshold=600,
                   max_pending_stalls=10,
                   shadow_ttl=120):
    return argparse.Namespace(
        remote=remote, workload=workload, only=only, package=package,
        status=status, force=force, skip_build=skip_build,
        skip_teardown=skip_teardown,
        max_retries=max_retries, poll_interval=poll_interval,
        gpu_resource_type=gpu_resource_type, default_gpu_cost=default_gpu_cost,
        pending_threshold=pending_threshold, max_pending_stalls=max_pending_stalls,
        shadow_ttl=shadow_ttl,
    )


# ── Parser tests ────────────────────────────────────────────────────────────

def test_run_parser_has_remote_flag():
    args = mod.build_parser().parse_args(["run", "--remote"])
    assert args.remote is True


def test_run_parser_remote_default_false():
    args = mod.build_parser().parse_args(["run"])
    assert args.remote is False


# ── _collect_run_flags tests ────────────────────────────────────────────────

def test_collect_run_flags_defaults():
    args = _make_run_args()
    assert mod._collect_run_flags(args) == []


def test_collect_run_flags_workload():
    args = _make_run_args(workload="wl-smoke")
    assert mod._collect_run_flags(args) == ["--workload", "wl-smoke"]


def test_collect_run_flags_force():
    args = _make_run_args(force=True)
    assert mod._collect_run_flags(args) == ["--force"]


def test_collect_run_flags_non_default_retries():
    args = _make_run_args(max_retries=5)
    assert mod._collect_run_flags(args) == ["--max-retries", "5"]


def test_collect_run_flags_skip_teardown():
    args = _make_run_args(skip_teardown=True)
    assert "--skip-teardown" in mod._collect_run_flags(args)


def test_collect_run_flags_skip_teardown_absent_by_default():
    args = _make_run_args()
    assert "--skip-teardown" not in mod._collect_run_flags(args)


def test_collect_run_flags_non_default_shadow_ttl():
    args = _make_run_args(shadow_ttl=60)
    flags = mod._collect_run_flags(args)
    assert "--shadow-ttl" in flags
    idx = flags.index("--shadow-ttl")
    assert flags[idx + 1] == "60"


def test_collect_run_flags_default_shadow_ttl_not_forwarded():
    args = _make_run_args(shadow_ttl=120)
    flags = mod._collect_run_flags(args)
    assert "--shadow-ttl" not in flags


# ── skip-teardown parser tests ─────────────────────────────────────────────

def test_run_parser_skip_teardown_flag():
    args = mod.build_parser().parse_args(["run", "--skip-teardown"])
    assert args.skip_teardown is True


def test_run_parser_skip_teardown_default_false():
    args = mod.build_parser().parse_args(["run"])
    assert args.skip_teardown is False


# ── skip-teardown param injection tests ────────────────────────────────────

def _inject_skip_teardown(pr_data):
    """Simulate the injection logic from deploy.py _cmd_run."""
    params = pr_data.setdefault("spec", {}).setdefault("params", [])
    for param in params:
        if param["name"] == "skipTeardown":
            param["value"] = "true"
            break
    else:
        params.append({"name": "skipTeardown", "value": "true"})
    return pr_data


def test_skip_teardown_injection_appends_new_param():
    pr_data = {"spec": {"params": [{"name": "namespace", "value": "ns1"}]}}
    _inject_skip_teardown(pr_data)
    names = {p["name"]: p["value"] for p in pr_data["spec"]["params"]}
    assert names["skipTeardown"] == "true"
    assert names["namespace"] == "ns1"


def test_skip_teardown_injection_updates_existing_param():
    pr_data = {"spec": {"params": [{"name": "skipTeardown", "value": "false"}]}}
    _inject_skip_teardown(pr_data)
    assert pr_data["spec"]["params"][0]["value"] == "true"


def test_skip_teardown_injection_missing_spec():
    pr_data = {}
    _inject_skip_teardown(pr_data)
    assert pr_data["spec"]["params"] == [{"name": "skipTeardown", "value": "true"}]


def test_skip_teardown_injection_missing_params():
    pr_data = {"spec": {}}
    _inject_skip_teardown(pr_data)
    assert pr_data["spec"]["params"] == [{"name": "skipTeardown", "value": "true"}]


# ── _check_existing_job tests ──────────────────────────────────────────────

def test_check_existing_job_active(monkeypatch):
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps({"status": {"active": 1}})
            stderr = ""
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    assert mod._check_existing_job("ns") == "active"


def test_check_existing_job_completed(monkeypatch):
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps({"status": {}})
            stderr = ""
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    assert mod._check_existing_job("ns") == "completed"


def test_check_existing_job_not_found(monkeypatch):
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = 'Error from server (NotFound): jobs.batch "sim2real-orchestrator" not found'
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    assert mod._check_existing_job("ns") is None


# ── _wait_for_job_pod tests ────────────────────────────────────────────────

def test_wait_for_pod_running(monkeypatch):
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps({
                "items": [{"status": {"phase": "Running", "containerStatuses": []}}],
            })
            stderr = ""
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    mod._wait_for_job_pod("ns", timeout=10, poll=1)


def test_wait_for_pod_image_pull_backoff(monkeypatch):
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps({
                "items": [{"status": {
                    "phase": "Pending",
                    "containerStatuses": [{"state": {"waiting": {
                        "reason": "ImagePullBackOff",
                        "message": "Back-off pulling image",
                    }}}],
                }}],
            })
            stderr = ""
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    with pytest.raises(SystemExit) as exc_info:
        mod._wait_for_job_pod("ns", timeout=10, poll=1)
    assert exc_info.value.code == 1


def test_wait_for_pod_no_pods_retries(monkeypatch):
    call_count = [0]

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        call_count[0] += 1
        if call_count[0] <= 2:
            class _Empty:
                returncode = 0
                stdout = json.dumps({"items": []})
                stderr = ""
            return _Empty()
        class _Running:
            returncode = 0
            stdout = json.dumps({
                "items": [{"status": {"phase": "Running", "containerStatuses": []}}],
            })
            stderr = ""
        return _Running()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    mod._wait_for_job_pod("ns", timeout=300, poll=1)
    assert call_count[0] == 3


def test_wait_for_pod_failed_phase_exits(monkeypatch):
    """Pod with phase=Failed triggers immediate exit."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps({
                "items": [{"status": {
                    "phase": "Failed",
                    "message": "OOMKilled",
                    "containerStatuses": [],
                }}],
            })
            stderr = ""
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    with pytest.raises(SystemExit) as exc_info:
        mod._wait_for_job_pod("ns", timeout=10, poll=1)
    assert exc_info.value.code == 1


def test_wait_for_pod_failed_surfaces_logs(monkeypatch, capsys):
    """A container exiting non-zero (empty pod.status.message) surfaces the
    container terminated detail and the orchestrator pod logs. Issue #276."""
    pod_json = json.dumps({
        "items": [{
            "metadata": {"name": "orch-pod-xyz"},
            "status": {
                "phase": "Failed",
                "containerStatuses": [{
                    "name": "orchestrator",
                    "state": {"terminated": {"exitCode": 2, "reason": "Error"}},
                }],
            },
        }],
    })
    log_text = "[ERROR] --workload: unrecognized values ['balanced-20']"
    log_cmds = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stderr = ""
        r = _R()
        if "logs" in cmd:
            log_cmds.append(cmd)
            r.stdout = log_text
        else:
            r.stdout = pod_json
        return r

    monkeypatch.setattr(mod, "run", fake_run)
    with pytest.raises(SystemExit) as exc_info:
        mod._wait_for_job_pod("ns", timeout=10, poll=1)
    assert exc_info.value.code == 1
    out = capsys.readouterr()
    combined = out.out + out.err
    # Container terminated detail appears in the header.
    assert "orchestrator exited 2 (Error)" in combined
    # The actual diagnostic from the pod logs is surfaced.
    assert log_text in combined
    # Logs are fetched for the orchestrator container with a bounded tail.
    assert log_cmds, "expected a kubectl logs call"
    cmd = log_cmds[0]
    assert "orch-pod-xyz" in cmd
    assert cmd[cmd.index("-c") + 1] == "orchestrator"
    assert any(a.startswith("--tail") for a in cmd)


def test_report_failed_pod_init_container_in_header(monkeypatch, capsys):
    """A non-zero terminated init container surfaces in the failure header,
    even when the orchestrator container never started. Issue #276."""
    pod = {
        "metadata": {"name": "orch-pod-xyz"},
        "status": {
            "phase": "Failed",
            "initContainerStatuses": [{
                "name": "fetch-inputs",
                "state": {"terminated": {"exitCode": 1, "reason": "Error"}},
            }],
            "containerStatuses": [],
        },
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1  # orchestrator container never ran -> no logs
            stdout = ""
            stderr = "container orchestrator is not valid"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    mod._report_failed_pod(pod, "ns")
    combined = "".join(capsys.readouterr())
    assert "fetch-inputs exited 1 (Error)" in combined


def test_report_failed_pod_no_logs_no_crash(monkeypatch, capsys):
    """If the orchestrator container never started (no logs), reporting still
    succeeds and prints the pod-level message."""
    pod = {
        "metadata": {"name": "orch-pod-xyz"},
        "status": {"phase": "Failed", "message": "Evicted",
                   "containerStatuses": []},
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "container not found"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    mod._report_failed_pod(pod, "ns")
    combined = "".join(capsys.readouterr())
    assert "Orchestrator pod failed: Evicted" in combined


def test_wait_for_pod_consecutive_kubectl_failures_exits(monkeypatch):
    """Three consecutive kubectl failures trigger early exit."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "connection refused"
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    with pytest.raises(SystemExit) as exc_info:
        mod._wait_for_job_pod("ns", timeout=300, poll=1)
    assert exc_info.value.code == 1


def test_wait_for_pod_init_container_image_pull_exits(monkeypatch):
    """ImagePullBackOff in initContainerStatuses triggers fail-fast."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps({
                "items": [{"status": {
                    "phase": "Pending",
                    "initContainerStatuses": [{
                        "state": {"waiting": {
                            "reason": "ImagePullBackOff",
                            "message": "Back-off pulling image",
                        }},
                    }],
                    "containerStatuses": [],
                }}],
            })
            stderr = ""
        return _R()
    monkeypatch.setattr(mod, "run", fake_run)
    with pytest.raises(SystemExit) as exc_info:
        mod._wait_for_job_pod("ns", timeout=10, poll=1)
    assert exc_info.value.code == 1


# ── _cmd_run_remote tests ──────────────────────────────────────────────────

def _setup_run_dir(tmp_path):
    """Create minimal workspace structure for _cmd_run_remote."""
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    (workspace / "setup_config.json").write_text("{}")
    (run_dir / "run_metadata.json").write_text(json.dumps({
        "component_image": "registry.example.com/epp:latest",
    }))
    (cluster_dir / "pipelinerun-baseline.yaml").write_text("apiVersion: v1")
    return run_dir


def _mock_subprocess_ok(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


def test_run_remote_refuses_when_active(monkeypatch, tmp_path):
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: "active")
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    args = _make_run_args(remote=True, skip_build=True)
    setup_config = {"orchestrator_image": "img:latest"}
    cluster_config = {"namespaces": ["ns"]}

    with pytest.raises(SystemExit) as exc_info:
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)
    assert exc_info.value.code == 1


def test_run_remote_deletes_completed_job(monkeypatch, tmp_path):
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: "completed")
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    calls = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    with patch("subprocess.run", side_effect=_mock_subprocess_ok):
        args = _make_run_args(remote=True, skip_build=True)
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    delete_calls = [c for c in calls if "delete" in c]
    assert len(delete_calls) == 1
    assert "sim2real-orchestrator" in delete_calls[0]


def test_run_remote_completed_delete_failure_exits(monkeypatch, tmp_path, capsys):
    """If deleting a completed Job fails, exit with error."""
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: "completed")
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: forbidden"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    args = _make_run_args(remote=True, skip_build=True)
    setup_config = {"orchestrator_image": "img:latest"}
    cluster_config = {"namespaces": ["ns"]}

    with pytest.raises(SystemExit) as exc_info:
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)
    assert exc_info.value.code == 1
    assert "forbidden" in capsys.readouterr().err.lower()


def test_run_remote_creates_configmap_and_job(monkeypatch, tmp_path):
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    apply_inputs = []

    def fake_subprocess_run(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
        if input:
            apply_inputs.append(json.loads(input))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        args = _make_run_args(remote=True, skip_build=True)
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    assert len(apply_inputs) == 2
    assert apply_inputs[0]["kind"] == "ConfigMap"
    assert apply_inputs[1]["kind"] == "Job"


def test_run_remote_uses_server_side_apply(monkeypatch, tmp_path):
    """ConfigMap and Job apply use --server-side to avoid the 256 KiB last-applied-configuration cap."""
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    apply_cmds = []

    def fake_subprocess_run(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
        if input:
            apply_cmds.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        args = _make_run_args(remote=True, skip_build=True)
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    assert len(apply_cmds) == 2
    for cmd in apply_cmds:
        assert "--server-side" in cmd
        assert "--force-conflicts" in cmd


def test_run_remote_job_uses_initcontainer_and_emptydir(monkeypatch, tmp_path):
    """Job uses initContainer to copy ConfigMap to a writable emptyDir."""
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    apply_inputs = []

    def fake_subprocess_run(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
        if input:
            apply_inputs.append(json.loads(input))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        args = _make_run_args(remote=True, skip_build=True)
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    job = apply_inputs[1]
    spec = job["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in spec["volumes"]}
    assert "config" in vols
    assert "items" in vols["config"]["configMap"]
    assert vols["workspace"] == {"name": "workspace", "emptyDir": {}}
    assert spec["initContainers"][0]["name"] == "copy-inputs"
    mount = spec["containers"][0]["volumeMounts"][0]
    assert mount["name"] == "workspace"
    assert mount["mountPath"] == "/data/workspace"


def test_run_remote_passes_scoping_flags(monkeypatch, tmp_path):
    run_dir = _setup_run_dir(tmp_path)
    cluster_dir = run_dir / "cluster"
    (cluster_dir / "pipelinerun-smoke-baseline.yaml").write_text(
        "metadata:\n  name: pipelinerun-smoke-baseline\n"
        "spec:\n  params:\n"
        "  - name: workloadName\n    value: wl-smoke\n"
        "  - name: phase\n    value: baseline\n"
    )
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    apply_inputs = []

    def fake_subprocess_run(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
        if input:
            apply_inputs.append(json.loads(input))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        args = _make_run_args(remote=True, skip_build=True, workload="wl-smoke")
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    job_dict = apply_inputs[1]
    container_args = job_dict["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--workload" in container_args
    assert "wl-smoke" in container_args


# ── Pre-flight filter validation merges discovered (#414) ──────────────────

def _write_pr_yaml(cluster_dir, stem, workload, package="baseline"):
    (cluster_dir / f"pipelinerun-{stem}.yaml").write_text(
        f"metadata:\n  name: pipelinerun-{stem}\n"
        f"spec:\n  params:\n"
        f"  - name: workloadName\n    value: {workload}\n"
        f"  - name: phase\n    value: {package}\n"
    )


def _existing_progress_entry(workload, package="baseline", status="done"):
    return {
        "workload": workload, "package": package, "status": status,
        "namespace": None, "completed_namespace": None,
        "retries": 0, "pending_stalls": 0,
        "pending_since": None, "running_since": None, "last_duration": None,
    }


def test_run_remote_preflight_accepts_newly_discovered_workload(monkeypatch, tmp_path):
    """Regression for #414. A workload YAML in cluster/ but absent from the
    progress ConfigMap must pass pre-flight validation — mirrors the
    init-loop merge _cmd_run does in-cluster."""
    from pipeline.lib.progress import ConfigMapProgressStore
    run_dir = _setup_run_dir(tmp_path)
    cluster_dir = run_dir / "cluster"
    # Two pair_keys in cluster/: one already known to progress, one newly added.
    _write_pr_yaml(cluster_dir, "existing-baseline", workload="wl-existing")
    _write_pr_yaml(cluster_dir, "newwl-baseline", workload="wl-newwl")
    # Progress only knows about wl-existing — wl-newwl is the newly-added one.
    monkeypatch.setattr(
        ConfigMapProgressStore, "load",
        lambda self: {"wl-existing-baseline": _existing_progress_entry("wl-existing")},
    )
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    apply_inputs = []

    def fake_subprocess_run(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
        if input:
            apply_inputs.append(json.loads(input))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        args = _make_run_args(remote=True, skip_build=True, workload="wl-newwl")
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        # Must NOT SystemExit at pre-flight.
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    # ConfigMap + Job were submitted (we got past pre-flight + image build).
    assert len(apply_inputs) == 2
    assert apply_inputs[0]["kind"] == "ConfigMap"
    assert apply_inputs[1]["kind"] == "Job"
    # And --workload was forwarded to the in-cluster orchestrator unchanged.
    container_args = apply_inputs[1]["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--workload" in container_args
    assert "wl-newwl" in container_args


def test_run_remote_preflight_still_rejects_truly_unknown_workload(monkeypatch, tmp_path, capsys):
    """A --workload value that exists neither in progress nor in cluster/ must
    still be rejected. The #414 fix loosens validation only for keys present
    in cluster/."""
    from pipeline.lib.progress import ConfigMapProgressStore
    run_dir = _setup_run_dir(tmp_path)
    cluster_dir = run_dir / "cluster"
    _write_pr_yaml(cluster_dir, "existing-baseline", workload="wl-existing")
    monkeypatch.setattr(
        ConfigMapProgressStore, "load",
        lambda self: {"wl-existing-baseline": _existing_progress_entry("wl-existing")},
    )
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    args = _make_run_args(remote=True, skip_build=True, workload="wl-bogus")
    setup_config = {"orchestrator_image": "img:latest"}
    cluster_config = {"namespaces": ["ns"]}

    with pytest.raises(SystemExit) as exc_info:
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "wl-bogus" in err
    assert "unrecognized" in err.lower()


def test_run_remote_no_image_exits(monkeypatch, tmp_path):
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    args = _make_run_args(remote=True, skip_build=True)
    setup_config = {}
    cluster_config = {"namespaces": ["ns"]}

    with pytest.raises(SystemExit) as exc_info:
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)
    assert exc_info.value.code == 1


def test_run_remote_configmap_apply_failure_exits(monkeypatch, tmp_path, capsys):
    """kubectl apply for ConfigMap failing exits with error."""
    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    def fake_subprocess_run(cmd, *, input=None, text=True, check=False, capture_output=True, **kw):
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": "forbidden"})()

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        args = _make_run_args(remote=True, skip_build=True)
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        with pytest.raises(SystemExit) as exc_info:
            mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)
    assert exc_info.value.code == 1
    assert "configmap" in capsys.readouterr().err.lower()


# ── main() routing tests ───────────────────────────────────────────────────

def test_main_routes_run_remote(tmp_path, monkeypatch):
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "run", "--remote",
    ])

    remote_calls = []

    def mock_run_remote(args, rd, sc, cc):
        remote_calls.append(True)

    with patch.object(mod, "_cmd_run_remote", mock_run_remote):
        with patch.object(mod, "_load_setup_config", return_value={
            "current_run": "test-run",
        }), patch.object(mod, "_load_cluster_config", return_value={
            "namespaces": ["ns"],
        }):
            mod.main()

    assert len(remote_calls) == 1


def test_main_routes_run_local(tmp_path, monkeypatch):
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr("sys.argv", [
        "deploy.py", "--experiment-root", str(tmp_path), "run",
    ])

    local_calls = []

    def mock_run(args, rd, sc, cc):
        local_calls.append(True)

    with patch.object(mod, "_cmd_run", mock_run):
        with patch.object(mod, "_load_setup_config", return_value={
            "current_run": "test-run",
        }), patch.object(mod, "_load_cluster_config", return_value={
            "namespaces": ["ns"],
        }):
            mod.main()

    assert len(local_calls) == 1


# ── Pre-flight filter validation (#251) ─────────────────────────────────────


def test_run_remote_status_filter_uses_configmap(monkeypatch, tmp_path):
    """--status filter in --remote mode reads from ConfigMap, not disk YAML (#251)."""
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    progress_with_status = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "failed",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "done",
                           "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: progress_with_status.copy())

    with patch("subprocess.run", side_effect=_mock_subprocess_ok):
        args = _make_run_args(remote=True, skip_build=True, status="failed")
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)


def test_run_remote_status_filter_rejects_unmatched(monkeypatch, tmp_path):
    """--status filter with no matching pairs still exits with error."""
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    progress_all_done = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0, "pending_stalls": 0, "pending_since": None},
    }
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: progress_all_done.copy())

    args = _make_run_args(remote=True, skip_build=True, status="failed")
    setup_config = {"orchestrator_image": "img:latest"}
    cluster_config = {"namespaces": ["ns"]}

    with pytest.raises(SystemExit) as exc_info:
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)
    assert exc_info.value.code == 1


def test_run_remote_skips_validation_when_configmap_unreachable(monkeypatch, tmp_path, capsys):
    """When ConfigMap is unreachable, pre-flight validation is skipped (not
    errored), and the warning explicitly names what was skipped (issue #287)."""
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = _setup_run_dir(tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_check_existing_job", lambda ns: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_wait_for_job_pod", lambda *a, **kw: None)

    def failing_load(self):
        raise RuntimeError("kubectl not available")

    monkeypatch.setattr(ConfigMapProgressStore, "load", failing_load)

    with patch("subprocess.run", side_effect=_mock_subprocess_ok):
        args = _make_run_args(remote=True, skip_build=True, status="failed")
        setup_config = {"orchestrator_image": "img:latest"}
        cluster_config = {"namespaces": ["ns"]}
        mod._cmd_run_remote(args, run_dir, setup_config, cluster_config)

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "ConfigMap unreachable" in combined
    assert "skipping pre-flight filter validation" in combined
