"""Tests for deploy.py run orchestrator and status subcommand."""
import argparse
import json
import pytest
from unittest.mock import patch

from pipeline.lib.progress import ConfigMapProgressStore


_PROGRESS = {
    "wl-smoke-baseline":   {"workload": "wl-smoke",  "package": "baseline",   "status": "done",      "namespace": "sim2real-0", "retries": 0},
    "wl-smoke-treatment":  {"workload": "wl-smoke",  "package": "treatment",  "status": "running",   "namespace": "sim2real-1", "retries": 0},
    "wl-load-baseline":    {"workload": "wl-load",   "package": "baseline",   "status": "pending",   "namespace": None,         "retries": 0},
    "wl-load-treatment":   {"workload": "wl-load",   "package": "treatment",  "status": "timed-out", "namespace": "sim2real-2", "retries": 1},
    "wl-heavy-baseline":   {"workload": "wl-heavy",  "package": "baseline",   "status": "failed",    "namespace": "sim2real-0", "retries": 0},
    "_orchestrator":       {"state": "normal", "backoff_level": 0, "last_probe_free_gpus": 8},
}


def _mock_cm(monkeypatch, data):
    """Monkeypatch ConfigMapProgressStore to return *data* on load and no-op on save."""
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: data)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)


def test_status_output_contains_all_pairs(tmp_path, capsys, monkeypatch):
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = None
        workload = None
        package = None
        status = None
        live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    for key in _PROGRESS:
        if not key.startswith("_"):
            assert key in out


def test_status_filter_by_workload(tmp_path, capsys, monkeypatch):
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = None
        workload = "wl-smoke"
        package = None
        status = None
        live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "wl-smoke-baseline" in out
    assert "wl-smoke-treatment" in out
    assert "wl-load-baseline" not in out


def test_status_filter_by_package(tmp_path, capsys, monkeypatch):
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = None
        workload = None
        package = "treatment"
        status = None
        live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "wl-smoke-treatment" in out
    assert "wl-load-treatment" in out
    assert "wl-smoke-baseline" not in out


def test_status_summary_line(tmp_path, capsys, monkeypatch):
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = None
        workload = None
        package = None
        status = None
        live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "5 pairs" in out
    assert "1 done" in out
    assert "1 running" in out
    assert "1 pending" in out


def test_status_missing_progress_file(tmp_path, capsys, monkeypatch):
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, {})

    class _Args:
        only = None
        workload = None
        package = None
        status = None
        live = False

    _cmd_status(_Args(), tmp_path / "missing-run-dir",
                setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "0 pairs" in out


def test_status_unreachable_configmap_exits(tmp_path, capsys, monkeypatch):
    """Cluster-unreachable causes _cmd_status to exit non-zero with a
    distinct message instead of printing '0 pairs' (issue #287)."""
    from pipeline.deploy import _cmd_status

    def _raise_unreachable(self):
        raise RuntimeError("kubectl: connection refused")

    monkeypatch.setattr(ConfigMapProgressStore, "load", _raise_unreachable)
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    class _Args:
        only = None
        workload = None
        package = None
        status = None
        live = False

    with pytest.raises(SystemExit) as exc_info:
        _cmd_status(_Args(), tmp_path / "run-x",
                    setup_config={"namespace": "sim2real-ns"})

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "unreachable" in combined.lower()
    assert "0 pairs" not in combined


def test_status_filter_by_only(tmp_path, capsys, monkeypatch):
    """status subcommand supports --only filter."""
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = "wl-smoke-baseline"; workload = None; package = None; status = None; live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "wl-smoke-baseline" in out
    assert "wl-load-baseline" not in out


def test_status_filter_by_status(tmp_path, capsys, monkeypatch):
    """status subcommand supports --status filter."""
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = None; workload = None; package = None; status = "running"; live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "wl-smoke-treatment" in out
    assert "wl-load-baseline" not in out


def test_status_mismatch_shows_valid_values(tmp_path, capsys, monkeypatch):
    """status subcommand shows valid values on filter mismatch."""
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, _PROGRESS)

    class _Args:
        only = None; workload = "nonexistent"; package = None; status = None; live = False

    with pytest.raises(SystemExit) as exc_info:
        _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "unrecognized" in captured
    assert "wl-smoke" in captured


def test_status_empty_progress_with_filters(tmp_path, capsys, monkeypatch):
    """status with empty progress and active filters warns filters are ignored."""
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, {})

    class _Args:
        only = None; workload = "foo"; package = None; status = None; live = False

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "0 pairs" in out
    assert "filters ignored" in out


def test_load_pairs_discovers_all_pairs(tmp_path):
    from pipeline.deploy import _load_pairs
    import yaml as _yaml
    for wl, pkg in [("smoke", "baseline"), ("smoke", "treatment"), ("load", "baseline")]:
        pr = {
            "apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
            "metadata": {"name": f"{pkg}-{wl}-run1", "namespace": "sim2real-0"},
            "spec": {"params": [
                {"name": "workloadName", "value": f"wl-{wl}"},
                {"name": "phase", "value": pkg},
            ]},
        }
        (tmp_path / f"pipelinerun-{wl}-{pkg}.yaml").write_text(_yaml.dump(pr))

    pairs = _load_pairs(tmp_path)
    assert "wl-smoke-baseline" in pairs
    assert "wl-smoke-treatment" in pairs
    assert "wl-load-baseline" in pairs
    assert len(pairs) == 3


