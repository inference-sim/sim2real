"""Tests for deploy.py _cmd_collect phase selection logic."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.lib.progress import ConfigMapProgressStore


def _mock_cm(monkeypatch, data):
    """Monkeypatch ConfigMapProgressStore to return *data* on load and no-op on save.

    Also bypasses deploy._make_progress_store's run_metadata.json read (#551) so
    tests don't need to author a metadata file — the command-under-test only
    cares about the store's load()/save() behavior.
    """
    monkeypatch.setattr(ConfigMapProgressStore, "load",
                        lambda self: json.loads(json.dumps(data)))
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)
    from pipeline import deploy as _deploy_mod
    monkeypatch.setattr(
        _deploy_mod,
        "_make_progress_store",
        lambda ns, run_dir: ConfigMapProgressStore(
            ns, run_name=run_dir.name, scenario="test-scenario"
        ),
    )


def test_collect_with_progress_default(tmp_path, monkeypatch):
    """Without --package, collect uses all unique packages from progress."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "wl-load", "package": "baseline", "status": "pending"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["baseline", "treatment"]


def test_collect_fallback_no_progress(tmp_path, monkeypatch):
    """Without --package and empty progress, discovers phases from cluster/ with warning."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    _mock_cm(monkeypatch, {})

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["baseline", "treatment"]
    mock_warn.assert_called()


def test_collect_single_package_from_progress(tmp_path, monkeypatch):
    """With --package treatment and progress containing it, collects only treatment."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["treatment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["treatment"]


def test_collect_experiment_expands_to_all_progress_phases(tmp_path, monkeypatch):
    """With --package experiment, expands to all known phases from progress."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-canary": {"workload": "wl-smoke", "package": "canary", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["experiment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    # Should expand to all phases from progress (sorted)
    assert collected_phases == ["baseline", "canary", "treatment"]


# ── --package glob tests (issue #518) ───────────────────────────────────────


def test_collect_package_glob_matches_family(tmp_path, monkeypatch):
    """--package 'base*' expands via fnmatch to matching real packages
    (unscoped path — no --only/--workload)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":     {"workload": "wl-smoke", "package": "baseline",     "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-baseline-alt": {"workload": "wl-smoke", "package": "baseline-alt", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":    {"workload": "wl-smoke", "package": "treatment",    "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["base*"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert sorted(collected_phases) == ["baseline", "baseline-alt"]


def test_collect_package_glob_does_not_match_experiment(tmp_path, monkeypatch):
    """A pattern like '*' matches every real package but must NOT match the
    synthetic 'experiment' magic token (which is literal-only)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":  {"workload": "wl-smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["*"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    # Real packages only; the magic 'experiment' token does not appear.
    assert sorted(collected_phases) == ["baseline", "treatment"]
    assert "experiment" not in collected_phases


def test_collect_package_glob_pattern_matching_only_experiment_fatals(tmp_path, monkeypatch):
    """A pattern like 'exp*' does not match the 'experiment' magic literal — even
    though 'experiment' is in the valid set for --package on collect, patterns
    are excluded from matching it. With no real packages matching the pattern,
    collect exits fatally."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":  {"workload": "wl-smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["exp*"]
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})


def test_collect_package_experiment_literal_still_expands(tmp_path, monkeypatch):
    """Backwards compat: literal --package experiment continues to expand to
    every known phase after glob support is added."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":  {"workload": "wl-smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-canary":    {"workload": "wl-smoke", "package": "canary",    "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["experiment"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert sorted(collected_phases) == ["baseline", "canary", "treatment"]


def test_collect_package_glob_scoped_path(tmp_path, monkeypatch):
    """Glob expansion also applies in the scoped path (with --workload)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":     {"workload": "smoke", "package": "baseline",     "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-baseline-alt": {"workload": "smoke", "package": "baseline-alt", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":    {"workload": "smoke", "package": "treatment",    "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":      {"workload": "load",  "package": "baseline",     "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = ["base*"]
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases), "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline", "baseline-alt"]
    # smoke's pairs match; load-baseline is excluded because workload filter drops it
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}, "baseline-alt": {"smoke"}}


def test_collect_package_literal_and_glob_mix(tmp_path, monkeypatch):
    """Literal + glob in the same --package value list unions correctly."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":  {"workload": "wl-smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-canary":    {"workload": "wl-smoke", "package": "canary",    "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["canary", "treat*"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert sorted(collected_phases) == ["canary", "treatment"]


def test_collect_unknown_package_exits(tmp_path, monkeypatch):
    """With --package foo (not in progress), collect exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "wl-smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["foo"]
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})


def test_collect_custom_package_in_progress(tmp_path, monkeypatch):
    """With --package canary where canary IS in progress, succeeds."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-canary": {"workload": "wl-smoke", "package": "canary", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = ["canary"]
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["canary"]


def test_collect_corrupt_configmap_exits(tmp_path, monkeypatch):
    """Corrupt ConfigMap causes _cmd_collect to exit non-zero (issue #140)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    def _raise_on_load(self):
        raise ValueError("Corrupt ConfigMap sim2real-progress in ns-0")

    monkeypatch.setattr(ConfigMapProgressStore, "load", _raise_on_load)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         pytest.raises(SystemExit) as exc_info:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert exc_info.value.code != 0
    # Phase extraction must not have run — corrupt progress halts before that.
    assert collected_phases == []


def test_collect_unreachable_configmap_exits(tmp_path, monkeypatch, capsys):
    """Cluster-unreachable causes _cmd_collect to exit non-zero rather than
    falling through to filesystem-discovered phases (issue #287)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    # Drop a file that _discover_phases would otherwise consume — we want to
    # confirm the unreachable path refuses this fallback.
    (cluster_dir / "pipelinerun-baseline-wl.yaml").write_text(
        "kind: PipelineRun\n")

    def _raise_unreachable(self):
        raise RuntimeError("kubectl: connection refused")

    monkeypatch.setattr(ConfigMapProgressStore, "load", _raise_unreachable)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    class Args:
        package = None
        skip_logs = False
        only = None
        workload = None

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         pytest.raises(SystemExit) as exc_info:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert exc_info.value.code != 0
    assert collected_phases == []
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "unreachable" in combined.lower()


def test_collect_only_done_phases(tmp_path, monkeypatch):
    """Only phases with status done are included."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "pending"},
        "wl-a-canary": {"workload": "wl-a", "package": "canary", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    # treatment is pending — excluded; baseline and canary are done — included
    assert sorted(collected_phases) == ["baseline", "canary"]


def test_collect_missing_package_key_skipped(tmp_path, monkeypatch):
    """Progress entries without a 'package' key are gracefully skipped."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-b-broken": {"workload": "wl-b", "status": "done"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["baseline"]


def test_collect_with_multi_baseline_progress(tmp_path, monkeypatch):
    """Collect discovers arbitrary phase names from progress."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-b1": {"workload": "wl-smoke", "package": "b1", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-b2": {"workload": "wl-smoke", "package": "b2", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-ac1": {"workload": "wl-smoke", "package": "ac1", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["ac1", "b1", "b2"]


def test_collect_fallback_discovers_from_pipelinerun_files(tmp_path, monkeypatch):
    """Without progress data, falls back to discovering phases from pipelinerun YAMLs."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    (cluster_dir / "pipelinerun-wl-smoke-b1.yaml").write_text("apiVersion: tekton.dev/v1")
    (cluster_dir / "pipelinerun-wl-smoke-b2.yaml").write_text("apiVersion: tekton.dev/v1")
    _mock_cm(monkeypatch, {})

    class Args:
        package = None
        skip_logs = False

    collected_phases = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        collected_phases.extend(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert collected_phases == ["b1", "b2"]


def test_collect_with_workload_scope(tmp_path, monkeypatch):
    """--workload scopes extraction to matching workloads only."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-treatment": {"workload": "load", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases), "allowed_workloads": allowed_workloads, "namespace": namespace})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["namespace"] == "ns-0"
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}, "treatment": {"smoke"}}


