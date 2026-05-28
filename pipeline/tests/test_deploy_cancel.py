"""Tests for _cancel_and_delete_pipelinerun return value."""
import pipeline.deploy as mod


def test_cancel_returns_true_on_successful_delete(monkeypatch):
    """When the final kubectl delete succeeds, return True."""
    calls = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        calls.append(cmd)
        class _R:
            returncode = 0
            stdout = '{"status":{"conditions":[{"type":"Succeeded","reason":"Cancelled"}]}}'
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Cancelled")

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")
    assert result is True


def test_cancel_returns_true_when_pr_does_not_exist(monkeypatch):
    """When the PipelineRun doesn't exist, nothing to cancel — return True."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "not found"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")
    assert result is True


def test_cancel_returns_false_when_delete_fails(monkeypatch):
    """When the final kubectl delete fails, return False and warn."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            stdout = ""
            stderr = "connection refused"
        if "get" in cmd:
            _R.returncode = 0
        elif "delete" in cmd:
            _R.returncode = 1
        else:
            _R.returncode = 0
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Cancelled")

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")
    assert result is False


def test_cancel_returns_false_when_get_fails_not_notfound(monkeypatch):
    """When kubectl get fails with a non-NotFound error (RBAC, network), return False."""
    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        class _R:
            returncode = 1
            stdout = ""
            stderr = "error: You must be logged in to the server (Unauthorized)"
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")
    assert result is False


def test_cancel_patch_failure_still_attempts_delete(monkeypatch):
    """When cancel patch fails, function still tries delete and returns based on delete result."""
    calls = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        calls.append(cmd[:3])
        class _R:
            stdout = ""
            stderr = ""
        if "get" in cmd:
            _R.returncode = 0
        elif "patch" in cmd:
            _R.returncode = 1
            _R.stderr = "forbidden"
        elif "delete" in cmd:
            _R.returncode = 0
        else:
            _R.returncode = 0
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", lambda pr, ns: "Running")

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")
    assert result is True
    assert ["kubectl", "delete", "pipelinerun"] in calls