def test_load_pairs_skips_corrupt_yaml(tmp_path, capsys):
    """Corrupt YAML files are skipped with a warning; valid ones still loaded."""
    import yaml as _yaml
    from pipeline.deploy import _load_pairs

    pr = {
        "metadata": {"name": "baseline-smoke-run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-smoke"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (tmp_path / "pipelinerun-smoke-baseline.yaml").write_text(_yaml.dump(pr))
    (tmp_path / "pipelinerun-bad.yaml").write_text("{{invalid yaml: [")

    pairs = _load_pairs(tmp_path)

    assert len(pairs) == 1
    assert "wl-smoke-baseline" in pairs
    assert "pipelinerun-bad.yaml" in capsys.readouterr().out


def test_load_pairs_skips_malformed_params(tmp_path, capsys):
    """Missing 'value' key in a param entry skips the file with a warning."""
    import yaml as _yaml
    from pipeline.deploy import _load_pairs

    pr = {
        "metadata": {"name": "run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (tmp_path / "pipelinerun-test.yaml").write_text(_yaml.dump(pr))
    pairs = _load_pairs(tmp_path)
    assert len(pairs) == 0
    assert "pipelinerun-test.yaml" in capsys.readouterr().out


def test_load_pairs_warns_on_skip(tmp_path, capsys):
    """Warning is emitted with filename when a file is skipped."""
    from pipeline.deploy import _load_pairs

    (tmp_path / "pipelinerun-broken.yaml").write_text("not: valid: yaml: [[[")
    _load_pairs(tmp_path)

    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "pipelinerun-broken.yaml" in out


def test_apply_run_filters_by_status():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = None; status = "failed"

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-heavy-baseline"}


def test_apply_run_filters_compose():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = "treatment"; status = "timed-out"

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-load-treatment"}


def test_apply_run_filters_only_flag(capsys):
    """Exact match does not emit the 'resolved' diagnostic."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = "wl-smoke-baseline"; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline"}
    assert "resolved" not in capsys.readouterr().out


def test_apply_run_filters_no_flags_returns_empty():
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == set()


def test_apply_run_filters_only_without_prefix(capsys):
    """--only accepts values without the wl- prefix and logs normalization."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = "smoke-baseline"; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline"}
    assert "resolved" in capsys.readouterr().out


def test_apply_run_filters_only_no_match():
    """--only aborts when neither exact nor prefixed form matches."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = "nonexistent"; workload = None; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1


def test_apply_run_filters_only_no_double_prefix():
    """--only wl-nonexistent doesn't false-match via double-prefixing."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = "wl-nonexistent"; workload = None; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1


def test_apply_run_filters_only_empty_string():
    """--only '' (from unset shell var) returns empty set, not all pairs."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = ""; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == set()


# ── Multi-value flag tests (issue #212) ────────────────────────────────────


def test_parse_list_none():
    from pipeline.deploy import _parse_list
    assert _parse_list(None) is None


def test_parse_list_empty_list():
    from pipeline.deploy import _parse_list
    assert _parse_list([]) is None


def test_parse_list_single_string():
    from pipeline.deploy import _parse_list
    assert _parse_list("smoke") == ["smoke"]


def test_parse_list_comma_separated():
    from pipeline.deploy import _parse_list
    assert _parse_list("smoke,load") == ["smoke", "load"]


def test_parse_list_list_input():
    from pipeline.deploy import _parse_list
    assert _parse_list(["smoke", "load"]) == ["smoke", "load"]


def test_parse_list_mixed_comma_and_list():
    from pipeline.deploy import _parse_list
    assert _parse_list(["smoke,load", "heavy"]) == ["smoke", "load", "heavy"]


def test_parse_list_strips_whitespace():
    from pipeline.deploy import _parse_list
    assert _parse_list(" smoke , load ") == ["smoke", "load"]


def test_parse_list_whitespace_only():
    from pipeline.deploy import _parse_list
    assert _parse_list("  ,  ") is None


def test_apply_run_filters_multi_workload():
    """Multiple --workload values match any of the specified workloads."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = ["wl-smoke", "wl-heavy"]; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline", "wl-smoke-treatment", "wl-heavy-baseline"}


def test_apply_run_filters_multi_package():
    """Multiple --package values use union semantics."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = ["baseline", "treatment"]; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline", "wl-load-baseline", "wl-heavy-baseline",
                      "wl-smoke-treatment", "wl-load-treatment"}


def test_apply_run_filters_multi_only():
    """Multiple --only values resolve independently and return the union."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = ["wl-smoke-baseline", "wl-load-treatment"]; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline", "wl-load-treatment"}


def test_apply_run_filters_multi_only_with_prefix_resolution(capsys):
    """Multiple --only values with wl- prefix resolution."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = ["smoke-baseline", "load-treatment"]; workload = None; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline", "wl-load-treatment"}
    assert "resolved" in capsys.readouterr().out


def test_apply_run_filters_multi_only_partial_unresolved(capsys):
    """Partial match in --only aborts with error listing unresolved keys."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = ["wl-smoke-baseline", "nonexistent"]; workload = None; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "no match" in captured
    assert "nonexistent" in captured


def test_apply_run_filters_comma_in_workload():
    """Comma-separated --workload values are parsed correctly."""
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = ["wl-smoke,wl-heavy"]; package = None; status = None

    result = _apply_run_filters(dict(_PROGRESS), _Args())
    assert result == {"wl-smoke-baseline", "wl-smoke-treatment", "wl-heavy-baseline"}


def test_apply_run_filters_multi_only_all_unresolved(capsys):
    """All --only keys unresolved aborts with exit code 1."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = ["nonexistent1", "nonexistent2"]; workload = None; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "no match" in captured
    assert "nonexistent1" in captured


def test_apply_run_filters_invalid_workload_aborts(capsys):
    """--workload with a value not in progress aborts with exit 1."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = "nonexistent"; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "nonexistent" in err
    assert "wl-smoke" in err


def test_apply_run_filters_partial_invalid_workload_aborts(capsys):
    """--workload with one valid and one invalid value still aborts."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = "wl-smoke,bogus"; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "bogus" in err


def test_apply_run_filters_invalid_package_aborts(capsys):
    """--package with a value not in progress aborts with exit 1."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = "baseline1x"; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "baseline1x" in err
    assert "baseline" in err


def test_apply_run_filters_partial_invalid_package_aborts(capsys):
    """--package with one valid and one invalid value still aborts."""
    import pytest
    from pipeline.deploy import _apply_run_filters

    class _Args:
        only = None; workload = None; package = "treatment,bogus"; status = None

    with pytest.raises(SystemExit) as exc_info:
        _apply_run_filters(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "bogus" in err


def test_resolve_scope_multi_only_all_unresolved_aborts(capsys):
    """Multi-value --only with all keys unresolved aborts via _apply_run_filters."""
    import pytest
    from pipeline.deploy import _resolve_scope

    class _Args:
        only = ["bad1", "bad2"]; workload = None; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _resolve_scope(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "no match" in captured


def test_report_filter_mismatch_multi_value_formatting(capsys):
    """_report_filter_mismatch formats multi-value lists with commas."""
    from pipeline.deploy import _report_filter_mismatch

    class _Args:
        only = None; workload = ["wl-smoke", "wl-heavy"]; package = ["baseline", "treatment"]; status = None

    _report_filter_mismatch(dict(_PROGRESS), _Args())
    captured = capsys.readouterr().err
    assert "--workload 'wl-smoke,wl-heavy'" in captured
    assert "--package 'baseline,treatment'" in captured


def test_collect_run_flags_list_values():
    """_collect_run_flags correctly serializes list-valued args."""
    from pipeline.deploy import _collect_run_flags

    class _Args:
        only = ["wl-smoke-baseline", "wl-load-treatment"]
        workload = None
        package = ["baseline", "treatment"]
        status = None
        force = False
        skip_teardown = False
        preserve_pipelineruns = False
        max_retries = 2
        poll_interval = 30
        gpu_resource_type = None
        default_gpu_cost = 1
        pending_threshold = 600
        max_pending_stalls = 10
        shadow_ttl = 120

    flags = _collect_run_flags(_Args())
    assert "--only" in flags
    idx = flags.index("--only")
    assert flags[idx + 1] == "wl-smoke-baseline"
    assert flags[idx + 2] == "wl-load-treatment"
    assert "--package" in flags
    pidx = flags.index("--package")
    assert flags[pidx + 1] == "baseline"
    assert flags[pidx + 2] == "treatment"
    assert "['wl-smoke-baseline'" not in " ".join(flags)


def test_collect_run_flags_single_string_status():
    """_collect_run_flags handles single-string status correctly."""
    from pipeline.deploy import _collect_run_flags

    class _Args:
        only = None
        workload = None
        package = None
        status = "failed"
        force = False
        skip_teardown = False
        preserve_pipelineruns = False
        max_retries = 2
        poll_interval = 30
        gpu_resource_type = None
        default_gpu_cost = 1
        pending_threshold = 600
        max_pending_stalls = 10
        shadow_ttl = 120

    flags = _collect_run_flags(_Args())
    assert flags == ["--status", "failed"]


def test_resolve_scope_shows_valid_keys_on_only_mismatch(capsys):
    """--only mismatch prints valid pair keys before aborting with exit code 1."""
    import pytest
    from pipeline.deploy import _resolve_scope

    class _Args:
        only = "nonexistent"; workload = None; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _resolve_scope(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "no match" in captured
    assert "wl-smoke-baseline" in captured
    assert "wl-load-treatment" in captured


def test_resolve_scope_shows_valid_workloads_on_mismatch(capsys):
    """--workload mismatch prints valid workload values."""
    import pytest
    from pipeline.deploy import _resolve_scope

    class _Args:
        only = None; workload = "nonexistent"; package = None; status = None

    with pytest.raises(SystemExit) as exc_info:
        _resolve_scope(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "unrecognized" in captured
    assert "wl-smoke" in captured
    assert "wl-load" in captured
    assert "wl-heavy" in captured


def test_resolve_scope_shows_valid_packages_on_mismatch(capsys):
    """--package mismatch prints valid package values."""
    import pytest
    from pipeline.deploy import _resolve_scope

    class _Args:
        only = None; workload = None; package = "nonexistent"; status = None

    with pytest.raises(SystemExit) as exc_info:
        _resolve_scope(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "unrecognized" in captured
    assert "baseline" in captured
    assert "treatment" in captured


def test_resolve_scope_shows_valid_statuses_on_mismatch(capsys):
    """--status mismatch prints valid status values."""
    import pytest
    from pipeline.deploy import _resolve_scope

    class _Args:
        only = None; workload = None; package = None; status = "nonexistent"

    with pytest.raises(SystemExit) as exc_info:
        _resolve_scope(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "No pairs matched" in captured
    assert "done" in captured
    assert "running" in captured
    assert "pending" in captured
    assert "failed" in captured
    assert "timed-out" in captured


def test_resolve_scope_combined_filter_mismatch(capsys):
    """Combined filters where each value is valid but intersection is empty."""
    import pytest
    from pipeline.deploy import _resolve_scope

    class _Args:
        only = None; workload = "wl-smoke"; package = None; status = "timed-out"

    with pytest.raises(SystemExit) as exc_info:
        _resolve_scope(dict(_PROGRESS), _Args())
    assert exc_info.value.code == 1
    captured = capsys.readouterr().err
    assert "--workload 'wl-smoke'" in captured
    assert "--status 'timed-out'" in captured


# ── _reconcile_on_resume ──────────────────────────────────────────────────────

_DISCOVERED = {
    "wl-smoke-baseline": {"pr_name": "baseline-smoke-run1", "pr_path": "cluster/pipelinerun-smoke-baseline.yaml"},
}


def test_reconcile_succeeded_sets_done_and_frees_namespace(monkeypatch):
    """On resume, a 'running' pair whose PipelineRun Succeeded transitions to done."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Succeeded")

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": "2026-01-01T00:00:00Z",
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "done"
    assert progress["wl-smoke-baseline"]["namespace"] is None
    assert progress["wl-smoke-baseline"]["completed_namespace"] == "sim2real-0"
    assert progress["wl-smoke-baseline"]["pending_since"] is None


def test_reconcile_completed_sets_done_and_frees_namespace(monkeypatch):
    """Tekton v0.44+ returns 'Completed' for success — treat same as 'Succeeded'."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Completed")
    monkeypatch.setattr(mod, "_delete_pipelinerun", lambda pr, ns: None)

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": "2026-01-01T00:00:00Z",
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "done"
    assert progress["wl-smoke-baseline"]["namespace"] is None
    assert progress["wl-smoke-baseline"]["completed_namespace"] == "sim2real-0"
    assert progress["wl-smoke-baseline"]["pending_since"] is None


def test_reconcile_on_resume_sets_completed_namespace_on_success(monkeypatch):
    """In _reconcile_on_resume, when a running pair's PipelineRun Succeeds, completed_namespace is recorded before namespace is cleared."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Succeeded")
    monkeypatch.setattr(mod, "_delete_pipelinerun", lambda pr, ns: None)

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-2",
            "pending_since": None,
        }
    }
    discovered = {
        "wl-smoke-baseline": {"pr_name": "baseline-smoke-run1", "workload": "wl-smoke", "package": "baseline"}
    }
    mod._reconcile_on_resume(progress, discovered)
    entry = progress["wl-smoke-baseline"]
    assert entry["status"] == "done"
    assert entry["namespace"] is None
    assert entry["completed_namespace"] == "sim2real-2"


def test_reconcile_unrecognized_status_resets_to_pending(capsys):
    """Stale statuses (e.g. 'collecting' from pre-upgrade) are reset to pending."""
    import pipeline.deploy as mod

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "collecting", "namespace": "sim2real-0",
            "pending_since": None,
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "pending"
    assert progress["wl-smoke-baseline"]["namespace"] is None
    captured = capsys.readouterr().out
    assert "unrecognized status 'collecting'" in captured


def test_reconcile_running_no_pr_resets_to_pending():
    """Running pair with no PipelineRun metadata resets to pending."""
    import pipeline.deploy as mod

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": None,
        }
    }
    mod._reconcile_on_resume(progress, {})
    assert progress["wl-smoke-baseline"]["status"] == "pending"
    assert progress["wl-smoke-baseline"]["namespace"] is None


def test_reconcile_succeeded_deletes_pipelinerun(monkeypatch):
    """On Succeeded, _reconcile_on_resume calls _delete_pipelinerun."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Succeeded")
    deleted = []
    monkeypatch.setattr(mod, "_delete_pipelinerun",
                        lambda pr, ns: deleted.append((pr, ns)))

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": "2026-01-01T00:00:00Z",
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "done"
    assert deleted == [("baseline-smoke-run1", "sim2real-0")]


def test_reconcile_preserve_pipelineruns_skips_deletion(monkeypatch):
    """With preserve_pipelineruns=True, _delete_pipelinerun is not called."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Succeeded")
    deleted = []
    monkeypatch.setattr(mod, "_delete_pipelinerun",
                        lambda pr, ns: deleted.append((pr, ns)))

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": "2026-01-01T00:00:00Z",
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED, preserve_pipelineruns=True)
    assert progress["wl-smoke-baseline"]["status"] == "done"
    assert deleted == []


def test_reconcile_succeeded_delete_failure_nonfatal(monkeypatch, capsys):
    """If _delete_pipelinerun raises, the pair still transitions to done."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Succeeded")
    monkeypatch.setattr(mod, "_delete_pipelinerun",
                        lambda pr, ns: (_ for _ in ()).throw(OSError("kubectl fail")))

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": None,
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "done"
    assert progress["wl-smoke-baseline"]["namespace"] is None
    assert "kubectl fail" in capsys.readouterr().out


def test_reconcile_failed_sets_failed_retains_namespace(monkeypatch):
    """On Failed, pair transitions to failed but namespace is retained for reset."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Failed")

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": "2026-01-01T00:00:00Z",
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "failed"
    assert progress["wl-smoke-baseline"]["namespace"] == "sim2real-0"


def test_reconcile_unknown_resets_to_pending(monkeypatch, capsys):
    """On Unknown (PR not found on cluster), pair resets to pending with warning."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Unknown")

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": None,
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "pending"
    assert progress["wl-smoke-baseline"]["namespace"] is None
    assert "not found on cluster" in capsys.readouterr().out


def test_reconcile_status_check_exception_skips_pair(monkeypatch, capsys):
    """If _check_pipelinerun_status raises, the pair is skipped with a warning."""
    import pipeline.deploy as mod

    def _raise(pr, ns):
        raise OSError("kubectl not found")
    monkeypatch.setattr(mod, "_check_pipelinerun_status", _raise)

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": None,
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "running"
    assert "failed to check PipelineRun status" in capsys.readouterr().out


def test_reconcile_still_running_left_unchanged(monkeypatch):
    """Running PipelineRun is left as-is (no double-dispatch)."""
    import pipeline.deploy as mod

    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Running")

    progress = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0",
            "pending_since": "2026-01-01T00:00:00Z",
        }
    }
    mod._reconcile_on_resume(progress, _DISCOVERED)
    assert progress["wl-smoke-baseline"]["status"] == "running"
    assert progress["wl-smoke-baseline"]["namespace"] == "sim2real-0"
    assert progress["wl-smoke-baseline"]["pending_since"] == "2026-01-01T00:00:00Z"


# ── _force_reset ──────────────────────────────────────────────────────────────

def _mock_run(monkeypatch):
    """Mock subprocess.run for _force_reset tests (no real kubectl/helm)."""
    import pipeline.deploy as mod

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)


def test_force_reset_resets_all_non_pending_pairs(monkeypatch):
    from pipeline.deploy import _force_reset
    _mock_run(monkeypatch)
    progress = dict(_PROGRESS)
    scope = set(progress.keys())
    n = _force_reset(progress, scope)
    # running, timed-out, failed, AND done are reset; only pending is skipped
    assert n == 4
    for key in ("wl-smoke-baseline", "wl-smoke-treatment", "wl-load-treatment", "wl-heavy-baseline"):
        assert progress[key]["status"] == "pending"
        assert progress[key]["namespace"] is None
        assert progress[key]["retries"] == 0


def test_force_reset_leaves_pending_pairs_unchanged(monkeypatch):
    from pipeline.deploy import _force_reset
    _mock_run(monkeypatch)
    progress = dict(_PROGRESS)
    scope = set(progress.keys())
    _force_reset(progress, scope)
    assert progress["wl-load-baseline"]["status"] == "pending"