def test_collect_with_only_scope(tmp_path, monkeypatch):
    """--only scopes extraction to one pair's workload and package."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = "wl-smoke-baseline"
        workload = None
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases), "allowed_workloads": allowed_workloads, "namespace": namespace})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["namespace"] == "ns-0"
    assert extract_calls[0]["phases"] == ["baseline"]
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}}


def test_collect_only_without_prefix(tmp_path, monkeypatch):
    """--only resolves wl- prefix automatically."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = "smoke-baseline"
        workload = None
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases), "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}}


def test_collect_workload_with_package_filter(tmp_path, monkeypatch):
    """--workload + --package compose: workload scopes within specified phases."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = ["baseline"]
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases), "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline"]
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}}


def test_collect_workload_no_match_exits(tmp_path, monkeypatch):
    """--workload with no matching pairs exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "nonexistent"
        package = None
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})


def test_collect_warns_nondone_scoped_pairs(tmp_path, monkeypatch):
    """When scoped pairs include non-done entries, warn but continue with done ones."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "running"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline"]
    assert any("running" in str(c) for c in mock_warn.call_args_list)


def test_collect_unscoped_unchanged(tmp_path, monkeypatch):
    """Without --only/--workload, collect behaves exactly as before (no workload param)."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = None
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["workload"] is None
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]


def test_collect_scoped_without_progress_exits(tmp_path, monkeypatch):
    """--workload without progress data exits with error."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    _mock_cm(monkeypatch, {})

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})


def test_collect_scoped_all_nondone_no_extraction(tmp_path, monkeypatch):
    """When all scoped pairs are non-done, no extraction is attempted and summary prints 0/0."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "running"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "pending"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 0
    assert any("running" in str(c) or "pending" in str(c) for c in mock_warn.call_args_list)


def test_collect_scoped_runtime_error(tmp_path, monkeypatch):
    """RuntimeError from extractor pod is caught and reported."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        raise RuntimeError("pod failed")

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert any("pod failed" in str(c) for c in mock_warn.call_args_list)


def test_collect_scoped_per_phase_failure(tmp_path, monkeypatch):
    """Per-phase extraction failure in scoped path populates failed list."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if on_workload_done:
            on_workload_done("baseline", "smoke", namespace, None)
            on_workload_done("treatment", "smoke", namespace, RuntimeError("tar failed"))
        return {
            "baseline": None,
            "treatment": RuntimeError("tar failed"),
        }

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert any("tar failed" in str(c) for c in mock_warn.call_args_list)


def test_collect_only_takes_precedence_over_workload(tmp_path, monkeypatch):
    """When both --only and --workload are given, --only takes precedence."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = "wl-smoke-baseline"
        workload = "load"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases), "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline"]
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}}


def test_collect_unscoped_multi_namespace_dispatch(tmp_path, monkeypatch):
    """Unscoped collect dispatches one extract call per distinct completed_namespace."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"namespace": namespace, "phases": sorted(phases), "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 2
    by_ns = {c["namespace"]: c for c in extract_calls}
    assert set(by_ns.keys()) == {"ns-0", "ns-1"}
    assert by_ns["ns-0"]["phases"] == ["baseline", "treatment"]
    assert by_ns["ns-1"]["phases"] == ["baseline", "treatment"]
    # Unscoped path passes no workload restriction
    assert all(c["workload"] is None for c in extract_calls)


def test_collect_unscoped_missing_completed_namespace_warns_and_skips(tmp_path, monkeypatch):
    """Done entries without completed_namespace emit a warning and are skipped."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    # No extraction — all entries missing completed_namespace
    assert extract_calls == []
    warnings = [str(c) for c in mock_warn.call_args_list]
    assert any("completed_namespace" in w for w in warnings)


def test_collect_scoped_multi_namespace_dispatch(tmp_path, monkeypatch):
    """Scoped collect uses each workload's completed_namespace, not primary."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":  {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":   {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":  {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"namespace": namespace, "phases": sorted(phases), "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-primary"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["namespace"] == "ns-0"
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"smoke"}, "treatment": {"smoke"}}


def test_collect_scoped_missing_completed_namespace_warns_and_skips(tmp_path, monkeypatch):
    """Scoped collect warns and skips workloads whose done pairs lack completed_namespace."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":  {"workload": "smoke", "package": "baseline",  "status": "done"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append(phases)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert extract_calls == []
    warnings = [str(c) for c in mock_warn.call_args_list]
    assert any("completed_namespace" in w for w in warnings)


def test_collect_reads_from_configmap(tmp_path, monkeypatch):
    """collect reads progress from ConfigMapProgressStore."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    cm_data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, cm_data)

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": phases, "workload": workload})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert sorted(extract_calls[0]["phases"]) == ["baseline", "treatment"]



# ── Incremental collect helpers ──────────────────────────────────────────────

def test_probe_remote_mtimes_parses_output():
    """_probe_remote_mtimes parses stat output into ``{workload: {iN: mtime}}``.

    Traces live under ``<phase>/<workload>/i<N>/trace_data.csv``. Iterations
    are kept as their own keys (issue #564 — collapsing to a per-workload max
    hid cross-slot iterations from the up-to-date gate).
    """
    from pipeline.deploy import _probe_remote_mtimes

    stat_output = (
        "1715800000 /data/run1/baseline/wl-smoke/i1/trace_data.csv\n"
        "1715800100 /data/run1/baseline/wl-load/i1/trace_data.csv\n"
    )

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=stat_output, stderr="")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {
        "wl-smoke": {"i1": 1715800000.0},
        "wl-load": {"i1": 1715800100.0},
    }


def test_probe_remote_mtimes_returns_empty_on_failure():
    """_probe_remote_mtimes returns {} and warns when kubectl exec fails."""
    from pipeline import deploy
    from pipeline.deploy import _probe_remote_mtimes

    with patch("subprocess.run") as mock_run, \
         patch.object(deploy, "warn") as mock_warn:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="exec failed")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {}
    assert any("mtime probe failed" in str(c) for c in mock_warn.call_args_list)


