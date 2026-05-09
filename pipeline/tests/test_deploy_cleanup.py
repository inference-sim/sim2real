"""Tests for deploy.py cleanup subcommand and _cleanup_pair helper."""

import json


_PROGRESS = {
    "wl-smoke-baseline":   {"workload": "wl-smoke",  "package": "baseline",  "status": "done",      "namespace": "sim2real-0", "retries": 0},
    "wl-smoke-treatment":  {"workload": "wl-smoke",  "package": "treatment", "status": "running",   "namespace": "sim2real-1", "retries": 0},
    "wl-load-baseline":    {"workload": "wl-load",   "package": "baseline",  "status": "pending",   "namespace": None,         "retries": 0},
    "wl-load-treatment":   {"workload": "wl-load",   "package": "treatment", "status": "timed-out", "namespace": "sim2real-2", "retries": 1},
    "wl-heavy-baseline":   {"workload": "wl-heavy",  "package": "baseline",  "status": "failed",    "namespace": "sim2real-0", "retries": 0},
}

_DISCOVERED = {
    "wl-smoke-baseline":  {"pr_name": "baseline-smoke-run1",  "workload": "wl-smoke", "package": "baseline"},
    "wl-smoke-treatment": {"pr_name": "treatment-smoke-run1", "workload": "wl-smoke", "package": "treatment"},
    "wl-load-baseline":   {"pr_name": "baseline-load-run1",   "workload": "wl-load",  "package": "baseline"},
    "wl-load-treatment":  {"pr_name": "treatment-load-run1",  "workload": "wl-load",  "package": "treatment"},
    "wl-heavy-baseline":  {"pr_name": "baseline-heavy-run1",  "workload": "wl-heavy", "package": "baseline"},
}


def test_cleanup_pair_failed_deletes_pr_and_helm(monkeypatch):
    """A failed pair gets its PipelineRun deleted and Helm releases uninstalled."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-heavy", "package": "baseline", "status": "failed",
             "namespace": "sim2real-0", "retries": 0}
    calls = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = "release-a\nrelease-b\n"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    mod._cleanup_pair("wl-heavy-baseline", entry, _DISCOVERED)

    assert entry["status"] == "pending"
    assert entry["namespace"] is None
    assert entry["retries"] == 0

    # Should have: kubectl delete pipelinerun, helm list, helm uninstall x2
    kubectl_deletes = [c for c in calls if c[:2] == ["kubectl", "delete"]]
    assert len(kubectl_deletes) == 1
    assert "baseline-heavy-run1" in kubectl_deletes[0]

    helm_lists = [c for c in calls if c[:2] == ["helm", "list"]]
    assert len(helm_lists) == 1

    helm_uninstalls = [c for c in calls if c[:2] == ["helm", "uninstall"]]
    assert len(helm_uninstalls) == 2


def test_cleanup_pair_running_cancels_first(monkeypatch):
    """A running pair gets cancelled before deletion."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-smoke", "package": "treatment", "status": "running",
             "namespace": "sim2real-1", "retries": 0}
    cancelled = []

    def fake_cancel(pr_name, ns):
        cancelled.append((pr_name, ns))

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", fake_cancel)
    monkeypatch.setattr(mod, "run", fake_run)

    mod._cleanup_pair("wl-smoke-treatment", entry, _DISCOVERED)

    assert cancelled == [("treatment-smoke-run1", "sim2real-1")]
    assert entry["status"] == "pending"
    assert entry["namespace"] is None


def test_cleanup_pair_none_namespace_resets_state(monkeypatch):
    """Pairs with no namespace still get reset (e.g. collect-failed)."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-load", "package": "baseline", "status": "collect-failed",
             "namespace": None, "retries": 2}

    result = mod._cleanup_pair("wl-load-baseline", entry, _DISCOVERED)
    assert result is True
    assert entry["status"] == "pending"
    assert entry["retries"] == 0


def test_cleanup_pair_kubectl_delete_failure_does_not_reset(monkeypatch):
    """When kubectl delete pipelinerun fails, state is NOT reset."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-heavy", "package": "baseline", "status": "failed",
             "namespace": "sim2real-0", "retries": 0}

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1 if "pipelinerun" in cmd else 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cleanup_pair("wl-heavy-baseline", entry, _DISCOVERED)
    assert result is False
    assert entry["status"] == "failed"
    assert entry["namespace"] == "sim2real-0"