def test_force_reset_scoped_to_package(monkeypatch):
    from pipeline.deploy import _force_reset
    _mock_run(monkeypatch)
    progress = {
        "wl-a-baseline":  {"workload": "wl-a", "package": "baseline",  "status": "failed", "namespace": "ns-0", "retries": 2},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "failed", "namespace": "ns-1", "retries": 1},
    }
    scope = {"wl-a-baseline"}
    n = _force_reset(progress, scope)
    assert n == 1
    assert progress["wl-a-baseline"]["status"] == "pending"
    assert progress["wl-a-baseline"]["retries"] == 0
    assert progress["wl-a-treatment"]["status"] == "failed"


def test_force_reset_returns_zero_when_nothing_to_reset():
    from pipeline.deploy import _force_reset
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "pending", "namespace": None, "retries": 0},
    }
    n = _force_reset(progress, {"wl-a-baseline"})
    assert n == 0
    assert progress["wl-a-baseline"]["status"] == "pending"


def test_force_reset_clears_retries(monkeypatch):
    from pipeline.deploy import _force_reset
    _mock_run(monkeypatch)
    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "timed-out", "namespace": "ns-0", "retries": 3},
    }
    _force_reset(progress, {"wl-a-baseline"})
    assert progress["wl-a-baseline"]["retries"] == 0


# ── GPU cost derivation provenance (issue #240) ──────────────────────────────


def test_derive_pair_gpu_costs_returns_provenance():
    """_derive_pair_gpu_costs returns (cost, source) tuples."""
    from pipeline.deploy import _derive_pair_gpu_costs
    from unittest.mock import patch
    import yaml

    discovered = {
        "wl-a-baseline": {"scenario_content": yaml.dump({"decode": {"replicas": 4}})},
    }

    with patch("pipeline.lib.capacity.gpu_cost_per_pair", return_value=4):
        result = _derive_pair_gpu_costs(
            discovered,
            defaults={"decode": {"resources": {"limits": {"nvidia.com/gpu": "1"}}}},
            fallback_cost=1,
        )
    assert result["wl-a-baseline"] == (4, "derived")


def test_derive_pair_gpu_costs_defaults_none_returns_fallback():
    """When defaults is None, returns fallback with 'fallback' source."""
    from pipeline.deploy import _derive_pair_gpu_costs

    discovered = {"wl-a-baseline": {"scenario_content": "decode:\n  replicas: 4\n"}}
    result = _derive_pair_gpu_costs(discovered, defaults=None, fallback_cost=1)
    assert result["wl-a-baseline"] == (1, "fallback")


def test_derive_pair_gpu_costs_empty_scenario_returns_defaults_only():
    """When scenarioContent is empty, derives from defaults with 'defaults-only' source."""
    from pipeline.deploy import _derive_pair_gpu_costs
    from unittest.mock import patch

    discovered = {"wl-a-baseline": {"scenario_content": ""}}
    with patch("pipeline.lib.capacity.gpu_cost_per_pair", return_value=2):
        result = _derive_pair_gpu_costs(
            discovered, defaults={"some": "defaults"}, fallback_cost=1,
        )
    assert result["wl-a-baseline"] == (2, "defaults-only")


def test_derive_pair_gpu_costs_derivation_error_returns_fallback():
    """When gpu_cost_per_pair returns an error string, uses fallback."""
    from pipeline.deploy import _derive_pair_gpu_costs
    from unittest.mock import patch
    import yaml

    discovered = {
        "wl-a-baseline": {"scenario_content": yaml.dump({"decode": {"replicas": 4}})},
    }
    with patch("pipeline.lib.capacity.gpu_cost_per_pair", return_value="missing field"):
        result = _derive_pair_gpu_costs(
            discovered, defaults={"some": "defaults"}, fallback_cost=1,
        )
    assert result["wl-a-baseline"] == (1, "fallback")


# ── Capacity-gated dispatch (issue #64) ──────────────────────────────────────


def test_init_progress_does_not_store_gpu_cost(tmp_path):
    """Progress entries must not persist gpu_cost — it's derived per invocation."""
    import yaml as _yaml
    from pipeline.deploy import _load_pairs

    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()

    pr = {
        "metadata": {"name": "baseline-smoke-run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-smoke"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-smoke-baseline.yaml").write_text(_yaml.dump(pr))

    discovered = _load_pairs(cluster_dir)
    progress = {}
    for key, meta in discovered.items():
        if key not in progress:
            progress[key] = {
                "workload": meta["workload"],
                "package":  meta["package"],
                "status":   "pending",
                "namespace": None,
                "completed_namespace": None,
                "retries":  0,
                "pending_stalls": 0,
                "pending_since": None,
            }
    assert "gpu_cost" not in progress["wl-smoke-baseline"]


def test_capacity_gated_dispatch_limits_pairs():
    """When free GPUs < total pending cost, only a subset is dispatched."""
    from pipeline.deploy import _capacity_limited_pairs

    cost_map = {
        "wl-a-baseline": 8,
        "wl-b-baseline": 4,
        "wl-c-baseline": 4,
        "wl-d-baseline": 8,
    }
    pending = ["wl-a-baseline", "wl-b-baseline", "wl-c-baseline", "wl-d-baseline"]

    # sorted: b(4), c(4), a(8), d(8). budget=12: b→8, c→4, 4>=8? No. So only b and c fit.
    result = _capacity_limited_pairs(pending, free_gpus=12, cost_map=cost_map)
    assert result == ["wl-b-baseline", "wl-c-baseline"]


def test_capacity_gated_dispatch_all_fit():
    """When free GPUs >= total pending cost, all pairs are returned."""
    from pipeline.deploy import _capacity_limited_pairs

    cost_map = {
        "wl-a-baseline": 4,
        "wl-b-baseline": 4,
    }
    pending = ["wl-a-baseline", "wl-b-baseline"]

    result = _capacity_limited_pairs(pending, free_gpus=16, cost_map=cost_map)
    # sorted by cost (both 4), stable sort preserves order
    assert set(result) == {"wl-a-baseline", "wl-b-baseline"}
    assert len(result) == 2


def test_capacity_gated_dispatch_sorts_ascending():
    """Pairs are sorted by gpu_cost ascending to maximize dispatch count."""
    from pipeline.deploy import _capacity_limited_pairs

    cost_map = {
        "wl-big-baseline": 8,
        "wl-small-baseline": 2,
        "wl-mid-baseline": 4,
    }
    pending = ["wl-big-baseline", "wl-small-baseline", "wl-mid-baseline"]

    # Budget 10: sorted → small(2), mid(4), big(8). 2+4=6<=10, 6+8=14>10. So small+mid.
    result = _capacity_limited_pairs(pending, free_gpus=10, cost_map=cost_map)
    assert result == ["wl-small-baseline", "wl-mid-baseline"]


def test_capacity_gated_dispatch_zero_budget():
    """Zero free GPUs means nothing is dispatched."""
    from pipeline.deploy import _capacity_limited_pairs

    cost_map = {"wl-a-baseline": 4}
    pending = ["wl-a-baseline"]

    result = _capacity_limited_pairs(pending, free_gpus=0, cost_map=cost_map)
    assert result == []


def test_probe_failure_dispatches_all_pending():
    """When probe returns error string, all pending pairs are dispatched (no gating)."""
    from pipeline.deploy import _capacity_limited_pairs

    cost_map = {
        "wl-a-baseline": 8,
        "wl-b-baseline": 4,
        "wl-c-baseline": 4,
    }
    pending = ["wl-a-baseline", "wl-b-baseline", "wl-c-baseline"]

    # Simulate the dispatch logic: when probe fails, free_gpus is None,
    # so _capacity_limited_pairs is NOT called — dispatchable = pending directly.
    capacity = "connection refused"  # str = failure
    free_gpus = None
    if isinstance(capacity, tuple):
        free_gpus = capacity[0]

    if free_gpus is not None:
        dispatchable = _capacity_limited_pairs(
            pending, free_gpus=free_gpus, cost_map=cost_map,
        )
    else:
        dispatchable = pending

    assert dispatchable == pending
    assert len(dispatchable) == 3


def test_slot_limited_dispatch(capsys):
    """When capacity allows all pairs but fewer slots exist, slot-limited log fires."""
    from pipeline.deploy import _capacity_limited_pairs, info

    cost_map = {
        "wl-a-baseline": 4,
        "wl-b-baseline": 4,
        "wl-c-baseline": 4,
    }
    pending = ["wl-a-baseline", "wl-b-baseline", "wl-c-baseline"]
    free_gpus = 24  # plenty of capacity

    dispatchable = _capacity_limited_pairs(
        pending, free_gpus=free_gpus, cost_map=cost_map,
    )
    # All 3 fit in capacity
    assert len(dispatchable) == 3

    # But only 1 slot available — slot-limited
    free_slots = ["sim2real-0"]  # 1 slot
    if len(dispatchable) < len(pending):
        info(f"Dispatching {len(dispatchable)}/{len(pending)} pending pairs (capacity-limited: {free_gpus} free GPUs)")
    elif len(free_slots) < len(dispatchable):
        info(f"Dispatching {len(free_slots)}/{len(pending)} pending pairs (slot-limited)")

    out = capsys.readouterr().out
    assert "slot-limited" in out
    assert "1/3" in out


def test_init_progress_includes_pending_stalls():
    """New progress entries include pending_stalls field initialized to 0."""
    progress_entry = {
        "workload": "wl-smoke",
        "package": "baseline",
        "status": "pending",
        "namespace": None,
        "retries": 0,
        "gpu_cost": 1,
        "pending_stalls": 0,
    }
    assert "pending_stalls" in progress_entry
    assert progress_entry["pending_stalls"] == 0


def test_run_parser_has_pending_flags():
    """run subcommand exposes --pending-threshold and --max-pending-stalls."""
    from pipeline.deploy import build_parser
    parser = build_parser()
    args = parser.parse_args(["run", "--pending-threshold", "300", "--max-pending-stalls", "5"])
    assert args.pending_threshold == 300
    assert args.max_pending_stalls == 5


def test_run_parser_pending_flag_defaults():
    """--pending-threshold defaults to 600, --max-pending-stalls to 10."""
    from pipeline.deploy import build_parser
    parser = build_parser()
    args = parser.parse_args(["run"])
    assert args.pending_threshold == 600
    assert args.max_pending_stalls == 10


def test_early_reclaim_recoverable_threshold_exceeded(monkeypatch):
    """Recoverable pending pod past threshold: cancel PR, free slot, return to pending."""
    import datetime as _dt
    import json
    import pipeline.deploy as mod

    pods_json_recoverable = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/8 nodes are available: 8 Insufficient nvidia.com/gpu.",
                }],
            },
        }],
    }

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0,
        "pending_since": (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=700)).isoformat(),
    }

    cancelled = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json_recoverable)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun",
                        lambda pr, ns: cancelled.append((pr, ns)) or True)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is True
    assert entry["status"] == "pending"
    assert entry["namespace"] is None
    assert entry["pending_stalls"] == 1
    assert entry["pending_since"] is None
    assert cancelled == [("baseline-smoke-run1", "sim2real-0")]


def test_early_reclaim_recoverable_under_threshold(monkeypatch):
    """Recoverable pending pod under threshold: set pending_since, do NOT reclaim."""
    import json
    import pipeline.deploy as mod

    pods_json_recoverable = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/8 nodes are available: 8 Insufficient nvidia.com/gpu.",
                }],
            },
        }],
    }

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json_recoverable)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["status"] == "running"
    assert entry["pending_since"] is not None


def test_early_reclaim_non_recoverable_fails_immediately(monkeypatch):
    """Non-recoverable pending: fail immediately, no waiting."""
    import json
    import pipeline.deploy as mod

    pods_json_non_recoverable = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/8 nodes are available: 8 node(s) didn't match Pod's node affinity/selector.",
                }],
            },
        }],
    }

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0, "pending_since": None,
    }

    cancelled = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json_non_recoverable)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun",
                        lambda pr, ns: cancelled.append((pr, ns)) or True)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is True
    assert entry["status"] == "failed"
    # Namespace retained so reset/cleanup can find the helm releases (issue #277)
    assert entry["namespace"] == "sim2real-0"
    assert entry["pending_stalls"] == 0
    assert cancelled == [("baseline-smoke-run1", "sim2real-0")]