def test_probe_remote_mtimes_logs_info_on_empty_stdout():
    """_probe_remote_mtimes logs info when find succeeds but finds nothing."""
    from pipeline import deploy
    from pipeline.deploy import _probe_remote_mtimes

    with patch("subprocess.run") as mock_run, \
         patch.object(deploy, "info") as mock_info:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {}
    assert any("no trace_data.csv" in str(c) for c in mock_info.call_args_list)


def test_probe_remote_mtimes_warns_on_stderr():
    """_probe_remote_mtimes warns when stderr has content but command succeeds."""
    from pipeline import deploy
    from pipeline.deploy import _probe_remote_mtimes

    stat_output = "1715800000 /data/run1/baseline/wl-smoke/i1/trace_data.csv\n"

    with patch("subprocess.run") as mock_run, \
         patch.object(deploy, "warn") as mock_warn:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=stat_output,
            stderr="stat: /data/run1/baseline/wl-broken/i1/trace_data.csv: No such file")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {"wl-smoke": {"i1": 1715800000.0}}
    assert any("mtime probe had errors" in str(c) for c in mock_warn.call_args_list)


def test_probe_remote_mtimes_warns_on_unparseable_line():
    """_probe_remote_mtimes warns when float() fails on the mtime token."""
    from pipeline import deploy
    from pipeline.deploy import _probe_remote_mtimes

    stat_output = "garbage /data/run1/baseline/wl-bad/i1/trace_data.csv\n1715800000 /data/run1/baseline/wl-smoke/i1/trace_data.csv\n"

    with patch("subprocess.run") as mock_run, \
         patch.object(deploy, "warn") as mock_warn:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=stat_output, stderr="")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {"wl-smoke": {"i1": 1715800000.0}}
    assert any("unparseable" in str(c) for c in mock_warn.call_args_list)


def test_probe_remote_mtimes_warns_on_single_token_line():
    """_probe_remote_mtimes warns on lines with fewer than 2 tokens."""
    from pipeline import deploy
    from pipeline.deploy import _probe_remote_mtimes

    stat_output = "onlyone\n1715800000 /data/run1/baseline/wl-smoke/i1/trace_data.csv\n"

    with patch("subprocess.run") as mock_run, \
         patch.object(deploy, "warn") as mock_warn:
        mock_run.return_value = MagicMock(
            returncode=0, stdout=stat_output, stderr="")
        result = _probe_remote_mtimes("pod", "/data/run1/baseline", "ns-0")

    assert result == {"wl-smoke": {"i1": 1715800000.0}}
    assert any("unparseable" in str(c) for c in mock_warn.call_args_list)


def test_is_up_to_date_true_when_local_newer(tmp_path):
    """_is_up_to_date returns True when local file is at least as new as remote.

    This is the plans-YAML use case: a single-file freshness check kept intact
    for ``_extract_phase_plans``. The iteration-scoped variant is
    ``_is_iteration_up_to_date`` (covered in test_collect_internals.py).
    """
    from pipeline.deploy import _is_up_to_date

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")
    local_mtime = local_csv.stat().st_mtime

    assert _is_up_to_date(local_csv, local_mtime - 100) is True
    assert _is_up_to_date(local_csv, local_mtime) is True


def test_is_up_to_date_false_when_no_local(tmp_path):
    """_is_up_to_date returns False when local file does not exist."""
    from pipeline.deploy import _is_up_to_date

    assert _is_up_to_date(tmp_path / "trace_data.csv", 1715800000.0) is False


def test_is_up_to_date_false_when_remote_newer(tmp_path):
    """_is_up_to_date returns False when remote mtime is newer than local."""
    from pipeline.deploy import _is_up_to_date
    import os

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")
    old_time = 1000000000.0
    os.utime(local_csv, (old_time, old_time))

    assert _is_up_to_date(local_csv, old_time + 100) is False


def test_is_up_to_date_false_when_remote_mtime_none(tmp_path):
    """_is_up_to_date returns False when remote_mtime is None."""
    from pipeline.deploy import _is_up_to_date

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")

    assert _is_up_to_date(local_csv, None) is False


def test_is_up_to_date_false_on_os_error(tmp_path):
    """_is_up_to_date returns False and warns when stat raises OSError."""
    from pipeline import deploy
    from pipeline.deploy import _is_up_to_date

    local_csv = tmp_path / "trace_data.csv"
    local_csv.write_text("data")

    with patch.object(Path, "stat", side_effect=OSError("permission denied")), \
         patch.object(deploy, "warn") as mock_warn:
        assert _is_up_to_date(local_csv, 1715800000.0) is False

    assert any("stat failed" in str(c) for c in mock_warn.call_args_list)


def test_collect_unscoped_reports_per_pair_with_namespace(tmp_path, monkeypatch, capsys):
    """Unscoped collect reports each phase/workload with namespace context."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    captured = capsys.readouterr()
    output = captured.out + captured.err
    # Per-pair lines include phase/workload and namespace
    assert "baseline/smoke" in output
    assert "treatment/smoke" in output
    assert "baseline/load" in output
    assert "(ns-0)" in output
    assert "(ns-1)" in output
    # Summary shows pair count and root path
    assert "3/3 pairs" in captured.out
    assert str(run_dir / "results") in captured.out
    # Old format should NOT appear
    assert "Collected: baseline" not in captured.out
    assert "2/2 phases" not in captured.out


def test_collect_unscoped_failure_reports_pair_count(tmp_path, monkeypatch, capsys):
    """When extraction fails for a phase, summary shows correct pair counts."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if on_workload_done:
            on_workload_done("baseline", "smoke", namespace, None)
            on_workload_done("treatment", "smoke", namespace, RuntimeError("disk full"))
        return {"baseline": None, "treatment": RuntimeError("disk full")}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    captured = capsys.readouterr()
    # 1 pair collected (baseline/smoke), 1 failed (treatment/smoke)
    assert "1/2 pairs" in captured.out
    assert "Failed:    1 pairs" in captured.out