def test_cleanup_pair_helm_list_failure_does_not_reset(monkeypatch):
    """When helm list fails, state is NOT reset — operator needs manual intervention."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-heavy", "package": "baseline", "status": "failed",
             "namespace": "sim2real-0", "retries": 0}

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1 if cmd[:2] == ["helm", "list"] else 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cleanup_pair("wl-heavy-baseline", entry, _DISCOVERED)
    assert result is False
    assert entry["status"] == "failed"
    assert entry["namespace"] == "sim2real-0"


def test_cleanup_pair_helm_uninstall_failure_does_not_reset(monkeypatch):
    """When helm uninstall fails, state is NOT reset."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-heavy", "package": "baseline", "status": "failed",
             "namespace": "sim2real-0", "retries": 0}

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1 if cmd[:2] == ["helm", "uninstall"] else 0
            stdout = "stuck-release\n" if cmd[:2] == ["helm", "list"] else ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cleanup_pair("wl-heavy-baseline", entry, _DISCOVERED)
    assert result is False
    assert entry["status"] == "failed"
    assert entry["namespace"] == "sim2real-0"


def test_cleanup_pair_missing_pr_name_warns(monkeypatch, capsys):
    """When pr_name is not in discovered, a warning is emitted."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-unknown", "package": "baseline", "status": "failed",
             "namespace": "sim2real-0", "retries": 0}

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cleanup_pair("wl-unknown-baseline", entry, {})
    assert result is True
    assert entry["status"] == "pending"
    out = capsys.readouterr().out
    assert "no PipelineRun name found" in out


def test_cmd_cleanup_continues_on_exception(tmp_path, monkeypatch, capsys):
    """One pair raising an exception does not abort cleanup of remaining pairs."""
    import pipeline.deploy as mod

    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "failed",
                          "namespace": "sim2real-0", "retries": 0},
        "wl-b-baseline": {"workload": "wl-b", "package": "baseline", "status": "failed",
                          "namespace": "sim2real-1", "retries": 0},
    }
    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(progress))

    call_count = []

    def exploding_cleanup(key, entry, disc, dry_run=False, namespaces=None):
        call_count.append(key)
        if key == "wl-a-baseline":
            raise RuntimeError("kubectl not found")
        entry["status"] = "pending"
        entry["namespace"] = None
        entry["retries"] = 0
        return True

    monkeypatch.setattr(mod, "_cleanup_pair", exploding_cleanup)

    class _Args:
        only = None; workload = None; package = None; status = None; dry_run = False

    mod._cmd_cleanup(_Args(), progress_path, _DISCOVERED)

    # Both pairs should have been attempted
    assert "wl-a-baseline" in call_count
    assert "wl-b-baseline" in call_count
    # Progress should still be saved (wl-b was cleaned)
    saved = json.loads(progress_path.read_text())
    assert saved["wl-b-baseline"]["status"] == "pending"


def test_cleanup_pair_done_deletes_pr_without_state_reset(monkeypatch):
    """Done pairs get PipelineRun deleted but stay in done state."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-smoke", "package": "baseline", "status": "done",
             "namespace": None, "retries": 0}
    calls = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cleanup_pair("wl-smoke-baseline", entry, _DISCOVERED,
                               namespaces=["sim2real-0", "sim2real-1"])

    assert result is True
    # State stays done
    assert entry["status"] == "done"
    # PipelineRun deleted across all namespace slots
    kubectl_deletes = [c for c in calls if "delete" in c and "pipelinerun" in c]
    assert len(kubectl_deletes) == 2
    assert "sim2real-0" in kubectl_deletes[0]
    assert "sim2real-1" in kubectl_deletes[1]
    # No helm operations for done pairs
    helm_calls = [c for c in calls if c[0] == "helm"]
    assert helm_calls == []