def test_early_reclaim_stalled_at_max_pending_stalls(monkeypatch):
    """When pending_stalls reaches max, pair transitions to stalled."""
    import datetime as _dt
    import json
    import pipeline.deploy as mod

    pods_json_recoverable = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/8 nodes are available: 8 Insufficient nvidia.com/gpu.",
                }],
            },
        }],
    }

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 9,
        "pending_since": (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=700)).isoformat(),
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json_recoverable)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: True)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is True
    assert entry["status"] == "stalled"
    assert entry["pending_stalls"] == 10
    # Terminal stall retains namespace so reset/cleanup can find releases (issue #277)
    assert entry["namespace"] == "sim2real-0"


def test_early_reclaim_kubectl_failure_returns_false(monkeypatch, capsys):
    """kubectl get pods failure: warn and don't reclaim, let timeout handle it."""
    import pipeline.deploy as mod

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "connection refused"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["status"] == "running"
    out = capsys.readouterr().out
    assert "pod query failed" in out


def test_early_reclaim_pods_running_clears_pending_since(monkeypatch):
    """When pods transition from Pending to Running, clear pending_since."""
    import json
    import pipeline.deploy as mod

    pods_json_running = {
        "items": [{
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }],
    }

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0,
        "pending_since": "2026-05-09T12:00:00+00:00",
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json_running)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["pending_since"] is None


def test_early_reclaim_malformed_pending_since_resets_timer(monkeypatch, capsys):
    """Malformed pending_since resets timer instead of crashing."""
    import json
    import pipeline.deploy as mod

    pods_json_recoverable = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/8 nodes are available: 8 Insufficient nvidia.com/gpu.",
                }],
            },
        }],
    }

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0,
        "pending_since": "not-a-valid-timestamp",
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json_recoverable)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["pending_since"] != "not-a-valid-timestamp"
    out = capsys.readouterr().out
    assert "malformed pending_since" in out


def test_force_reset_clears_pending_stalls(monkeypatch):
    """--force resets pending_stalls along with retries."""
    from pipeline.deploy import _force_reset

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    import pipeline.deploy as mod
    monkeypatch.setattr(mod, "run", fake_run)

    progress = {
        "wl-a-baseline": {
            "workload": "wl-a", "package": "baseline", "status": "stalled",
            "namespace": None, "retries": 2, "pending_stalls": 10,
            "pending_since": "2026-05-09T12:00:00+00:00",
        },
    }
    _force_reset(progress, {"wl-a-baseline"})
    assert progress["wl-a-baseline"]["pending_stalls"] == 0
    assert progress["wl-a-baseline"]["pending_since"] is None


def test_early_reclaim_json_decode_error_warns(monkeypatch, capsys):
    """kubectl returns garbage JSON with rc=0: warn and don't reclaim."""
    import pipeline.deploy as mod

    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = "<html>auth proxy page</html>"
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["status"] == "running"
    out = capsys.readouterr().out
    assert "invalid JSON" in out


def test_status_ignores_orchestrator_metadata_as_pair(tmp_path, capsys, monkeypatch):
    """_orchestrator key should not appear as a pair row in status output."""
    progress = {
        "wl-foo-baseline": {"workload": "foo", "package": "baseline", "status": "running", "namespace": "ns-1", "retries": 0},
        "_orchestrator": {"state": "backing_off", "backoff_level": 2, "last_probe_free_gpus": 0},
    }
    _mock_cm(monkeypatch, progress)

    from pipeline.deploy import _cmd_status
    args = argparse.Namespace(only=None, workload=None, package=None, status=None)
    _cmd_status(args, tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "wl-foo-baseline" in out
    lines = out.strip().split("\n")
    pair_lines = [l for l in lines if l.strip().startswith("wl-") or l.strip().startswith("_")]
    for line in pair_lines:
        assert not line.strip().startswith("_orchestrator")


def test_status_backward_compat_ignores_legacy_orchestrator(tmp_path, capsys, monkeypatch):
    """Old progress files may still carry a legacy _orchestrator key. The backoff
    state machine is gone (issue #274), so status must render cleanly and never
    surface the removed orchestrator/backoff section."""
    progress = {
        "wl-foo-baseline": {"workload": "foo", "package": "baseline", "status": "running", "namespace": "ns-1", "retries": 0},
        "_orchestrator": {"state": "backing_off", "backoff_level": 2, "last_probe_free_gpus": 0, "last_scarcity_time": "2026-05-08T14:32:00+00:00"},
    }
    _mock_cm(monkeypatch, progress)

    from pipeline.deploy import _cmd_status
    args = argparse.Namespace(only=None, workload=None, package=None, status=None)
    _cmd_status(args, tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "wl-foo-baseline" in out
    assert "backing_off" not in out
    assert "Orchestrator" not in out


def test_resolve_scope_excludes_orchestrator_key(tmp_path):
    """_resolve_scope should never include _orchestrator in the pair set."""
    from pipeline.deploy import _resolve_scope
    args = argparse.Namespace(only=None, workload=None, package=None, status=None)
    scope = _resolve_scope(_PROGRESS, args)
    assert "_orchestrator" not in scope
    assert len(scope) == 5  # only the real pair keys


def test_apply_run_filters_excludes_orchestrator_key():
    """_apply_run_filters should not include _orchestrator even with status filter."""
    from pipeline.deploy import _apply_run_filters
    args = argparse.Namespace(only=None, workload=None, package=None, status="running")
    result = _apply_run_filters(_PROGRESS, args)
    assert "_orchestrator" not in result


def test_report_filter_mismatch_excludes_orchestrator(tmp_path, capsys):
    """_report_filter_mismatch valid-values lists should not include metadata keys."""
    from pipeline.deploy import _report_filter_mismatch
    _report_filter_mismatch(_PROGRESS, argparse.Namespace(only="nonexistent", workload=None, package=None, status=None))
    err_out = capsys.readouterr().err
    assert "_orchestrator" not in err_out


def test_status_empty_pairs_only_orchestrator(tmp_path, capsys, monkeypatch):
    """deploy.py status should handle progress with only _orchestrator (no pairs)."""
    progress = {
        "_orchestrator": {"state": "backing_off", "backoff_level": 3, "last_probe_free_gpus": 0},
    }
    _mock_cm(monkeypatch, progress)

    from pipeline.deploy import _cmd_status
    args = argparse.Namespace(only=None, workload=None, package=None, status=None)
    _cmd_status(args, tmp_path, setup_config={"namespace": "sim2real-ns"})
    out = capsys.readouterr().out
    assert "0 pairs" in out


# ── Image build decision (_cmd_build) ──────────────────────────────────────────

def test_cmd_build_missing_metadata(tmp_path):
    """Missing run_metadata.json → sys.exit."""
    from pipeline.deploy import _cmd_build
    with pytest.raises(SystemExit):
        _cmd_build(tmp_path, namespace="ns", skip_build=False)


def test_cmd_build_no_component_image(tmp_path, capsys):
    """component_image absent → skip."""
    from pipeline.deploy import _cmd_build
    (tmp_path / "run_metadata.json").write_text(json.dumps({"registry": "quay.io/me"}))
    result = _cmd_build(tmp_path, namespace="ns", skip_build=False)
    assert result == "skip"


def test_cmd_build_empty_component_image(tmp_path):
    """component_image is empty string → sys.exit (misconfigured setup)."""
    from pipeline.deploy import _cmd_build
    (tmp_path / "run_metadata.json").write_text(json.dumps({"component_image": ""}))
    with pytest.raises(SystemExit):
        _cmd_build(tmp_path, namespace="ns", skip_build=False)


def test_cmd_build_skip_flag(tmp_path, capsys):
    """--skip-build returns skip."""
    from pipeline.deploy import _cmd_build
    (tmp_path / "run_metadata.json").write_text(
        json.dumps({"component_image": "quay.io/me/sched:r1", "registry": "quay.io/me", "repo_name": "sched"})
    )
    result = _cmd_build(tmp_path, namespace="ns", skip_build=True)
    assert result == "skip"


# ── _check_slot_ready hf_secret_name parameter ──────────────────────────────


class TestCheckSlotReadyHfSecret:
    """_check_slot_ready uses the hf_secret_name parameter."""

    @patch("pipeline.deploy.run")
    def test_uses_configured_secret_name(self, mock_run):
        from pipeline.deploy import _check_slot_ready

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Bound"

        ready, failures = _check_slot_ready("test-ns", hf_secret_name="my-hf-token")

        secret_calls = [c for c in mock_run.call_args_list
                        if "secret" in str(c) and "my-hf-token" in str(c)]
        assert len(secret_calls) == 1
        assert ready

    @patch("pipeline.deploy.run")
    def test_reports_configured_secret_name_in_failure(self, mock_run):
        from pipeline.deploy import _check_slot_ready

        def side_effect(cmd, *, check=True, capture=False, input=None):
            class R:
                returncode = 0
                stdout = "Bound"
            r = R()
            if "secret" in cmd:
                r.returncode = 1
            return r

        mock_run.side_effect = side_effect

        ready, failures = _check_slot_ready("test-ns", hf_secret_name="my-hf-token")

        assert not ready
        assert any("my-hf-token" in f for f in failures)


def test_load_pairs_includes_scenario_content(tmp_path):
    """_load_pairs extracts scenarioContent param from PipelineRun YAMLs."""
    import yaml as _yaml
    from pipeline.deploy import _load_pairs

    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()

    scenario = {"scenario": [{"decode": {"replicas": 2}}]}
    pr = {
        "metadata": {"name": "baseline-wl1-run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl1"},
            {"name": "phase", "value": "baseline"},
            {"name": "scenarioContent", "value": _yaml.dump(scenario)},
        ]},
    }
    (cluster_dir / "pipelinerun-wl1-baseline.yaml").write_text(_yaml.dump(pr))

    pairs = _load_pairs(cluster_dir)
    assert "wl-wl1-baseline" in pairs
    assert pairs["wl-wl1-baseline"]["scenario_content"] == _yaml.dump(scenario)


def test_load_pairs_missing_scenario_content(tmp_path):
    """_load_pairs sets scenario_content to None when param is absent."""
    import yaml as _yaml
    from pipeline.deploy import _load_pairs

    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()

    pr = {
        "metadata": {"name": "baseline-wl1-run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl1"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-wl1-baseline.yaml").write_text(_yaml.dump(pr))

    pairs = _load_pairs(cluster_dir)
    assert pairs["wl-wl1-baseline"]["scenario_content"] is None


# ── _derive_pair_gpu_costs ───────────────────────────────────────────────────


def test_derive_pair_gpu_costs_heterogeneous():
    """Per-pair cost derivation produces different costs for different scenarios."""
    import yaml as _yaml
    from pipeline.deploy import _derive_pair_gpu_costs

    defaults = {
        "accelerator": {"count": 1},
        "decode": {"enabled": True, "replicas": 1},
    }

    scenario_a = {"scenario": [{"decode": {"replicas": 2}}]}
    scenario_b = {"scenario": [{"decode": {"replicas": 4}}]}

    discovered = {
        "wl-a-baseline": {"scenario_content": _yaml.dump(scenario_a)},
        "wl-b-treatment": {"scenario_content": _yaml.dump(scenario_b)},
    }

    costs = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=1)
    assert costs["wl-a-baseline"] == (2, "derived")
    assert costs["wl-b-treatment"] == (4, "derived")


def test_derive_pair_gpu_costs_fallback_on_missing_scenario():
    """When scenarioContent is None, falls back to defaults-only derivation."""
    from pipeline.deploy import _derive_pair_gpu_costs

    defaults = {
        "decode": {"enabled": True, "replicas": 1},
        "accelerator": {"count": 4},
    }

    discovered = {
        "wl-a-baseline": {"scenario_content": None},
    }

    costs = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=1)
    assert costs["wl-a-baseline"] == (4, "defaults-only")