def test_collect_scoped_reports_namespace_context(tmp_path, monkeypatch, capsys):
    """Scoped collect reports phase/workload with namespace."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-slot-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-slot-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "smoke"
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-primary"]})

    captured = capsys.readouterr()
    output = captured.out
    assert "baseline/smoke" in output
    assert "treatment/smoke" in output
    assert "(ns-slot-0)" in output
    assert "2/2 pairs" in output


def test_collect_unscoped_no_cross_product_on_slot_reuse(tmp_path, monkeypatch, capsys):
    """When a namespace slot is reused for different workloads, only actual pairs are reported."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    # ns-0 ran baseline/smoke and treatment/smoke, then was reused for baseline/load
    # treatment/load does NOT exist in ns-0
    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment": {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline": {"workload": "load", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    captured = capsys.readouterr()
    output = captured.out + captured.err
    # Should report exactly 3 pairs, not 4 (no phantom treatment/load)
    assert "3/3 pairs" in captured.out
    assert "treatment/load" not in output
    assert "baseline/smoke" in output
    assert "baseline/load" in output
    assert "treatment/smoke" in output


# ── Parallel extraction tests ────────────────────────────────────────────────


def test_collect_unscoped_parallel_multi_ns(tmp_path, monkeypatch):
    """With multiple namespace slots, extraction runs via ThreadPoolExecutor."""
    from pipeline import deploy
    import concurrent.futures

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
        "wl-heavy-baseline":   {"workload": "heavy", "package": "baseline",  "status": "done", "completed_namespace": "ns-2"},
        "wl-heavy-treatment":  {"workload": "heavy", "package": "treatment", "status": "done", "completed_namespace": "ns-2"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"namespace": namespace, "phases": sorted(phases)})
        return {p: None for p in phases}

    executor_used = []
    OrigExecutor = concurrent.futures.ThreadPoolExecutor

    class TrackingExecutor(OrigExecutor):
        def __init__(self, *a, **kw):
            executor_used.append(True)
            super().__init__(*a, **kw)

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("concurrent.futures.ThreadPoolExecutor", TrackingExecutor):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 3
    ns_set = {c["namespace"] for c in extract_calls}
    assert ns_set == {"ns-0", "ns-1", "ns-2"}
    assert len(executor_used) == 1


def test_collect_unscoped_single_ns_no_threading(tmp_path, monkeypatch):
    """With a single namespace slot, extraction runs sequentially (no ThreadPoolExecutor)."""
    from pipeline import deploy
    import concurrent.futures

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        return {p: None for p in phases}

    executor_used = []
    OrigExecutor = concurrent.futures.ThreadPoolExecutor

    class TrackingExecutor(OrigExecutor):
        def __init__(self, *a, **kw):
            executor_used.append(True)
            super().__init__(*a, **kw)

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("concurrent.futures.ThreadPoolExecutor", TrackingExecutor):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(executor_used) == 0


def test_collect_unscoped_parallel_one_slot_fails(tmp_path, monkeypatch, capsys):
    """When one slot fails in parallel mode, other slots still succeed."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if namespace == "ns-1":
            raise RuntimeError("pod not ready")
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "baseline/smoke" in out
    assert "treatment/smoke" in out
    assert "2/" in captured.out


def test_collect_unscoped_parallel_step_header(tmp_path, monkeypatch, capsys):
    """Step header announces slot count when multiple slots exist."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    out = capsys.readouterr().out
    assert "2 slots in parallel" in out


def test_collect_unscoped_parallel_non_runtime_error(tmp_path, monkeypatch, capsys):
    """Non-RuntimeError from one slot doesn't crash the loop — other slots still succeed."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        if namespace == "ns-1":
            raise OSError("kubectl binary not found")
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "baseline/smoke" in out
    assert "treatment/smoke" in out
    assert "2/" in captured.out


def test_collect_unscoped_parallel_all_slots_fail(tmp_path, monkeypatch, capsys):
    """All slots failing produces correct summary with zero collected."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        raise RuntimeError(f"pod failed in {namespace}")

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    out = capsys.readouterr().out
    assert "0/" in out
    assert "Failed:" in out


# ── Stale file clearing tests ───────────────────────────────────────────────


def test_collect_full_copy_clears_stale_files(tmp_path, monkeypatch):
    """Full-copy path removes stale files inside each ``i<N>/`` before kubectl cp.

    Post-#564 the workload dir is no longer wiped as a whole — only the
    specific ``i<N>/`` we're about to re-copy. This test places a stale log
    inside ``i1/`` and confirms it's gone after collect.
    """
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    iter_dir = run_dir / "results" / "baseline" / "wl-smoke" / "i1"
    iter_dir.mkdir(parents=True)

    stale_file = iter_dir / "server_logs" / "stale.log"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("stale")

    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        # Second-level ls: enumerate i<N>/ under the workload
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            mock.stdout = "wl-smoke"
        if "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir, skip_logs=False)

    assert not stale_file.exists()


def test_collect_per_workload_clears_stale_files(tmp_path, monkeypatch):
    """Per-workload (scoped) path removes stale files inside each ``i<N>/``
    before kubectl cp — same per-iteration semantics as the unscoped path."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    iter_dir = run_dir / "results" / "baseline" / "wl-smoke" / "i1"
    iter_dir.mkdir(parents=True)

    stale_file = iter_dir / "server_logs" / "stale.log"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("stale")

    data = {
        "wl-smoke-baseline": {"workload": "wl-smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        # Second-level ls: enumerate i<N>/ under the workload
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
            mock.stdout = "i1"
        if "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir,
        skip_logs=False, workload="wl-smoke")

    assert not stale_file.exists()


def test_collect_skip_logs_clears_stale_log_dirs(tmp_path, monkeypatch):
    """--skip-logs path removes stale server_logs/, epp_logs/, and gpu_logs/
    inside every existing local iteration subtree before copying.

    Post step-5, log subdirs live under ``<wl>/i<N>/{server_logs,epp_logs,gpu_logs}``.
    The wipe walks each ``i*/`` under the workload dir.
    """
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    results_dir = run_dir / "results" / "baseline" / "wl-smoke"
    iter_dir = results_dir / "i1"
    iter_dir.mkdir(parents=True)

    stale_server = iter_dir / "server_logs" / "stale.log"
    stale_server.parent.mkdir(parents=True)
    stale_server.write_text("stale server log")

    stale_epp = iter_dir / "epp_logs" / "stale.log"
    stale_epp.parent.mkdir(parents=True)
    stale_epp.write_text("stale epp log")

    stale_gpu = iter_dir / "gpu_logs" / "stale.log"
    stale_gpu.parent.mkdir(parents=True)
    stale_gpu.write_text("stale gpu log")

    stale_metrics = iter_dir / "metrics" / "stale.csv"
    stale_metrics.parent.mkdir(parents=True)
    stale_metrics.write_text("stale metrics csv")

    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
            # Second-level ls: list iteration subdirs under the workload
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            # First-level ls: list workload subdirs under the phase
            mock.stdout = "wl-smoke"
        if "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir, skip_logs=True)

    assert not stale_server.exists()
    assert not (iter_dir / "server_logs").exists()
    assert not stale_epp.exists()
    assert not stale_gpu.exists()
    assert not stale_metrics.exists()


def test_collect_skip_logs_invokes_gpu_logs_copy(tmp_path, monkeypatch):
    """--skip-logs path issues a kubectl cp for gpu_logs/ alongside epp_logs/,
    scoped to each iteration subdirectory (post step-5 layout)."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    cp_targets = []

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_list = cmd if isinstance(cmd, list) else cmd.split()
        cmd_str = " ".join(cmd_list)
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            mock.stdout = "wl-smoke"
        if "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        if "cp" in cmd_list and len(cmd_list) >= 4:
            cp_targets.append(cmd_list[2])  # the source spec, e.g. ns/pod:/path/...
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir, skip_logs=True)

    sources = " ".join(cp_targets)
    assert "/wl-smoke/i1/gpu_logs/" in sources, f"no i1/gpu_logs/ copy issued; saw: {cp_targets}"
    assert "/wl-smoke/i1/epp_logs/" in sources, f"sanity: i1/epp_logs/ copy still issued; saw: {cp_targets}"
    assert "/wl-smoke/i1/gpu_stream_done" in sources, f"no i1/gpu_stream_done sentinel copy; saw: {cp_targets}"


