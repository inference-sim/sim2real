"""Tests for _extract_phase_plans in deploy.py."""

import subprocess
from unittest.mock import patch

from pipeline import deploy


def _cp_result(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


class _RunRouter:
    """Pattern-route each `run()` call to a canned response.

    Patterns are matched in order; first match wins. Each pattern is
    `(matcher, result)` where matcher is a callable taking the cmd list and
    returning bool. Records every call in `.calls` for assertions.
    """

    def __init__(self, patterns):
        self.patterns = patterns
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        for matcher, result in self.patterns:
            if matcher(cmd):
                return result
        # Default: empty success — keeps tests resilient to incidental calls.
        return _cp_result()


def _is_ls(cmd, path):
    """kubectl exec ... -- sh -c 'ls <path> 2>/dev/null' (exact path match)."""
    if cmd[:2] != ["kubectl", "exec"]:
        return False
    shell = cmd[-1] if cmd[-2:-1] == ["-c"] else ""
    return shell == f"ls {path} 2>/dev/null"


def _is_find_dirs(cmd, path):
    """kubectl exec ... -- sh -c 'find <path> -mindepth 1 -maxdepth 1 -type d ...'"""
    if cmd[:2] != ["kubectl", "exec"]:
        return False
    shell = cmd[-1] if cmd[-2:-1] == ["-c"] else ""
    return shell.startswith(f"find {path} ") and "-type d" in shell


def _is_find_yaml_stat(cmd, path):
    """kubectl exec ... -- sh -c 'find <path> ... -name *.yaml -exec stat ...'"""
    if cmd[:2] != ["kubectl", "exec"]:
        return False
    shell = cmd[-1] if cmd[-2:-1] == ["-c"] else ""
    return (
        shell.startswith(f"find {path} ")
        and "-name '*.yaml'" in shell
        and "stat" in shell
    )


def _is_cp(cmd, remote_suffix):
    """kubectl cp ... <namespace>/<pod>:<remote_path> <local> ..."""
    if cmd[:2] != ["kubectl", "cp"]:
        return False
    return any(arg.endswith(remote_suffix) for arg in cmd)


def test_happy_path_single_root_single_flow(tmp_path):
    """Single root, single flow → yamls land under plans/{flow}/."""
    run_name = "demo-run"
    phase = "baseline"
    plans_root = f"/data/{run_name}/plans/{phase}"
    workload = "balanced_20"
    wl_path = f"{plans_root}/{workload}"
    root = "root-20260101-120000-000"
    plan_dir = f"{wl_path}/{root}/plan"
    flow = "flow-control-baseline"
    flow_dir = f"{plan_dir}/{flow}"

    run_dir = tmp_path / "workspace" / "runs" / run_name
    run_dir.mkdir(parents=True)

    router = _RunRouter([
        (lambda c: _is_ls(c, plans_root), _cp_result(stdout=f"{workload}\n")),
        (lambda c: _is_ls(c, wl_path), _cp_result(stdout=f"{root}\n")),
        (lambda c: _is_find_dirs(c, plan_dir), _cp_result(stdout=f"{plan_dir}/{flow}\n")),
        (lambda c: _is_find_yaml_stat(c, flow_dir),
         _cp_result(stdout=(
             f"100 {flow_dir}/01_pvc.yaml\n"
             f"200 {flow_dir}/config.yaml\n"
         ))),
        (lambda c: _is_cp(c, "01_pvc.yaml"), _cp_result()),
        (lambda c: _is_cp(c, "config.yaml"), _cp_result()),
    ])

    with patch.object(deploy, "run", router):
        deploy._extract_phase_plans("pod-x", run_name, phase, "ns-0", run_dir)

    dest = run_dir / "results" / phase / "plans" / flow
    assert dest.is_dir()
    cp_calls = [c for c in router.calls if c[:2] == ["kubectl", "cp"]]
    assert len(cp_calls) == 2
    assert any("01_pvc.yaml" in arg for c in cp_calls for arg in c)
    assert any("config.yaml" in arg for c in cp_calls for arg in c)


def test_picks_latest_root_by_lex_sort(tmp_path):
    """When multiple roots exist, latest by lex sort is used."""
    run_name = "demo-run"
    phase = "baseline"
    plans_root = f"/data/{run_name}/plans/{phase}"
    workload = "balanced_20"
    wl_path = f"{plans_root}/{workload}"
    older = "root-20260101-120000-000"
    newer = "root-20260101-130000-000"
    plan_dir = f"{wl_path}/{newer}/plan"  # MUST be under newer
    flow = "flow-control-baseline"
    flow_dir = f"{plan_dir}/{flow}"

    run_dir = tmp_path / "workspace" / "runs" / run_name
    run_dir.mkdir(parents=True)

    router = _RunRouter([
        (lambda c: _is_ls(c, plans_root), _cp_result(stdout=f"{workload}\n")),
        # ls returns roots out of order to confirm sort
        (lambda c: _is_ls(c, wl_path), _cp_result(stdout=f"{newer}\n{older}\n")),
        (lambda c: _is_find_dirs(c, plan_dir), _cp_result(stdout=f"{plan_dir}/{flow}\n")),
        (lambda c: _is_find_yaml_stat(c, flow_dir),
         _cp_result(stdout=f"100 {flow_dir}/config.yaml\n")),
        (lambda c: _is_cp(c, "config.yaml"), _cp_result()),
    ])

    with patch.object(deploy, "run", router):
        deploy._extract_phase_plans("pod-x", run_name, phase, "ns-0", run_dir)

    # Confirm find/stat targeted the newer root, not the older
    find_calls = [c for c in router.calls if _is_find_dirs(c, plan_dir)]
    assert find_calls, "expected a find under newer root's plan dir"


def test_skip_when_local_up_to_date(tmp_path):
    """Local file with mtime >= remote → no kubectl cp invocation."""
    run_name = "demo-run"
    phase = "baseline"
    plans_root = f"/data/{run_name}/plans/{phase}"
    workload = "balanced_20"
    wl_path = f"{plans_root}/{workload}"
    root = "root-20260101-120000-000"
    plan_dir = f"{wl_path}/{root}/plan"
    flow = "flow-control-baseline"
    flow_dir = f"{plan_dir}/{flow}"

    run_dir = tmp_path / "workspace" / "runs" / run_name
    flow_dest = run_dir / "results" / phase / "plans" / flow
    flow_dest.mkdir(parents=True)
    local_yaml = flow_dest / "config.yaml"
    local_yaml.write_text("local")
    # Force a known local mtime well in the future of the remote.
    import os
    os.utime(local_yaml, (5000, 5000))

    router = _RunRouter([
        (lambda c: _is_ls(c, plans_root), _cp_result(stdout=f"{workload}\n")),
        (lambda c: _is_ls(c, wl_path), _cp_result(stdout=f"{root}\n")),
        (lambda c: _is_find_dirs(c, plan_dir), _cp_result(stdout=f"{plan_dir}/{flow}\n")),
        (lambda c: _is_find_yaml_stat(c, flow_dir),
         _cp_result(stdout=f"100 {flow_dir}/config.yaml\n")),
    ])

    with patch.object(deploy, "run", router):
        deploy._extract_phase_plans("pod-x", run_name, phase, "ns-0", run_dir)

    cp_calls = [c for c in router.calls if c[:2] == ["kubectl", "cp"]]
    assert cp_calls == [], f"expected no kubectl cp calls; got {cp_calls}"


def test_missing_plans_dir_returns_gracefully(tmp_path):
    """No plans/{phase}/ on PVC → return without error or cp call."""
    run_name = "demo-run"
    phase = "baseline"
    plans_root = f"/data/{run_name}/plans/{phase}"

    run_dir = tmp_path / "workspace" / "runs" / run_name
    run_dir.mkdir(parents=True)

    # ls returns rc=1 (missing dir) and empty stdout
    router = _RunRouter([
        (lambda c: _is_ls(c, plans_root), _cp_result(rc=1, stdout="", stderr="")),
    ])

    with patch.object(deploy, "run", router):
        deploy._extract_phase_plans("pod-x", run_name, phase, "ns-0", run_dir)

    # No copy attempted; plans dir not created locally
    cp_calls = [c for c in router.calls if c[:2] == ["kubectl", "cp"]]
    assert cp_calls == []
    assert not (run_dir / "results" / phase / "plans").exists()


def test_setup_dir_filtered_from_flows(tmp_path):
    """A 'setup' subdir under plan/ is treated as metadata and not copied."""
    run_name = "demo-run"
    phase = "baseline"
    plans_root = f"/data/{run_name}/plans/{phase}"
    workload = "balanced_20"
    wl_path = f"{plans_root}/{workload}"
    root = "root-20260101-120000-000"
    plan_dir = f"{wl_path}/{root}/plan"
    flow = "flow-control-baseline"
    flow_dir = f"{plan_dir}/{flow}"

    run_dir = tmp_path / "workspace" / "runs" / run_name
    run_dir.mkdir(parents=True)

    router = _RunRouter([
        (lambda c: _is_ls(c, plans_root), _cp_result(stdout=f"{workload}\n")),
        (lambda c: _is_ls(c, wl_path), _cp_result(stdout=f"{root}\n")),
        # find returns BOTH the flow dir AND a 'setup' dir
        (lambda c: _is_find_dirs(c, plan_dir),
         _cp_result(stdout=f"{plan_dir}/setup\n{plan_dir}/{flow}\n")),
        (lambda c: _is_find_yaml_stat(c, flow_dir),
         _cp_result(stdout=f"100 {flow_dir}/config.yaml\n")),
        (lambda c: _is_cp(c, "config.yaml"), _cp_result()),
    ])

    with patch.object(deploy, "run", router):
        deploy._extract_phase_plans("pod-x", run_name, phase, "ns-0", run_dir)

    # Confirm we never tried to stat or copy under setup/
    setup_stats = [c for c in router.calls if _is_find_yaml_stat(c, f"{plan_dir}/setup")]
    assert setup_stats == [], "should not have stat'd files under plan/setup"
    cp_calls = [c for c in router.calls if c[:2] == ["kubectl", "cp"]]
    assert len(cp_calls) == 1


def test_secret_redacted_after_copy(tmp_path, monkeypatch):
    """End-to-end: cp lands a Secret YAML; local file is redacted in-place."""
    run_name = "demo-run"
    phase = "baseline"
    plans_root = f"/data/{run_name}/plans/{phase}"
    workload = "balanced_20"
    wl_path = f"{plans_root}/{workload}"
    root = "root-20260101-120000-000"
    plan_dir = f"{wl_path}/{root}/plan"
    flow = "flow-control-baseline"
    flow_dir = f"{plan_dir}/{flow}"

    run_dir = tmp_path / "workspace" / "runs" / run_name
    run_dir.mkdir(parents=True)

    secret_yaml = (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        "  name: hf-token\n"
        "  namespace: jchen4\n"
        "type: Opaque\n"
        "data:\n"
        "  token: aGZfYWJjMTIzZGVmNDU2\n"
    )

    from pathlib import Path as _P

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["kubectl", "cp"]:
            dest = _P(cmd[3])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(secret_yaml)
            return _cp_result()
        if _is_ls(cmd, plans_root):
            return _cp_result(stdout=f"{workload}\n")
        if _is_ls(cmd, wl_path):
            return _cp_result(stdout=f"{root}\n")
        if _is_find_dirs(cmd, plan_dir):
            return _cp_result(stdout=f"{plan_dir}/{flow}\n")
        if _is_find_yaml_stat(cmd, flow_dir):
            return _cp_result(stdout=f"100 {flow_dir}/secret.yaml\n")
        return _cp_result()

    monkeypatch.setattr(deploy, "run", fake_run)
    deploy._extract_phase_plans("pod-x", run_name, phase, "ns-0", run_dir)

    local = run_dir / "results" / phase / "plans" / flow / "secret.yaml"
    assert local.exists()
    text = local.read_text()
    assert text.startswith("# REDACTED by sim2real collect: 1 Secret stubbed\n")
    assert "REDACTED" in text
    assert "aGZfYWJjMTIzZGVmNDU2" not in text