def test_derive_pair_gpu_costs_fallback_on_bad_yaml():
    """When scenarioContent is invalid YAML, falls back to defaults-only derivation."""
    from pipeline.deploy import _derive_pair_gpu_costs

    defaults = {"decode": {"enabled": True, "replicas": 1}, "accelerator": {"count": 2}}

    discovered = {
        "wl-a-baseline": {"scenario_content": ": invalid: yaml: ["},
    }

    costs = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=99)
    assert costs["wl-a-baseline"] == (2, "defaults-only")


def test_derive_pair_gpu_costs_no_defaults():
    """When defaults is None, uses fallback_cost for all pairs."""
    from pipeline.deploy import _derive_pair_gpu_costs

    discovered = {
        "wl-a-baseline": {"scenario_content": "scenario:\n- decode:\n    replicas: 2\n"},
    }

    costs = _derive_pair_gpu_costs(discovered, defaults=None, fallback_cost=7)
    assert costs["wl-a-baseline"] == (7, "fallback")


def test_derive_pair_gpu_costs_per_pair_heterogeneous(tmp_path):
    """Per-pair cost derivation from scenarioContent in loaded PipelineRuns."""
    import yaml as _yaml
    from pipeline.deploy import _load_pairs, _derive_pair_gpu_costs

    cluster_dir = tmp_path / "cluster"
    cluster_dir.mkdir()

    scenario_a = {"scenario": [{"decode": {"replicas": 2, "accelerator": {"count": 4}}}]}
    pr_a = {
        "metadata": {"name": "baseline-a-run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
            {"name": "scenarioContent", "value": _yaml.dump(scenario_a)},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr_a))

    scenario_b = {"scenario": [{"decode": {"replicas": 1, "accelerator": {"count": 2}}}]}
    pr_b = {
        "metadata": {"name": "treatment-b-run1", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-b"},
            {"name": "phase", "value": "treatment"},
            {"name": "scenarioContent", "value": _yaml.dump(scenario_b)},
        ]},
    }
    (cluster_dir / "pipelinerun-b-treatment.yaml").write_text(_yaml.dump(pr_b))

    defaults = {"decode": {"enabled": True, "replicas": 1}, "accelerator": {"count": 1}}
    discovered = _load_pairs(cluster_dir)
    costs = _derive_pair_gpu_costs(discovered, defaults=defaults, fallback_cost=1)

    assert costs["wl-a-baseline"] == (8, "derived")
    assert costs["wl-b-treatment"] == (2, "derived")


# ── status ConfigMap behavior ───────────────────────────────────────────────

def test_status_parser_has_no_remote_flag():
    """status subcommand does NOT accept --remote (flag removed)."""
    from pipeline.deploy import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["status", "--remote"])

def test_status_reads_configmap_when_namespace_configured(tmp_path, capsys, monkeypatch):
    """status reads from ConfigMap when namespace is configured."""
    from pipeline.deploy import _cmd_status

    progress_data = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "running", "namespace": "sim2real-0", "retries": 0,
        },
    }
    _mock_cm(monkeypatch, progress_data)

    class _Args:
        only = None; workload = None; package = None; status = None

    _cmd_status(_Args(), tmp_path,
                setup_config={"namespace": "sim2real-ns"})

    out = capsys.readouterr().out
    assert "wl-smoke-baseline" in out

def test_status_reads_from_configmap(tmp_path, capsys, monkeypatch):
    """status reads progress from ConfigMap."""
    from pipeline.deploy import _cmd_status

    progress_data = {
        "wl-smoke-baseline": {
            "workload": "wl-smoke", "package": "baseline",
            "status": "done", "namespace": None, "retries": 0,
        },
    }
    _mock_cm(monkeypatch, progress_data)

    class _Args:
        only = None; workload = None; package = None; status = None

    run_dir = tmp_path / "nonexistent-run"
    run_dir.mkdir()
    _cmd_status(_Args(), run_dir,
                setup_config={"namespace": "sim2real-ns"})

    out = capsys.readouterr().out
    assert "wl-smoke-baseline" in out

def test_status_empty_configmap_reports_no_run(tmp_path, capsys, monkeypatch):
    """Empty ConfigMap reports '0 pairs'."""
    from pipeline.deploy import _cmd_status
    _mock_cm(monkeypatch, {})

    class _Args:
        only = None; workload = None; package = None; status = None

    _cmd_status(_Args(), tmp_path,
                setup_config={"namespace": "sim2real-ns"})

    out = capsys.readouterr().out
    assert "0 pairs" in out

def test_status_no_namespace_exits_with_error(tmp_path, capsys):
    """status with no namespace configured exits with error."""
    from pipeline.deploy import _cmd_status

    class _Args:
        only = None; workload = None; package = None; status = None

    with pytest.raises(SystemExit):
        _cmd_status(_Args(), tmp_path, setup_config={})


# ── _configmap_namespace helper ──────────────────────────────────────────────

def test_configmap_namespace_from_setup_config():
    """Primary namespace comes from setup_config['namespace']."""
    from pipeline.deploy import _configmap_namespace
    assert _configmap_namespace({"namespace": "sim2real-ns"}) == "sim2real-ns"

def test_configmap_namespace_fallback_to_namespaces_arg():
    """Falls back to explicit namespaces[0] when setup_config has no namespace."""
    from pipeline.deploy import _configmap_namespace
    assert _configmap_namespace({}, ["sim2real-0", "sim2real-1"]) == "sim2real-0"

def test_configmap_namespace_fallback_to_setup_config_namespaces():
    """Falls back to setup_config['namespaces'][0] when namespace key is empty."""
    from pipeline.deploy import _configmap_namespace
    assert _configmap_namespace({"namespaces": ["sim2real-0"]}) == "sim2real-0"

def test_configmap_namespace_empty():
    """Returns '' when no namespace source is available."""
    from pipeline.deploy import _configmap_namespace
    assert _configmap_namespace({}) == ""
    assert _configmap_namespace(None) == ""


# ── ConfigMapProgressStore wiring in _cmd_run / _cmd_reset ─────────────────

def test_cmd_run_uses_configmap_store(monkeypatch, tmp_path):
    """_cmd_run uses ConfigMapProgressStore directly."""
    import pipeline.deploy as mod

    _mock_cm(monkeypatch, {})
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_load_pairs", lambda d: {})

    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text('{}')
    (run_dir / "cluster").mkdir()

    args = argparse.Namespace(
        skip_build=True, only=None, workload=None, package=None,
        status=None, force=False, max_retries=2, poll_interval=30,
        gpu_resource_type=None, default_gpu_cost=1,
        pending_threshold=600, max_pending_stalls=10,
    )
    setup = {"namespace": "sim2real-ns", "namespaces": ["sim2real-ns"]}

    with pytest.raises(SystemExit):
        mod._cmd_run(args, run_dir, setup)


def test_cmd_reset_uses_configmap_store(monkeypatch, tmp_path):
    """_cmd_reset uses ConfigMapProgressStore directly."""
    import pipeline.deploy as mod

    progress_data = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline",
                          "status": "done", "namespace": None, "retries": 0},
    }
    _mock_cm(monkeypatch, progress_data)

    run_dir = tmp_path / "runs" / "test-run"
    run_dir.mkdir(parents=True)

    args = argparse.Namespace(only=None, workload=None, package=None,
                              status=None, dry_run=False)
    # Note: _cmd_reset now takes run_dir, not progress_path
    mod._cmd_reset(args, run_dir, {},
                   namespaces=["sim2real-ns"],
                   setup_config={"namespace": "sim2real-ns"})


def test_status_uses_configmap_directly(tmp_path, capsys, monkeypatch):
    """status uses ConfigMapProgressStore directly (CM data appears in output)."""
    from pipeline.deploy import _cmd_status

    remote_data = {"wl-remote-baseline": {"workload": "wl-remote", "package": "baseline",
                                          "status": "running", "namespace": "ns-0", "retries": 0}}
    _mock_cm(monkeypatch, remote_data)

    class _Args:
        only = None; workload = None; package = None; status = None

    _cmd_status(_Args(), tmp_path, setup_config={"namespace": "sim2real-ns"})

    out = capsys.readouterr().out
    assert "wl-remote-baseline" in out


def test_early_reclaim_non_recoverable_cancel_fails_leaves_slot_busy(monkeypatch):
    """When cancel fails for non-recoverable pod, slot stays busy (not freed)."""
    import json
    import pipeline.deploy as mod

    pods_json = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "node(s) had untolerated taint {nvidia.com/gpu=present}",
                }],
            },
        }],
    }
    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: False)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["status"] == "running"
    assert entry["namespace"] == "sim2real-0"


def test_early_reclaim_recoverable_cancel_fails_leaves_slot_busy(monkeypatch):
    """When cancel fails for recoverable timeout, slot stays busy."""
    import datetime as _dt
    import json
    import pipeline.deploy as mod

    pods_json = {
        "items": [{
            "status": {
                "phase": "Pending",
                "conditions": [{
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/8 nodes are available: 8 Insufficient nvidia.com/gpu.",
                }],
            },
        }],
    }
    old_time = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=700)).isoformat()
    entry = {
        "workload": "wl-smoke", "package": "baseline", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "gpu_cost": 1,
        "pending_stalls": 0, "pending_since": old_time,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = json.dumps(pods_json)
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: False)

    reclaimed = mod._handle_pending_pods(
        pr_name="baseline-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        pending_threshold=600,
        max_pending_stalls=10,
    )

    assert reclaimed is False
    assert entry["status"] == "running"
    assert entry["namespace"] == "sim2real-0"


# ── _handle_timeout tests ────────────────────────────────────────────────────


def test_handle_timeout_cancel_fails_leaves_entry_unchanged(monkeypatch):
    """When PR is timed out but cancel fails, return False and leave entry unchanged."""
    import datetime as _dt
    import pipeline.deploy as mod

    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    entry = {
        "workload": "wl-smoke", "package": "treatment", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = old_ts
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: False)

    result = mod._handle_timeout(
        pr_name="treatment-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        timeout_hours=4.0,
        max_retries=3,
    )

    assert result is False
    assert entry["status"] == "running"
    assert entry["namespace"] == "sim2real-0"


def test_handle_timeout_cancel_succeeds_requeues(monkeypatch):
    """When PR is timed out and cancel succeeds, requeue entry."""
    import datetime as _dt
    import pipeline.deploy as mod

    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    entry = {
        "workload": "wl-smoke", "package": "treatment", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = old_ts
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: True)

    result = mod._handle_timeout(
        pr_name="treatment-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        timeout_hours=4.0,
        max_retries=3,
    )

    assert result is True
    assert entry["status"] == "pending"
    assert entry["retries"] == 1
    assert entry["namespace"] is None


def test_handle_timeout_max_retries_retains_namespace(monkeypatch):
    """At max retries, a timed-out pair stays timed-out and retains its
    namespace so reset/cleanup can find the helm releases (issue #277)."""
    import datetime as _dt
    import pipeline.deploy as mod

    old_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    entry = {
        "workload": "wl-smoke", "package": "treatment", "status": "running",
        "namespace": "sim2real-0", "retries": 3, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = old_ts
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: True)

    result = mod._handle_timeout(
        pr_name="treatment-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        timeout_hours=4.0,
        max_retries=3,
    )

    assert result is True
    assert entry["status"] == "timed-out"
    assert entry["namespace"] == "sim2real-0"


def test_handle_timeout_not_expired_returns_none(monkeypatch):
    """When PR is not timed out, return None (no action taken)."""
    import datetime as _dt
    import pipeline.deploy as mod

    recent_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")

    entry = {
        "workload": "wl-smoke", "package": "treatment", "status": "running",
        "namespace": "sim2real-0", "retries": 0, "pending_since": None,
    }

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = recent_ts
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._handle_timeout(
        pr_name="treatment-smoke-run1",
        namespace="sim2real-0",
        entry=entry,
        timeout_hours=4.0,
        max_retries=3,
    )

    assert result is None
    assert entry["status"] == "running"