def test_collect_skip_logs_invokes_metrics_copy(tmp_path, monkeypatch):
    """--skip-logs path issues a kubectl cp for metrics/ alongside epp_logs/ and gpu_logs/,
    scoped to each iteration subdirectory, and copies the metrics_stream_done sentinel.

    The recursive cp captures whatever collect_metrics.sh (the upstream scraper
    the stream-metrics sidecar now wraps — see sim2real#579) writes inside the
    per-cell metrics/ directory: metrics/raw/*_metrics.log Prometheus text
    dumps and metrics/processed/*.json aggregated summaries. The specific
    file names don't need mocking here since the assertion only verifies
    that the metrics/ dir itself is the cp source."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    cp_targets = []

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_list = cmd if isinstance(cmd, list) else cmd.split()
        cmd_str = " ".join(cmd_list)
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            mock.stdout = "wl-smoke"
        if "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        if "cp" in cmd_list and len(cmd_list) >= 4:
            cp_targets.append(cmd_list[2])
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir, skip_logs=True)

    sources = " ".join(cp_targets)
    assert "/wl-smoke/i1/metrics/" in sources, (
        f"no i1/metrics/ copy issued; saw: {cp_targets}")
    assert "/wl-smoke/i1/metrics_stream_done" in sources, (
        f"no i1/metrics_stream_done sentinel copy; saw: {cp_targets}")


def test_collect_idempotent_no_stale_accumulation(tmp_path, monkeypatch):
    """Running collect twice produces same result as once (no accumulation).

    Each ``kubectl cp`` overwrites the ``i<N>`` directory contents fresh, so
    log files from a prior collect do not pile up alongside new ones.
    """
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    data = {
        "wl-smoke-baseline": {"workload": "smoke", "package": "baseline", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    call_count = [0]

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        # Second-level ls: enumerate i<N>/ under the workload
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            mock.stdout = "wl-smoke"
        elif "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        elif "cp" in cmd_str:
            call_count[0] += 1
            dest = run_dir / "results" / "baseline" / "wl-smoke" / "i1"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "trace_data.csv").write_text(f"data-{call_count[0]}")
            (dest / "server_logs").mkdir(exist_ok=True)
            (dest / "server_logs" / f"pod-{call_count[0]}.log").write_text("log")
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir, skip_logs=False)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir, skip_logs=False)

    server_logs = run_dir / "results" / "baseline" / "wl-smoke" / "i1" / "server_logs"
    log_files = list(server_logs.glob("*.log"))
    assert len(log_files) == 1


# ── Per-slot workload filtering tests (issue #213) ───────────────────────────


def test_collect_parallel_filters_workloads_per_slot(tmp_path, monkeypatch):
    """In parallel mode (multiple namespace slots), each slot's extraction
    receives an allowed_workloads set containing ONLY the workloads that
    progress assigns to that slot."""
    from pipeline import deploy
    import concurrent.futures

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    # Two slots: ns-0 has smoke, ns-1 has load+heavy
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
        "wl-heavy-baseline":   {"workload": "heavy", "package": "baseline",  "status": "done", "completed_namespace": "ns-1"},
        "wl-heavy-treatment":  {"workload": "heavy", "package": "treatment", "status": "done", "completed_namespace": "ns-1"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({
            "namespace": namespace,
            "phases": sorted(phases),
            "allowed_workloads": allowed_workloads,
        })
        return {p: None for p in phases}

    OrigExecutor = concurrent.futures.ThreadPoolExecutor

    class TrackingExecutor(OrigExecutor):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("concurrent.futures.ThreadPoolExecutor", TrackingExecutor):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    # Should have 2 extract calls (one per slot)
    assert len(extract_calls) == 2

    # Build a map of namespace -> allowed_workloads for verification
    ns_to_allowed = {c["namespace"]: c["allowed_workloads"] for c in extract_calls}

    # ns-0 should allow smoke in both phases
    assert ns_to_allowed["ns-0"] == {"baseline": {"smoke"}, "treatment": {"smoke"}}, (
        f"Expected ns-0 per-phase allowed_workloads, got {ns_to_allowed['ns-0']}")

    # ns-1 should allow load+heavy in both phases
    assert ns_to_allowed["ns-1"] == {"baseline": {"load", "heavy"}, "treatment": {"load", "heavy"}}, (
        f"Expected ns-1 per-phase allowed_workloads, got {ns_to_allowed['ns-1']}")


def test_collect_sequential_filters_workloads_per_slot(tmp_path, monkeypatch):
    """In sequential mode (single slot), extraction still receives
    allowed_workloads containing the workloads assigned to that slot."""
    from pipeline import deploy
    import concurrent.futures

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    # Single slot: ns-0 has both workloads
    data = {
        "wl-smoke-baseline":   {"workload": "smoke", "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-smoke-treatment":  {"workload": "smoke", "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
        "wl-load-baseline":    {"workload": "load",  "package": "baseline",  "status": "done", "completed_namespace": "ns-0"},
        "wl-load-treatment":   {"workload": "load",  "package": "treatment", "status": "done", "completed_namespace": "ns-0"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({
            "namespace": namespace,
            "phases": sorted(phases),
            "allowed_workloads": allowed_workloads,
        })
        return {p: None for p in phases}

    executor_used = []
    OrigExecutor = concurrent.futures.ThreadPoolExecutor

    class TrackingExecutor(OrigExecutor):
        def __init__(self, *a, **kw):
            executor_used.append(True)
            super().__init__(*a, **kw)

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch("concurrent.futures.ThreadPoolExecutor", TrackingExecutor):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    # Sequential mode: no ThreadPoolExecutor used
    assert len(executor_used) == 0

    # Should have exactly 1 extract call
    assert len(extract_calls) == 1

    # The single slot should receive per-phase allowed_workloads
    call = extract_calls[0]
    assert call["namespace"] == "ns-0"
    assert call["allowed_workloads"] == {"baseline": {"smoke", "load"}, "treatment": {"smoke", "load"}}, (
        f"Expected per-phase allowed_workloads, got {call['allowed_workloads']}")


def test_extract_phases_filters_by_allowed_workloads(tmp_path, monkeypatch):
    """Inside _extract_phases_from_pvc, when allowed_workloads is set,
    only those workloads are copied even if ls discovers more on the PVC."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    copied_workloads = []

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        # Second-level ls: enumerate i<N>/ under the workload
        if "exec" in cmd_str and "ls " in cmd_str and any(
            f"/{w}/" in cmd_str for w in ("wl-smoke", "wl-load", "wl-heavy")
        ):
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            # First-level ls: workload subdirs under the phase
            mock.stdout = "wl-smoke\nwl-load\nwl-heavy"
        elif "exec" in cmd_str and "stat" in cmd_str:
            # No mtimes — force copy
            mock.stdout = ""
        elif "cp" in cmd_str:
            # Track which workloads get copied
            for wl in ("wl-smoke", "wl-load", "wl-heavy"):
                if wl in cmd_str:
                    copied_workloads.append(wl)
                    dest = run_dir / "results" / "baseline" / wl / "i1"
                    dest.mkdir(parents=True, exist_ok=True)
                    break
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Also mock helper functions that would trip up without a real pod
    monkeypatch.setattr(deploy, "_probe_phase_sizes",
                        lambda pod, rn, phases, ns: {p: 100 for p in phases})
    monkeypatch.setattr(deploy, "_probe_remote_mtimes",
                        lambda pod, path, ns: {})
    monkeypatch.setattr(deploy, "_is_up_to_date", lambda local, remote: False)

    # Call with allowed_workloads limiting baseline phase to only smoke and heavy
    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir,
        skip_logs=False, allowed_workloads={"baseline": {"wl-smoke", "wl-heavy"}})

    # Only wl-smoke and wl-heavy should have been copied, NOT wl-load
    assert "wl-smoke" in copied_workloads, "wl-smoke should have been copied"
    assert "wl-heavy" in copied_workloads, "wl-heavy should have been copied"
    assert "wl-load" not in copied_workloads, (
        "wl-load should NOT have been copied when allowed_workloads excludes it")


