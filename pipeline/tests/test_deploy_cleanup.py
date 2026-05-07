"""Tests for deploy.py cleanup subcommand and _cleanup_pair helper."""


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


def test_cleanup_pair_skips_none_namespace(monkeypatch):
    """Pairs with no namespace are no-ops (returns False)."""
    import pipeline.deploy as mod

    entry = {"workload": "wl-load", "package": "baseline", "status": "pending",
             "namespace": None, "retries": 0}

    result = mod._cleanup_pair("wl-load-baseline", entry, _DISCOVERED)
    assert result is False
    assert entry["status"] == "pending"