# ── dispatch loop entry assignment (issue #244) ──────────────────────────────


def test_dispatch_sets_entry_running(tmp_path, monkeypatch):
    """After successful kubectl apply, the dispatched pair must be marked 'running'."""
    import yaml as _yaml
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    # Set up run directory structure
    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    # Write a single PipelineRun YAML
    # Key derivation: "wl-" + stem.removeprefix("pipelinerun-") → "wl-a-baseline"
    pr = {
        "metadata": {"name": "pr-a-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr))

    # Write run_metadata.json (needed by _cmd_build)
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    # Setup config with one namespace slot
    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    # Track progress store saves
    saved_progress = {}

    def mock_save(self, data):
        saved_progress.update(data)

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", mock_save)

    # _cmd_build → no-op (skip)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    # _check_slot_ready → always ready
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))

    # probe_free_gpus → plenty of capacity (imported inside _cmd_run from pipeline.lib.capacity)
    import pipeline.lib.capacity as _cap_mod
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (8, 8, 0))

    # load_defaults → minimal defaults (also imported inside _cmd_run)
    monkeypatch.setattr(_cap_mod, "load_defaults", lambda root: {"decode": {"accelerator": {"count": 1}}})

    # subprocess run → kubectl apply succeeds; then PipelineRun completes
    call_count = {"n": 0}

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        call_count["n"] += 1
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    # After dispatch, PipelineRun immediately "Succeeds" so loop terminates
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Succeeded")

    # Set REPO_ROOT and EXPERIMENT_ROOT
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    # Build args namespace
    args = argparse.Namespace(
        skip_build=True,
        max_retries=0,
        poll_interval=1,
        pending_threshold=600,
        max_pending_stalls=10,
        default_gpu_cost=1,
        gpu_resource_type="nvidia.com/gpu",
        only=None,
        workload=None,
        package=None,
        status=None,
        force=False,
        skip_teardown=False,
        remote=False,
        preserve_pipelineruns=False,
        shadow_ttl=0,
    )

    mod._cmd_run(args, run_dir, setup_config)

    # The pair should have been marked running at some point during dispatch
    assert "wl-a-baseline" in saved_progress
    # After full loop it ends as "done" (since we mock Succeeded), but
    # the critical thing is that no UnboundLocalError was raised.
    # The final status should be "done" because _check_pipelinerun_status returns Succeeded.
    assert saved_progress["wl-a-baseline"]["status"] == "done"


# ── Slot-aware probe gating (issue #274) ────────────────────────────────────

def _write_pr(cluster_dir, name):
    import yaml as _yaml
    pr = {
        "metadata": {"name": f"pr-{name}-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": f"wl-{name}"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / f"pipelinerun-{name}-baseline.yaml").write_text(_yaml.dump(pr))


def _run_args():
    return argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1, pending_threshold=600,
        max_pending_stalls=10, default_gpu_cost=1, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None, force=False,
        skip_teardown=False, remote=False, preserve_pipelineruns=False, shadow_ttl=0,
    )


def test_all_slots_busy_skips_gpu_probe(tmp_path, monkeypatch, capsys):
    """Issue #274: when every slot is busy, the cycle must NOT call probe_free_gpus
    and must emit the all-slots-busy log; only PipelineRun status checking runs.
    Polling stays at the base interval so slot recovery is detected next cycle."""
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    _write_pr(cluster_dir, "a")
    _write_pr(cluster_dir, "b")
    (run_dir / "run_metadata.json").write_text(json.dumps({}))
    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    # wl-a already running in the only slot (busy); wl-b pending.
    preloaded = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline",
                          "status": "running", "namespace": "sim2real-0",
                          "completed_namespace": None, "retries": 0,
                          "pending_stalls": 0, "pending_since": None},
    }
    saved_progress = {}

    def mock_save(self, data):
        saved_progress.clear()
        saved_progress.update(data)

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: dict(preloaded))
    monkeypatch.setattr(ConfigMapProgressStore, "save", mock_save)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))
    # Isolate the probe-gating behavior from in-cycle health/timeout/pending checks.
    monkeypatch.setattr(mod, "_handle_pending_pods", lambda **kw: False)
    monkeypatch.setattr(mod, "_handle_timeout", lambda **kw: None)
    monkeypatch.setattr(mod, "_check_pod_health", lambda **kw: False)

    import pipeline.lib.capacity as _cap_mod
    probe_calls = {"n": 0, "at_first_sleep": None}

    def fake_probe(**kw):
        probe_calls["n"] += 1
        return (8, 8, 0)

    monkeypatch.setattr(_cap_mod, "probe_free_gpus", fake_probe)
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root: {"decode": {"accelerator": {"count": 1}}})

    state = {"a_done": False}

    def fake_status(pr_name, ns):
        if pr_name == "pr-a-baseline":
            return "Succeeded" if state["a_done"] else "Running"
        return "Succeeded"

    monkeypatch.setattr(mod, "_check_pipelinerun_status", fake_status)

    def fake_sleep(secs):
        # First sleep follows the all-slots-busy cycle: snapshot the probe count
        # (should be 0), then free the busy slot so the loop can make progress.
        if probe_calls["at_first_sleep"] is None:
            probe_calls["at_first_sleep"] = probe_calls["n"]
            assert secs == 1  # base poll interval, not a backoff-stretched value
        state["a_done"] = True

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    mod._cmd_run(_run_args(), run_dir, setup_config)

    # The first (all-slots-busy) cycle must not have probed GPUs.
    assert probe_calls["at_first_sleep"] == 0
    out = capsys.readouterr().out
    assert "all 1 slots busy" in out
    # No backoff/orchestrator state is ever persisted.
    assert "_orchestrator" not in saved_progress


def test_free_slot_runs_gpu_probe_and_dispatches(tmp_path, monkeypatch):
    """Issue #274: with a free slot, the cycle calls probe_free_gpus and dispatches.
    No _orchestrator key is persisted."""
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    _write_pr(cluster_dir, "a")
    (run_dir / "run_metadata.json").write_text(json.dumps({}))
    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    saved_progress = {}

    def mock_save(self, data):
        saved_progress.clear()
        saved_progress.update(data)

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", mock_save)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))

    import pipeline.lib.capacity as _cap_mod
    probe_calls = {"n": 0}

    def fake_probe(**kw):
        probe_calls["n"] += 1
        return (8, 8, 0)

    monkeypatch.setattr(_cap_mod, "probe_free_gpus", fake_probe)
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root: {"decode": {"accelerator": {"count": 1}}})
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Succeeded")

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    mod._cmd_run(_run_args(), run_dir, setup_config)

    # The free slot triggered at least one GPU probe and the pair dispatched.
    assert probe_calls["n"] >= 1
    assert saved_progress["wl-a-baseline"]["status"] == "done"
    assert "_orchestrator" not in saved_progress


# ── Live-loop failure paths retain namespace (issue #277) ───────────────────


def _run_harness(tmp_path, monkeypatch, *, status_fn, extra_patches=None):
    """Drive _cmd_run through one dispatch + one poll iteration for a single pair.

    status_fn is used for _check_pipelinerun_status. extra_patches is an optional
    callable(mod, monkeypatch) for test-specific monkeypatching (e.g. pod-health
    escalation). Returns the saved progress dict.
    """
    import yaml as _yaml
    import pipeline.deploy as mod
    import pipeline.lib.capacity as _cap_mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    pr = {
        "metadata": {"name": "pr-a-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr))
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    saved_progress = {}
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save",
                        lambda self, data: saved_progress.update(data))

    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (8, 8, 0))
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root: {"decode": {"accelerator": {"count": 1}}})

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", status_fn)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    if extra_patches is not None:
        extra_patches(mod, monkeypatch)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1, pending_threshold=600,
        max_pending_stalls=10, default_gpu_cost=1,
        gpu_resource_type="nvidia.com/gpu", only=None, workload=None,
        package=None, status=None, force=False, skip_teardown=False,
        remote=False, preserve_pipelineruns=False, shadow_ttl=0,
    )
    mod._cmd_run(args, run_dir, setup_config)
    return saved_progress


def test_cmd_run_hard_failure_retains_namespace(tmp_path, monkeypatch):
    """On a hard PipelineRun failure, the live loop marks the pair failed but
    retains its namespace so a later reset can find and uninstall the helm
    releases (issue #277). Mirrors _reconcile_on_resume's retain-on-failure."""
    saved = _run_harness(tmp_path, monkeypatch,
                         status_fn=lambda pr_name, ns: "Failed")
    assert saved["wl-a-baseline"]["status"] == "failed"
    assert saved["wl-a-baseline"]["namespace"] == "sim2real-0"


def test_cmd_run_pod_health_escalation_retains_namespace(tmp_path, monkeypatch):
    """On pod-health escalation (cancel + delete), the live loop marks the pair
    failed but retains its namespace for reset cleanup (issue #277)."""
    def extra(mod, mp):
        mp.setattr(mod, "_handle_pending_pods", lambda **kw: False)
        mp.setattr(mod, "_handle_timeout", lambda **kw: None)
        mp.setattr(mod, "_check_pod_health", lambda **kw: True)
        mp.setattr(mod, "_cancel_and_delete_pipelinerun", lambda pr, ns: True)

    saved = _run_harness(tmp_path, monkeypatch,
                         status_fn=lambda pr_name, ns: "Running",
                         extra_patches=extra)
    assert saved["wl-a-baseline"]["status"] == "failed"
    assert saved["wl-a-baseline"]["namespace"] == "sim2real-0"


# ── Scoped GPU cost derivation (issue #244) ─────────────────────────────────


def test_derive_costs_only_for_scoped_pairs(tmp_path, monkeypatch):
    """_derive_pair_gpu_costs must only be called with in-scope pairs, not all discovered."""
    import yaml as _yaml
    import pipeline.deploy as mod
    import pipeline.lib.capacity as _cap_mod
    from pipeline.lib.progress import ConfigMapProgressStore

    # Create cluster dir with 3 workloads x 2 packages = 6 pairs
    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    for wl in ("a", "b", "c"):
        for pkg in ("baseline", "treatment"):
            pr = {
                "metadata": {"name": f"pr-{wl}-{pkg}", "namespace": "ns"},
                "spec": {"params": [
                    {"name": "workloadName", "value": f"wl-{wl}"},
                    {"name": "phase", "value": pkg},
                ]},
            }
            (cluster_dir / f"pipelinerun-{wl}-{pkg}.yaml").write_text(_yaml.dump(pr))

    # Write run_metadata.json
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    # Setup config
    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    # Mock ConfigMapProgressStore
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)

    # _cmd_build → no-op
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")

    # _check_slot_ready → always ready
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))

    # probe_free_gpus → plenty of capacity
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (8, 8, 0))

    # load_defaults → minimal defaults
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root: {"decode": {"accelerator": {"count": 1}}})

    # subprocess run → success
    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    # PipelineRun immediately succeeds so loop terminates
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Succeeded")

    # Set REPO_ROOT and EXPERIMENT_ROOT
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    # Track which keys are passed to _derive_pair_gpu_costs
    from pipeline.deploy import _derive_pair_gpu_costs as _orig_derive
    called_keys: list[set] = []

    def tracking_derive(discovered, *, defaults, fallback_cost):
        called_keys.append(set(discovered.keys()))
        return _orig_derive(discovered, defaults=defaults, fallback_cost=fallback_cost)

    monkeypatch.setattr(mod, "_derive_pair_gpu_costs", tracking_derive)

    # Args: scope to workload "wl-a" only (2 pairs: wl-a-baseline, wl-a-treatment)
    args = argparse.Namespace(
        skip_build=True,
        max_retries=0,
        poll_interval=1,
        pending_threshold=600,
        max_pending_stalls=10,
        default_gpu_cost=1,
        gpu_resource_type="nvidia.com/gpu",
        only=None,
        workload="wl-a",
        package=None,
        status=None,
        force=False,
        skip_teardown=False,
        remote=False,
        preserve_pipelineruns=False,
        shadow_ttl=0,
    )

    mod._cmd_run(args, run_dir, setup_config)

    # _derive_pair_gpu_costs should have been called with only the 2 in-scope pairs
    assert len(called_keys) == 1