def test_extract_phases_filters_by_allowed_workloads_skip_logs(tmp_path, monkeypatch):
    """Same as above but with skip_logs=True — the --skip-logs discovery path
    also respects allowed_workloads filtering."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    copied_workloads = []

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "exec" in cmd_str and "ls " in cmd_str and any(
            f"/{w}/" in cmd_str for w in ("wl-smoke", "wl-load", "wl-heavy")
        ):
            # Second-level ls: list iteration subdirs under the workload
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            # First-level ls: workload subdirs under the phase
            mock.stdout = "wl-smoke\nwl-load\nwl-heavy"
        elif "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        elif "cp" in cmd_str:
            for wl in ("wl-smoke", "wl-load", "wl-heavy"):
                if wl in cmd_str:
                    copied_workloads.append(wl)
                    dest = run_dir / "results" / "baseline" / wl / "i1"
                    dest.mkdir(parents=True, exist_ok=True)
                    break
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    monkeypatch.setattr(deploy, "_probe_phase_sizes",
                        lambda pod, rn, phases, ns: {p: 100 for p in phases})
    monkeypatch.setattr(deploy, "_probe_remote_mtimes",
                        lambda pod, path, ns: {})
    monkeypatch.setattr(deploy, "_is_up_to_date", lambda local, remote: False)

    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-0", run_dir,
        skip_logs=True, allowed_workloads={"baseline": {"wl-smoke", "wl-heavy"}})

    assert "wl-smoke" in copied_workloads, "wl-smoke should have been copied"
    assert "wl-heavy" in copied_workloads, "wl-heavy should have been copied"
    assert "wl-load" not in copied_workloads, (
        "wl-load should NOT have been copied in skip-logs mode when excluded")


def test_extract_phases_per_phase_filter_prevents_cross_phase_leak(tmp_path, monkeypatch):
    """Regression test for #216: a workload assigned to treatment on this slot
    must NOT be collected under baseline even if it exists on the PVC there."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    copied_pairs = []

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        # Second-level ls: enumerate i<N>/ under the workload
        if "exec" in cmd_str and "ls " in cmd_str and any(
            f"/{w}/" in cmd_str for w in ("chatbot", "balanced")
        ):
            mock.stdout = "i1"
        elif "exec" in cmd_str and "ls" in cmd_str:
            # First-level ls: both phases have chatbot and balanced on PVC
            mock.stdout = "chatbot\nbalanced"
        elif "exec" in cmd_str and "stat" in cmd_str:
            mock.stdout = ""
        elif "cp" in cmd_str:
            for phase in ("baseline", "treatment"):
                for wl in ("chatbot", "balanced"):
                    if f"/{phase}/{wl}/" in cmd_str:
                        copied_pairs.append(f"{phase}/{wl}")
                        dest = run_dir / "results" / phase / wl / "i1"
                        dest.mkdir(parents=True, exist_ok=True)
                        break
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(deploy, "_probe_phase_sizes",
                        lambda pod, rn, phases, ns: {p: 100 for p in phases})
    monkeypatch.setattr(deploy, "_probe_remote_mtimes",
                        lambda pod, path, ns: {})
    monkeypatch.setattr(deploy, "_is_up_to_date", lambda local, remote: False)

    # This slot is assigned: baseline/chatbot + treatment/balanced
    # PVC has both workloads under both phases (stale)
    deploy._extract_phases_from_pvc(
        ["baseline", "treatment"], "test-run", "ns-0", run_dir,
        skip_logs=False,
        allowed_workloads={"baseline": {"chatbot"}, "treatment": {"balanced"}})

    assert "baseline/chatbot" in copied_pairs
    assert "treatment/balanced" in copied_pairs
    assert "baseline/balanced" not in copied_pairs, (
        "baseline/balanced is stale — should NOT be collected (cross-phase leak)")
    assert "treatment/chatbot" not in copied_pairs, (
        "treatment/chatbot is stale — should NOT be collected (cross-phase leak)")


# ── Issue #242: scoped collect must dispatch per-slot ─────────────────────────