def test_cmd_cleanup_skips_pending_only(tmp_path, monkeypatch, capsys):
    """cleanup acts on all non-pending pairs, including done."""
    import pipeline.deploy as mod

    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    cleaned = []
    monkeypatch.setattr(mod, "_cleanup_pair",
                        lambda k, e, d, dry_run=False, namespaces=None: cleaned.append(k) or True)

    class _Args:
        only = None; workload = None; package = None; status = None; dry_run = False

    mod._cmd_cleanup(_Args(), progress_path, _DISCOVERED)

    # pending (wl-load-baseline) should be skipped
    assert "wl-load-baseline" not in cleaned
    # done, running, timed-out, failed should all be cleaned
    assert "wl-smoke-baseline" in cleaned
    assert "wl-smoke-treatment" in cleaned
    assert "wl-load-treatment" in cleaned
    assert "wl-heavy-baseline" in cleaned


def test_cmd_cleanup_respects_only_filter(tmp_path, monkeypatch, capsys):
    """--only scopes cleanup to a single pair."""
    import pipeline.deploy as mod

    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    cleaned = []
    monkeypatch.setattr(mod, "_cleanup_pair",
                        lambda k, e, d, dry_run=False, namespaces=None: cleaned.append(k) or True)

    class _Args:
        only = "wl-heavy-baseline"; workload = None; package = None; status = None; dry_run = False

    mod._cmd_cleanup(_Args(), progress_path, _DISCOVERED)

    assert cleaned == ["wl-heavy-baseline"]


def test_cmd_cleanup_dry_run_does_not_save(tmp_path, monkeypatch, capsys):
    """--dry-run does not mutate progress.json."""
    import pipeline.deploy as mod

    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    monkeypatch.setattr(mod, "_cleanup_pair",
                        lambda k, e, d, dry_run=False, namespaces=None: True)

    class _Args:
        only = None; workload = None; package = None; status = None; dry_run = True

    mod._cmd_cleanup(_Args(), progress_path, _DISCOVERED)

    # Progress should not be modified
    saved = json.loads(progress_path.read_text())
    assert saved == _PROGRESS


def test_cmd_cleanup_saves_progress_on_success(tmp_path, monkeypatch, capsys):
    """After cleanup, progress.json is updated with reset entries."""
    import pipeline.deploy as mod

    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    # Use real _cleanup_pair logic but mock subprocess calls
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 0
            stdout = ""
        return _R()

    def fake_cancel(pr_name, ns):
        pass

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_cancel_and_delete_pipelinerun", fake_cancel)

    class _Args:
        only = None; workload = None; package = None; status = None; dry_run = False

    mod._cmd_cleanup(_Args(), progress_path, _DISCOVERED,
                     namespaces=["sim2real-0", "sim2real-1", "sim2real-2"])

    saved = json.loads(progress_path.read_text())
    # Non-done pairs reset to pending
    assert saved["wl-smoke-treatment"]["status"] == "pending"
    assert saved["wl-load-treatment"]["status"] == "pending"
    assert saved["wl-heavy-baseline"]["status"] == "pending"
    # Done pair stays done (PipelineRun deleted but state preserved)
    assert saved["wl-smoke-baseline"]["status"] == "done"
    # Pending pair unchanged
    assert saved["wl-load-baseline"]["status"] == "pending"