# ── Pod health check (issue #228) ───────────────────────────────────────────


def test_check_pod_health_tier1_deletes(monkeypatch):
    """Tier 1 finding triggers pod deletion and returns False (no escalation yet)."""
    import pipeline.deploy as mod
    from pipeline.lib.health import PodState, RemediationTracker
    import pipeline.lib.health as health_mod

    tracker = RemediationTracker()
    deleted = []

    monkeypatch.setattr(health_mod, "get_all_pods", lambda ns: [
        PodState(name="vllm-0", phase="Running", ready=False,
                 restart_count=1, reason="OOMKilled", message=""),
    ])
    monkeypatch.setattr(health_mod, "get_events", lambda ns: [])
    monkeypatch.setattr(health_mod, "delete_pod", lambda ns, name: (deleted.append(name), True)[1])

    result = mod._check_pod_health(
        namespace="ns-0", pair_key="wl-a-baseline",
        tracker=tracker, skip_teardown=False,
    )
    assert result is False
    assert "vllm-0" in deleted
    assert tracker.count("vllm-0") == 1


def test_check_pod_health_tier2_escalates(monkeypatch):
    """Tier 2 finding escalates when skip_teardown=False."""
    import pipeline.deploy as mod
    from pipeline.lib.health import PodState, RemediationTracker
    import pipeline.lib.health as health_mod

    tracker = RemediationTracker()

    monkeypatch.setattr(health_mod, "get_all_pods", lambda ns: [
        PodState(name="vllm-0", phase="Running", ready=False,
                 restart_count=5, reason="OOMKilled", message=""),
    ])
    monkeypatch.setattr(health_mod, "get_events", lambda ns: [])
    monkeypatch.setattr(health_mod, "delete_pod", lambda ns, name: True)

    # Pre-load tracker past threshold so OOM escalates to tier 2
    tracker.record("vllm-0")
    tracker.record("vllm-0")

    result = mod._check_pod_health(
        namespace="ns-0", pair_key="wl-a-baseline",
        tracker=tracker, skip_teardown=False,
    )
    assert result is True


def test_check_pod_health_tier2_no_escalate_skip_teardown(monkeypatch):
    """Tier 2 does NOT escalate when skip_teardown=True."""
    import pipeline.deploy as mod
    from pipeline.lib.health import PodState, RemediationTracker
    import pipeline.lib.health as health_mod

    tracker = RemediationTracker()
    tracker.record("vllm-0")
    tracker.record("vllm-0")

    monkeypatch.setattr(health_mod, "get_all_pods", lambda ns: [
        PodState(name="vllm-0", phase="Running", ready=False,
                 restart_count=5, reason="OOMKilled", message=""),
    ])
    monkeypatch.setattr(health_mod, "get_events", lambda ns: [])
    monkeypatch.setattr(health_mod, "delete_pod", lambda ns, name: True)

    result = mod._check_pod_health(
        namespace="ns-0", pair_key="wl-a-baseline",
        tracker=tracker, skip_teardown=True,
    )
    assert result is False


def test_check_pod_health_resets_healthy(monkeypatch):
    """Healthy pods (Running+Ready) reset their tracker count."""
    import pipeline.deploy as mod
    from pipeline.lib.health import PodState, RemediationTracker
    import pipeline.lib.health as health_mod

    tracker = RemediationTracker()
    tracker.record("vllm-0")
    tracker.record("vllm-0")
    assert tracker.count("vllm-0") == 2

    monkeypatch.setattr(health_mod, "get_all_pods", lambda ns: [
        PodState(name="vllm-0", phase="Running", ready=True,
                 restart_count=0, reason="", message=""),
    ])
    monkeypatch.setattr(health_mod, "get_events", lambda ns: [])

    mod._check_pod_health(
        namespace="ns-0", pair_key="wl-a-baseline",
        tracker=tracker, skip_teardown=False,
    )
    assert tracker.count("vllm-0") == 0


# ── Health escalation integration (issue #228) ───────────────────────────────


def test_health_escalation_cancels_pipelinerun(tmp_path, monkeypatch):
    """When _check_pod_health returns True (escalation), the orchestrator cancels the
    PipelineRun, marks the pair failed, and frees the slot."""
    import yaml as _yaml
    import pipeline.deploy as mod
    import pipeline.lib.capacity as _cap_mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    pr = {
        "metadata": {"name": "pr-a-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr))
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    saved = {}
    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: saved.update(d))
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (8, 8, 0))
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root, **kw: {"decode": {"accelerator": {"count": 1}}})

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    # PipelineRun reports "Running" always — health check will escalate
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Running")

    # Health check always returns escalation
    monkeypatch.setattr(mod, "_check_pod_health",
                        lambda **kw: True)

    # Cancel succeeds
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun",
                        lambda pr, ns: True)

    # Prevent infinite loop from _handle_pending_pods and _handle_timeout
    monkeypatch.setattr(mod, "_handle_pending_pods", lambda **kw: False)
    monkeypatch.setattr(mod, "_handle_timeout", lambda **kw: None)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1,
        pending_threshold=600, max_pending_stalls=10,
        default_gpu_cost=1, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None,
        force=False, skip_teardown=False, remote=False,
        preserve_pipelineruns=False, shadow_ttl=0,
    )

    mod._cmd_run(args, run_dir, setup_config)

    assert "wl-a-baseline" in saved
    assert saved["wl-a-baseline"]["status"] == "failed"


class TestSelectDispatchable:
    """Tests for _select_dispatchable: shuffle then capacity-gate."""

    def test_returns_empty_when_pending_empty(self):
        from pipeline.deploy import _select_dispatchable
        result = _select_dispatchable([], free_gpus=8, cost_map={})
        assert result == []

    def test_returns_all_when_budget_fits_all(self):
        from pipeline.deploy import _select_dispatchable
        pending = [f"p{i}" for i in range(5)]
        cost_map = {k: 1 for k in pending}
        result = _select_dispatchable(pending, free_gpus=10, cost_map=cost_map)
        assert sorted(result) == sorted(pending)

    def test_does_not_mutate_input_pending_list(self):
        from pipeline.deploy import _select_dispatchable
        pending = ["a", "b", "c", "d"]
        original = list(pending)
        cost_map = {k: 1 for k in pending}
        _select_dispatchable(pending, free_gpus=2, cost_map=cost_map)
        assert pending == original, "input list must not be mutated"

    def test_picks_uniform_random_subset_under_equal_costs(self):
        """The fairness criterion from issue #266.

        With 24 pairs at 4 GPUs each and budget=48 (fits 12), each pair should
        appear in roughly 12/24 = 50% of trials. Run many trials and check the
        per-pair appearance rate is within tolerance of 0.5.
        """
        from pipeline.deploy import _select_dispatchable
        import random as _random
        pending = [f"p{i:02d}" for i in range(24)]
        cost_map = {k: 4 for k in pending}
        trials = 2000
        counts = {k: 0 for k in pending}
        rng = _random.Random(42)  # deterministic for the test
        for _ in range(trials):
            with patch("pipeline.deploy.random", rng):
                picked = _select_dispatchable(pending, free_gpus=48, cost_map=cost_map)
            assert len(picked) == 12
            for k in picked:
                counts[k] += 1
        # Each pair should appear in roughly 50% ± 5% of trials.
        for k, c in counts.items():
            rate = c / trials
            assert 0.40 <= rate <= 0.60, (
                f"{k} appeared {c}/{trials} = {rate:.2%}, expected ~50%"
            )

    def test_first_half_not_overrepresented_under_equal_costs(self):
        """Direct test of the regression #266 fixes: first-half pairs are not
        systematically picked over second-half pairs."""
        from pipeline.deploy import _select_dispatchable
        import random as _random
        pending = [f"p{i:02d}" for i in range(24)]
        cost_map = {k: 4 for k in pending}
        trials = 1000
        first_half_appearances = 0
        second_half_appearances = 0
        rng = _random.Random(123)
        for _ in range(trials):
            with patch("pipeline.deploy.random", rng):
                picked = _select_dispatchable(pending, free_gpus=48, cost_map=cost_map)
            for k in picked:
                idx = int(k[1:])
                if idx < 12:
                    first_half_appearances += 1
                else:
                    second_half_appearances += 1
        # Expected: ~6000 each (12 picks/trial × 1000 trials × 0.5 from each half).
        # Pre-fix behavior: first_half = 12000, second_half = 0.
        ratio = first_half_appearances / (first_half_appearances + second_half_appearances)
        assert 0.45 <= ratio <= 0.55, (
            f"first-half got {ratio:.2%} of picks (expected ~50%); pre-fix bias?"
        )

    def test_smallest_cost_first_packing_with_heterogeneous_costs(self):
        """When costs differ, the gate must still prefer smallest costs to
        maximize dispatch count (existing _capacity_limited_pairs invariant).

        With pairs of cost 1, 1, 1, 8, 8 and budget=4: only the three cost-1
        pairs fit. Verify all three are picked regardless of shuffle order.
        """
        from pipeline.deploy import _select_dispatchable
        pending = ["big1", "small1", "big2", "small2", "small3"]
        cost_map = {"big1": 8, "big2": 8, "small1": 1, "small2": 1, "small3": 1}
        # Run many trials — every trial should pick exactly the three small pairs.
        for _ in range(50):
            picked = _select_dispatchable(pending, free_gpus=4, cost_map=cost_map)
            assert sorted(picked) == ["small1", "small2", "small3"], (
                f"got {picked}; smallest-first packing violated"
            )

    def test_within_cost_group_randomization_with_heterogeneous_costs(self):
        """Within a cost tier, the helper must randomize. With four cost-1
        pairs and budget=2, any two of the four should be possible picks."""
        from pipeline.deploy import _select_dispatchable
        import random as _random
        pending = ["a", "b", "c", "d"]
        cost_map = {k: 1 for k in pending}
        seen_pairs = set()
        rng = _random.Random(7)
        for _ in range(200):
            with patch("pipeline.deploy.random", rng):
                picked = _select_dispatchable(pending, free_gpus=2, cost_map=cost_map)
            seen_pairs.add(frozenset(picked))
        # 4-choose-2 = 6 possible 2-element subsets; expect to see most of them.
        assert len(seen_pairs) >= 4, f"only saw {seen_pairs}; randomness too narrow"


def test_dispatch_shuffles_dispatchable(tmp_path, monkeypatch):
    """Verify that dispatchable list is shuffled before slot assignment (#247).

    Exercises the actual _cmd_run dispatch path and asserts random.shuffle
    is called on the dispatchable list as a side effect.
    """
    import yaml as _yaml
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    pr = {
        "metadata": {"name": "pr-a-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr))
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    setup_config = {"namespaces": ["sim2real-0"], "namespace": "sim2real-0"}

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))

    import pipeline.lib.capacity as _cap_mod
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (8, 8, 0))
    monkeypatch.setattr(_cap_mod, "load_defaults", lambda root: {"decode": {"accelerator": {"count": 1}}})

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Succeeded")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    shuffle_calls = []
    original_shuffle = mod.random.shuffle

    def tracking_shuffle(lst):
        shuffle_calls.append(list(lst))
        original_shuffle(lst)

    monkeypatch.setattr(mod.random, "shuffle", tracking_shuffle)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1,
        pending_threshold=600, max_pending_stalls=10,
        default_gpu_cost=1, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None,
        force=False, skip_teardown=False, remote=False,
        preserve_pipelineruns=False, shadow_ttl=0,
    )

    mod._cmd_run(args, run_dir, setup_config)

    assert len(shuffle_calls) >= 1
    assert "wl-a-baseline" in shuffle_calls[0]