def test_collect_scoped_multi_slot_per_workload(tmp_path, monkeypatch):
    """--workload dispatches per-slot when a workload's packages span multiple slots."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-mid-constantcontrol": {"workload": "mid", "package": "constantcontrol", "status": "done", "completed_namespace": "ns-0"},
        "wl-mid-expceil":         {"workload": "mid", "package": "expceil",         "status": "done", "completed_namespace": "ns-1"},
        "wl-mid-expceiling":      {"workload": "mid", "package": "expceiling",      "status": "done", "completed_namespace": "ns-2"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "mid"
        package = None
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({
            "namespace": namespace,
            "phases": sorted(phases),
            "allowed_workloads": allowed_workloads,
        })
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-primary"]})

    assert len(extract_calls) == 3, f"Expected 3 slot dispatches, got {len(extract_calls)}"
    ns_set = {c["namespace"] for c in extract_calls}
    assert ns_set == {"ns-0", "ns-1", "ns-2"}

    ns_to_allowed = {c["namespace"]: c["allowed_workloads"] for c in extract_calls}
    assert ns_to_allowed["ns-0"] == {"constantcontrol": {"mid"}}
    assert ns_to_allowed["ns-1"] == {"expceil": {"mid"}}
    assert ns_to_allowed["ns-2"] == {"expceiling": {"mid"}}


# ── Issue #204: kubectl run failure surfaces as RuntimeError ─────────────────


def test_extract_phases_kubectl_run_failure_raises_runtime_error(tmp_path, monkeypatch):
    """When kubectl run fails creating the extractor pod, the function raises
    a clean RuntimeError — not a raw CalledProcessError that escapes callers
    catching only RuntimeError."""
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    def mock_run(cmd, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "run" in cmd and "--image=alpine:3.19" in cmd_str:
            return MagicMock(returncode=1, stdout="",
                             stderr="forbidden: namespace quota exceeded")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="create failed.*quota exceeded"):
        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir, skip_logs=False)


# ─── Issue 398: --package composes with --workload as a pair-scope filter ───
# Pair-scope filters (--only, --workload, --package, --status) compose as an
# intersection across every deploy.py subcommand.  Before the fix, collect
# silently dropped --package from pair scope and only used it for phase scope,
# so --workload X --package Y resolved to "every package of X" for pair-scope
# checks (with spurious warnings about pairs of other packages).

def test_collect_package_baseline_with_workload_no_spurious_warn(tmp_path, monkeypatch):
    """`--workload X --package baseline` must not warn about non-baseline pairs of X.

    Regression for issue #398. Pre-fix, the synthetic _ScopeArgs dropped
    --package, so a pending wl-X-treatment pair landed in in_scope and the
    "skipping" warn loop fired on it even though the user only asked about
    baseline.
    """
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-reason-baseline":  {"workload": "reason", "package": "baseline",
                                "status": "done", "completed_namespace": "ns-0"},
        "wl-reason-treatment": {"workload": "reason", "package": "treatment",
                                "status": "pending"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "reason"
        package = ["baseline"]
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False,
                     workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases),
                              "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline"]
    assert extract_calls[0]["allowed_workloads"] == {"baseline": {"reason"}}
    # The treatment pair is out of scope (user filtered to --package baseline),
    # so the "Scoped pair ... skipping" warn must not fire on it.
    warn_msgs = [str(c) for c in mock_warn.call_args_list]
    assert not any("wl-reason-treatment" in m for m in warn_msgs), (
        f"Spurious warn about out-of-scope pair: {warn_msgs}"
    )


def test_collect_package_experiment_with_workload_still_warns_nondone(tmp_path, monkeypatch):
    """`--package experiment` keeps today's behavior: warns on every non-done pair of X.

    The synthetic 'experiment' value doesn't name a pair package, so it
    cannot narrow pair scope — the fix's carve-out preserves that.
    """
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-reason-baseline":  {"workload": "reason", "package": "baseline",
                                "status": "done", "completed_namespace": "ns-0"},
        "wl-reason-treatment": {"workload": "reason", "package": "treatment",
                                "status": "pending"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "reason"
        package = ["experiment"]
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False,
                     workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases),
                              "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    # Only baseline is done, so that's all that gets extracted, but every
    # package of every scoped pair was in-scope — the warn about the pending
    # treatment pair must fire.
    assert extract_calls[0]["phases"] == ["baseline"]
    warn_msgs = [str(c) for c in mock_warn.call_args_list]
    assert any("wl-reason-treatment" in m and "pending" in m for m in warn_msgs), (
        f"Expected warn about pending wl-reason-treatment under --package experiment: {warn_msgs}"
    )


def test_collect_multi_package_with_workload(tmp_path, monkeypatch):
    """`--workload X --package baseline,softreflective` narrows to those two packages."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-reason-baseline": {"workload": "reason", "package": "baseline",
                               "status": "done", "completed_namespace": "ns-0"},
        "wl-reason-softreflective": {"workload": "reason", "package": "softreflective",
                                     "status": "done", "completed_namespace": "ns-0"},
        "wl-reason-treatment": {"workload": "reason", "package": "treatment",
                                "status": "pending"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = "reason"
        package = ["baseline", "softreflective"]
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False,
                     workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"phases": sorted(phases),
                              "allowed_workloads": allowed_workloads})
        if on_workload_done and allowed_workloads:
            for phase in phases:
                for wl in allowed_workloads.get(phase, set()):
                    on_workload_done(phase, wl, namespace, None)
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract), \
         patch.object(deploy, "warn") as mock_warn:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-0"]})

    assert len(extract_calls) == 1
    assert extract_calls[0]["phases"] == ["baseline", "softreflective"]
    assert extract_calls[0]["allowed_workloads"] == {
        "baseline": {"reason"},
        "softreflective": {"reason"},
    }
    # treatment was excluded from pair scope, so no warn about it.
    warn_msgs = [str(c) for c in mock_warn.call_args_list]
    assert not any("wl-reason-treatment" in m for m in warn_msgs), (
        f"Spurious warn about out-of-scope pair: {warn_msgs}"
    )


# ── main() dispatcher tests for `collect` subcommand (#447) ────────────────

def _run_deploy_main_collect(argv, monkeypatch, tmp_path):
    """Call deploy.main() with mocked argv and --experiment-root=tmp_path.

    Mirrors the helper in test_deploy_run.py — main() re-resolves
    EXPERIMENT_ROOT from --experiment-root (or cwd), so monkeypatching the
    module-level global is not enough.
    """
    import sys as _sys
    from pipeline import deploy
    monkeypatch.setattr(_sys, "argv",
                        ["deploy.py", "--experiment-root", str(tmp_path), *argv])
    monkeypatch.setattr(deploy, "_tty", False, raising=False)
    return deploy.main()


def _make_collect_run_dir(tmp_path, run_name="trial-1", *,
                          with_cluster=True, with_metadata=True,
                          metadata_content=None):
    """Fixture helper: build a workspace/runs/<run>/ tree for collect dispatcher tests."""
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / run_name
    run_dir.mkdir(parents=True)
    if with_cluster:
        (run_dir / "cluster").mkdir()
    if with_metadata:
        content = metadata_content if metadata_content is not None else \
            {"version": 1, "run_name": run_name, "cluster_id": "ocp-east"}
        (run_dir / "run_metadata.json").write_text(json.dumps(content))
    return workspace, run_dir


def test_main_collect_missing_run_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with no run dir → assemble hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_main_collect_missing_cluster_dir_emits_assemble_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with runs/trial-1/ but no cluster/ → assemble hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_collect_run_dir(tmp_path, with_cluster=False)
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run 'sim2real assemble --run trial-1' first" in capsys.readouterr().err


def test_main_collect_missing_run_metadata_emits_corrupt_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with no run_metadata.json → 're-assemble' hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_collect_run_dir(tmp_path, with_metadata=False)
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_main_collect_missing_cluster_id_emits_corrupt_hint(tmp_path, capsys, monkeypatch):
    """`deploy.py --run trial-1 collect` with metadata missing cluster_id → 're-assemble' hint."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "setup_config.json").write_text("{}")
    _make_collect_run_dir(tmp_path,
                          metadata_content={"version": 1, "run_name": "trial-1"})
    with pytest.raises(SystemExit):
        _run_deploy_main_collect(["--run", "trial-1", "collect"], monkeypatch, tmp_path)
    assert "run metadata corrupted; re-assemble" in capsys.readouterr().err


def test_cmd_collect_empty_namespaces_exits(tmp_path, capsys, monkeypatch):
    """_cmd_collect with cluster_config missing 'namespaces' → 'No namespace configured.' exit."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "trial-1"
    (run_dir / "cluster").mkdir(parents=True)

    # NAMESPACE env var must be unset for the guard to fire.
    monkeypatch.delenv("NAMESPACE", raising=False)

    class Args:
        package = None
        skip_logs = False

    with pytest.raises(SystemExit):
        deploy._cmd_collect(Args(), run_dir, {})
    assert "No namespace configured." in capsys.readouterr().err