def test_force_reset_calls_cleanup_for_non_pending_non_done(monkeypatch):
    """_force_reset cleans non-pending, non-done pairs via _cleanup_pair."""
    import pipeline.deploy as mod

    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "failed",
                          "namespace": "sim2real-0", "retries": 0},
        "wl-a-treatment": {"workload": "wl-a", "package": "treatment", "status": "pending",
                           "namespace": None, "retries": 0},
        "wl-b-baseline": {"workload": "wl-b", "package": "baseline", "status": "timed-out",
                          "namespace": "sim2real-1", "retries": 2},
        "wl-c-baseline": {"workload": "wl-c", "package": "baseline", "status": "done",
                          "namespace": None, "retries": 0},
    }
    discovered = {
        "wl-a-baseline": {"pr_name": "baseline-a-run1", "workload": "wl-a", "package": "baseline"},
        "wl-a-treatment": {"pr_name": "treatment-a-run1", "workload": "wl-a", "package": "treatment"},
        "wl-b-baseline": {"pr_name": "baseline-b-run1", "workload": "wl-b", "package": "baseline"},
        "wl-c-baseline": {"pr_name": "baseline-c-run1", "workload": "wl-c", "package": "baseline"},
    }

    cleaned = []

    def fake_cleanup(key, entry, disc, dry_run=False, namespaces=None):
        cleaned.append(key)
        entry["status"] = "pending"
        entry["namespace"] = None
        entry["retries"] = 0
        return True

    monkeypatch.setattr(mod, "_cleanup_pair", fake_cleanup)

    scope = set(progress.keys())
    n = mod._force_reset(progress, scope, discovered,
                         namespaces=["sim2real-0", "sim2real-1"])

    # Pending and done should be skipped
    assert "wl-a-treatment" not in cleaned
    assert "wl-c-baseline" not in cleaned
    # Failed and timed-out should be cleaned
    assert "wl-a-baseline" in cleaned
    assert "wl-b-baseline" in cleaned
    assert n == 2
    assert progress["wl-c-baseline"]["status"] == "done"


def test_force_reset_skips_count_on_cleanup_failure(monkeypatch):
    """_force_reset does not count pairs where _cleanup_pair returned False."""
    import pipeline.deploy as mod

    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "failed",
                          "namespace": "sim2real-0", "retries": 0},
    }
    discovered = {"wl-a-baseline": {"pr_name": "pr1", "workload": "wl-a", "package": "baseline"}}

    monkeypatch.setattr(mod, "_cleanup_pair", lambda *a, **kw: False)

    n = mod._force_reset(progress, {"wl-a-baseline"}, discovered)
    assert n == 0
    assert progress["wl-a-baseline"]["status"] == "failed"


def test_force_reset_continues_on_exception(monkeypatch):
    """_force_reset handles exceptions without aborting remaining pairs."""
    import pipeline.deploy as mod

    progress = {
        "wl-a-baseline": {"workload": "wl-a", "package": "baseline", "status": "failed",
                          "namespace": "sim2real-0", "retries": 0},
        "wl-b-baseline": {"workload": "wl-b", "package": "baseline", "status": "failed",
                          "namespace": "sim2real-1", "retries": 0},
    }
    discovered = {
        "wl-a-baseline": {"pr_name": "pr1", "workload": "wl-a", "package": "baseline"},
        "wl-b-baseline": {"pr_name": "pr2", "workload": "wl-b", "package": "baseline"},
    }

    call_count = []

    def sometimes_fails(key, entry, disc, dry_run=False, namespaces=None):
        call_count.append(key)
        if key == "wl-a-baseline":
            raise RuntimeError("network error")
        entry["status"] = "pending"
        entry["namespace"] = None
        entry["retries"] = 0
        return True

    monkeypatch.setattr(mod, "_cleanup_pair", sometimes_fails)

    n = mod._force_reset(progress, set(progress.keys()), discovered)

    assert len(call_count) == 2
    assert n == 1  # only wl-b succeeded
    assert progress["wl-a-baseline"]["status"] == "failed"  # unchanged
    assert progress["wl-b-baseline"]["status"] == "pending"


def test_cmd_cleanup_aborts_on_filter_mismatch(tmp_path, capsys):
    """cleanup exits with error when --only doesn't match any pair."""
    import pipeline.deploy as mod

    progress_path = tmp_path / "progress.json"
    progress_path.write_text(json.dumps(_PROGRESS))

    class _Args:
        only = "nonexistent"; workload = None; package = None; status = None; dry_run = False

    with __import__("pytest").raises(SystemExit) as exc_info:
        mod._cmd_cleanup(_Args(), progress_path, _DISCOVERED)
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "No pairs matched" in captured.out + captured.err