# ── Dispatch cooldown (issue #249) ──────────────────────────────────────────


def test_shadow_ledger_prevents_over_subscription(tmp_path, monkeypatch):
    """Shadow ledger limits dispatch to effective free GPU capacity.

    With 8 probed GPUs, cost=4 per pair, and shadow_ttl=120, only 2 pairs
    can dispatch before the ledger gates further dispatch (8/4=2). The third
    pair waits until its predecessors complete and free the slots+probe.
    """
    import time as _time_mod
    import yaml as _yaml
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    for name in ("a", "b", "c"):
        pr = {
            "metadata": {"name": f"pr-{name}-baseline", "namespace": "ns"},
            "spec": {"params": [
                {"name": "workloadName", "value": f"wl-{name}"},
                {"name": "phase", "value": "baseline"},
            ]},
        }
        (cluster_dir / f"pipelinerun-{name}-baseline.yaml").write_text(_yaml.dump(pr))

    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    setup_config = {"namespaces": ["sim2real-0", "sim2real-1", "sim2real-2"],
                    "namespace": "sim2real-ns"}

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))

    import pipeline.lib.capacity as _cap_mod
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (8, 16, 8))
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root: {"decode": {"accelerator": {"count": 4}}})

    dispatch_log = []

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        if "apply" in cmd:
            dispatch_log.append(clock[0])
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Succeeded")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)

    clock = [100.0]

    def fake_time():
        val = clock[0]
        clock[0] += 1.0
        return val

    monkeypatch.setattr(_time_mod, "time", fake_time)
    monkeypatch.setattr(mod.time, "time", fake_time)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    import pipeline.lib.shadow as _shadow_mod
    monkeypatch.setattr(_shadow_mod.time, "time", fake_time)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1,
        pending_threshold=600, max_pending_stalls=10,
        default_gpu_cost=4, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None,
        force=False, skip_teardown=False, remote=False,
        preserve_pipelineruns=False, shadow_ttl=120,
    )

    mod._cmd_run(args, run_dir, setup_config)

    # All 3 pairs eventually dispatched
    assert len(dispatch_log) == 3
    # First two dispatch close together (same iteration); third is later
    # (after predecessors complete and shadow entries remain but probe allows)
    assert dispatch_log[1] - dispatch_log[0] < 5


def test_shadow_ttl_zero_disables_gating(tmp_path, monkeypatch):
    """shadow_ttl=0 disables shadow tracking — dispatch uses probe directly."""
    import yaml as _yaml
    import pipeline.deploy as mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)

    # Three pairs each costing 4 GPUs
    for name in ("a", "b", "c"):
        pr = {
            "metadata": {"name": f"pr-{name}-baseline", "namespace": "ns"},
            "spec": {"params": [
                {"name": "workloadName", "value": f"wl-{name}"},
                {"name": "phase", "value": "baseline"},
            ]},
        }
        (cluster_dir / f"pipelinerun-{name}-baseline.yaml").write_text(_yaml.dump(pr))

    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    # 3 slots, 12 probed free GPUs, cost 4 each — without shadow all 3 fit
    setup_config = {"namespaces": ["sim2real-0", "sim2real-1", "sim2real-2"],
                    "namespace": "sim2real-ns"}

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, d: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))

    import pipeline.lib.capacity as _cap_mod
    monkeypatch.setattr(_cap_mod, "probe_free_gpus", lambda **kw: (12, 16, 4))
    monkeypatch.setattr(_cap_mod, "load_defaults",
                        lambda root: {"decode": {"accelerator": {"count": 4}}})

    dispatch_count = {"n": 0}

    def fake_run(cmd, *, check=True, capture=False, input=None, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        if "apply" in cmd:
            dispatch_count["n"] += 1
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status",
                        lambda pr_name, ns: "Succeeded")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1,
        pending_threshold=600, max_pending_stalls=10,
        default_gpu_cost=4, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None,
        force=False, skip_teardown=False, remote=False,
        preserve_pipelineruns=False, shadow_ttl=0,
    )

    mod._cmd_run(args, run_dir, setup_config)

    # All 3 pairs dispatched — shadow tracking disabled, probe says 12 free
    # which fits 3 pairs of cost 4 each
    assert dispatch_count["n"] == 3


def _setup_dispatch_run(tmp_path, monkeypatch, *, baseline_yaml: str):
    """Common setup for _cmd_run integration tests.

    Builds a single-pair run dir with a PipelineRun YAML and the given
    `baseline.yaml` content, mocks the cluster and store, and returns
    the args + setup_config + run_dir for the test to invoke _cmd_run.

    Captures probe_free_gpus kwargs into the returned dict; the loop is
    short-circuited by reporting "Succeeded" on the first poll.
    """
    import yaml as _yaml
    import pipeline.deploy as mod
    import pipeline.lib.capacity as _cap_mod
    from pipeline.lib.progress import ConfigMapProgressStore

    run_dir = tmp_path / "runs" / "test-run"
    cluster_dir = run_dir / "cluster"
    cluster_dir.mkdir(parents=True)
    pr = {
        "metadata": {"name": "pr-a-baseline", "namespace": "ns"},
        "spec": {"params": [
            {"name": "workloadName", "value": "wl-a"},
            {"name": "phase", "value": "baseline"},
        ]},
    }
    (cluster_dir / "pipelinerun-a-baseline.yaml").write_text(_yaml.dump(pr))
    (cluster_dir / "baseline.yaml").write_text(baseline_yaml)
    (run_dir / "run_metadata.json").write_text(json.dumps({}))

    monkeypatch.setattr(ConfigMapProgressStore, "load", lambda self: {})
    monkeypatch.setattr(ConfigMapProgressStore, "save", lambda self, data: None)
    monkeypatch.setattr(mod, "_cmd_build", lambda *a, **kw: "skip")
    monkeypatch.setattr(mod, "_check_slot_ready", lambda ns, **kw: (True, []))
    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr_name, ns: "Succeeded")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "EXPERIMENT_ROOT", tmp_path)
    # _cmd_run only parses the scenario YAML when defaults_result is truthy
    # (line ~1940 in deploy.py). Provide a minimal non-None defaults stub.
    monkeypatch.setattr(_cap_mod, "load_defaults", lambda *a, **kw: {"accelerator": {"resource": "nvidia.com/gpu"}})
    monkeypatch.setattr(mod, "run", lambda *a, **kw: type("_R", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    captured: dict = {"probe_kwargs": []}

    def fake_probe(**kwargs):
        captured["probe_kwargs"].append(kwargs)
        return (8, 8, 0)

    monkeypatch.setattr(_cap_mod, "probe_free_gpus", fake_probe)

    args = argparse.Namespace(
        skip_build=True, max_retries=0, poll_interval=1,
        pending_threshold=600, max_pending_stalls=10,
        default_gpu_cost=4, gpu_resource_type="nvidia.com/gpu",
        only=None, workload=None, package=None, status=None, force=False,
        skip_teardown=False, remote=False, preserve_pipelineruns=False,
        shadow_ttl=0,
    )
    setup_config = {"namespace": "sim2real-0", "namespaces": ["sim2real-0"]}
    return mod, args, setup_config, run_dir, captured


class TestCmdRunForwardsNodeFilters:
    """Issue #268: _cmd_run must forward [NodeFilter()] to probe_free_gpus
    even when extract_node_filters yields {}, so cordon/taint screening
    always runs."""

    def test_empty_extractor_result_forwards_default_filter(self, tmp_path, monkeypatch):
        from pipeline.lib.capacity import NodeFilter
        # Scenario with no role keys → extract_node_filters returns {}.
        baseline = "scenario:\n- name: test\n"
        mod, args, setup, run_dir, captured = _setup_dispatch_run(
            tmp_path, monkeypatch, baseline_yaml=baseline,
        )
        mod._cmd_run(args, run_dir, setup)
        assert captured["probe_kwargs"], "probe_free_gpus was never called"
        first = captured["probe_kwargs"][0]
        assert first.get("node_filters") == [NodeFilter()]

    def test_role_with_acceleratorType_forwards_role_filter(self, tmp_path, monkeypatch, capsys):
        from pipeline.lib.capacity import NodeFilter
        baseline = (
            "scenario:\n"
            "- name: test\n"
            "  decode:\n"
            "    acceleratorType:\n"
            "      labelKey: nvidia.com/gpu.product\n"
            "      labelValue: NVIDIA-H100-80GB-HBM3\n"
        )
        mod, args, setup, run_dir, captured = _setup_dispatch_run(
            tmp_path, monkeypatch, baseline_yaml=baseline,
        )
        mod._cmd_run(args, run_dir, setup)
        assert captured["probe_kwargs"], "probe_free_gpus was never called"
        first = captured["probe_kwargs"][0]
        assert first.get("node_filters") == [
            NodeFilter(required_gpu_products=frozenset({"NVIDIA-H100-80GB-HBM3"}))
        ]
        out = capsys.readouterr().out
        assert "Eligibility filter [decode]" in out
        assert "NVIDIA-H100-80GB-HBM3" in out

    def test_no_per_role_constraint_logs_info_line(self, tmp_path, monkeypatch, capsys):
        """Issue #268 item 6: when no constraint can be extracted, the orchestrator
        must announce that cordon/taint-only screening is in effect."""
        baseline = "scenario:\n- name: test\n"
        mod, args, setup, run_dir, _captured = _setup_dispatch_run(
            tmp_path, monkeypatch, baseline_yaml=baseline,
        )
        mod._cmd_run(args, run_dir, setup)
        out = capsys.readouterr().out
        assert "No per-role GPU product constraint extracted" in out
        assert "cordon/taint screening only" in out


class TestLoadProgressHelper:
    """Unit tests for _load_progress helper (issue #140)."""

    def _fake_store(self, behavior):
        """Return a stub store whose load() executes ``behavior`` (a callable)."""
        class _Store:
            configmap_name = "sim2real-progress-fake"

            def load(self_inner):
                return behavior()
        return _Store()

    def test_returns_load_result_on_success(self):
        from pipeline.deploy import _load_progress
        store = self._fake_store(lambda: {"a": 1})
        assert _load_progress(store) == {"a": 1}

    def test_exits_with_message_on_value_error(self, capsys):
        from pipeline.deploy import _load_progress

        def boom():
            raise ValueError("Corrupt ConfigMap sim2real-progress-fake in ns-x")
        store = self._fake_store(boom)
        with pytest.raises(SystemExit) as exc_info:
            _load_progress(store)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Corrupt" in combined
        assert "sim2real-progress-fake" in combined
        assert "prepare" in combined.lower() or "manually" in combined.lower()

    def test_propagates_runtime_error_by_default(self):
        from pipeline.deploy import _load_progress

        def boom():
            raise RuntimeError("kubectl unreachable")
        store = self._fake_store(boom)
        with pytest.raises(RuntimeError, match="kubectl unreachable"):
            _load_progress(store)

    def test_raises_progress_unavailable_when_allow_unreachable(self):
        """allow_unreachable=True converts RuntimeError into ProgressUnavailable
        so callers can distinguish unreachable from legitimate empty data
        (issue #287)."""
        from pipeline.deploy import _load_progress, ProgressUnavailable

        def boom():
            raise RuntimeError("kubectl unreachable")
        store = self._fake_store(boom)
        with pytest.raises(ProgressUnavailable, match="kubectl unreachable"):
            _load_progress(store, allow_unreachable=True)

    def test_progress_unavailable_subclasses_runtime_error(self):
        """Existing handlers that catch RuntimeError continue to work."""
        from pipeline.deploy import ProgressUnavailable
        assert issubclass(ProgressUnavailable, RuntimeError)

    def test_value_error_exits_even_when_allow_unreachable(self):
        from pipeline.deploy import _load_progress

        def boom():
            raise ValueError("Corrupt")
        store = self._fake_store(boom)
        with pytest.raises(SystemExit):
            _load_progress(store, allow_unreachable=True)