# ── --iteration filter (issue #512) ─────────────────────────────────────────


def test_collect_iteration_filter_narrows_scope(tmp_path, monkeypatch):
    """--iteration 1-3 restricts collect to the matching iteration subset.

    Integration test named in the issue: pairs exist at i1..i5 for the same
    (workload, package); ``deploy.py collect --iteration 1-3`` must consider
    only i1..i3. We surface the effect by placing i4 and i5 in a separate
    completed_namespace and asserting no extract call is made against that
    namespace when the filter is applied.
    """
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-chat-mid|sim2real-ac|i1": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-a"},
        "wl-chat-mid|sim2real-ac|i2": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-a"},
        "wl-chat-mid|sim2real-ac|i3": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-a"},
        "wl-chat-mid|sim2real-ac|i4": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-b"},
        "wl-chat-mid|sim2real-ac|i5": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-b"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = None
        package = None
        iteration = "1-3"
        skip_logs = False

    extract_calls = []

    def mock_extract(phases, run_name, namespace, run_dir_arg, *, skip_logs=False, workload=None, allowed_workloads=None, on_workload_done=None):
        extract_calls.append({"namespace": namespace, "phases": sorted(phases)})
        return {p: None for p in phases}

    with patch.object(deploy, "_extract_phases_from_pvc", mock_extract):
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-a"]})

    dispatched_ns = {c["namespace"] for c in extract_calls}
    assert "ns-a" in dispatched_ns, "expected extract against ns-a (holds i1..i3)"
    assert "ns-b" not in dispatched_ns, (
        "ns-b holds only i4..i5 — must be excluded by --iteration 1-3")


def test_collect_iteration_filter_no_match_aborts(tmp_path, capsys, monkeypatch):
    """--iteration with no matching iteration aborts via _report_filter_mismatch."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-chat-mid|sim2real-ac|i1": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-a"},
        "wl-chat-mid|sim2real-ac|i2": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-a"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = None
        package = None
        iteration = "9"
        skip_logs = False

    with pytest.raises(SystemExit) as exc_info:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-a"]})
    assert exc_info.value.code == 1
    assert "--iteration '9'" in capsys.readouterr().err


def test_collect_iteration_malformed_exits(tmp_path, capsys, monkeypatch):
    """--iteration abc surfaces parse_iteration_spec's error before doing work."""
    from pipeline import deploy

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)
    data = {
        "wl-chat-mid|sim2real-ac|i1": {"workload": "chat-mid", "package": "sim2real-ac", "status": "done", "completed_namespace": "ns-a"},
    }
    _mock_cm(monkeypatch, data)

    class Args:
        only = None
        workload = None
        package = None
        iteration = "abc"
        skip_logs = False

    with pytest.raises(SystemExit) as exc_info:
        deploy._cmd_collect(Args(), run_dir, {"namespaces": ["ns-a"]})
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "iteration" in (captured.out + captured.err).lower()


# ── Issue #564: cross-slot iteration preservation ────────────────────────────


def test_extract_phases_preserves_iterations_across_slot_collects(tmp_path, monkeypatch):
    """End-to-end regression guard for #564.

    Simulates the reported failure: replicas=2, one ``(phase, workload)``
    pair. Slot A holds i1 on its PVC, slot B holds i2. Collecting slot A
    then slot B (as the parallel/sequential caller does, one invocation per
    slot) must land both iterations on local disk. Pre-fix, slot B would
    wipe the workload dir and lose slot A's i1.
    """
    from pipeline import deploy
    import subprocess

    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    (run_dir / "cluster").mkdir(parents=True)

    # Whichever slot is being emulated for the current mock_run call.
    active_slot = ["ns-A"]
    # What each slot's PVC holds under /data/test-run/baseline/wl-x/
    slot_iterations = {"ns-A": ["i1"], "ns-B": ["i2"]}

    def mock_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stdout="", stderr="")
        cmd_list = list(cmd) if isinstance(cmd, list) else cmd.split()
        cmd_str = " ".join(cmd_list)
        if "exec" in cmd_str and "find" in cmd_str:
            # mtime probe — return one line per iteration on this slot
            lines = [
                f"1000 /data/test-run/baseline/wl-x/{iN}/trace_data.csv"
                for iN in slot_iterations[active_slot[0]]
            ]
            mock.stdout = "\n".join(lines)
            return mock
        if "exec" in cmd_str and "ls " in cmd_str and "/wl-x/" in cmd_str:
            # Second-level ls: iterations this slot holds
            mock.stdout = "\n".join(slot_iterations[active_slot[0]])
            return mock
        if "exec" in cmd_str and "ls" in cmd_str:
            # First-level ls: single workload
            mock.stdout = "wl-x"
            return mock
        if "exec" in cmd_str and "stat" in cmd_str:
            # phase-size probe
            mock.stdout = "0"
            return mock
        if "cp" in cmd_list:
            # Fake the kubectl cp — write a trace file at the destination iN
            # so subsequent stat calls could see it if needed.
            dst = cmd_list[cmd_list.index("cp") + 2]
            Path(dst).mkdir(parents=True, exist_ok=True)
            (Path(dst) / "trace_data.csv").write_text(
                f"data from {active_slot[0]}"
            )
            return mock
        return mock

    monkeypatch.setattr(subprocess, "run", mock_run)

    # Collect slot A first
    active_slot[0] = "ns-A"
    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-A", run_dir, skip_logs=False,
        allowed_workloads={"baseline": {"wl-x"}})

    i1_local = run_dir / "results" / "baseline" / "wl-x" / "i1" / "trace_data.csv"
    assert i1_local.exists(), "slot A's i1 should be on disk after first collect"
    assert i1_local.read_text() == "data from ns-A"

    # Now collect slot B — this used to wipe slot A's i1
    active_slot[0] = "ns-B"
    deploy._extract_phases_from_pvc(
        ["baseline"], "test-run", "ns-B", run_dir, skip_logs=False,
        allowed_workloads={"baseline": {"wl-x"}})

    # Both must be present now
    assert i1_local.exists(), (
        "regression: slot A's i1 was wiped by slot B's collect (issue #564)"
    )
    assert i1_local.read_text() == "data from ns-A"
    i2_local = run_dir / "results" / "baseline" / "wl-x" / "i2" / "trace_data.csv"
    assert i2_local.exists(), "slot B's i2 should also land"
    assert i2_local.read_text() == "data from ns-B"
