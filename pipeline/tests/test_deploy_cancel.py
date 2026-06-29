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


# ─── Issue #412: wait-loop must not break on CancelledRunningFinally ───
# CancelledRunningFinally is the transient state where Tekton is running the
# `finally` block (which contains llmdbenchmark-teardown → helm uninstall).
# Breaking out of the wait loop and deleting the PipelineRun while it's in
# this state force-kills the teardown TaskRun and orphans helm releases.

def test_cancel_waits_through_cancelled_running_finally(monkeypatch):
    """Regression for #412: CancelledRunningFinally is NOT terminal.

    Wait loop must keep polling while the finally block executes, and only
    break when the reason reaches a terminal value (Cancelled here).
    """
    poll_counter = []

    # Sequence:
    #   1st call (line 415, initial check before the patch): "Running"
    #   2nd-4th calls (wait loop iterations 1-3): "CancelledRunningFinally"
    #   5th call (wait loop iteration 4): "Cancelled" → terminal, break
    sequence = [
        "Running",
        "CancelledRunningFinally",
        "CancelledRunningFinally",
        "CancelledRunningFinally",
        "Cancelled",
    ]

    seq_iter = iter(sequence)
    last_status = ["Running"]

    def fake_status(pr, ns):
        try:
            last_status[0] = next(seq_iter)
        except StopIteration:
            pass
        poll_counter.append(last_status[0])
        return last_status[0]

    cmds = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        cmds.append(list(cmd[:3]))
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", fake_status)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")

    assert result is True
    # The wait loop must have observed CancelledRunningFinally at least once
    # without breaking — that is the regression guard.
    transient_observations = [s for s in poll_counter if s == "CancelledRunningFinally"]
    assert len(transient_observations) >= 3, (
        f"Wait loop broke prematurely on a transient state; "
        f"poll history: {poll_counter}"
    )
    # And it must have eventually observed the terminal Cancelled.
    assert "Cancelled" in poll_counter, (
        f"Wait loop did not reach terminal Cancelled; poll history: {poll_counter}"
    )
    # Delete must have been issued exactly once after wait completed.
    delete_calls = [c for c in cmds if c == ["kubectl", "delete", "pipelinerun"]]
    assert len(delete_calls) == 1


def test_cancel_times_out_when_finally_never_completes(monkeypatch):
    """Last-resort safety: if the finally block never terminates within 120s
    (40 polls × 3s), the wait loop must exit, warn, and force-delete the PR.

    This preserves the existing safety property — a stuck teardown cannot pin
    a slot indefinitely. The fix only widens what counts as "still in flight",
    it does not remove the timeout escape.
    """
    poll_counter = []

    def fake_status(pr, ns):
        # First call (line 415) returns "Running" to enter the cancel branch.
        # Every subsequent call returns the transient state — finally never finishes.
        if not poll_counter:
            poll_counter.append("Running")
            return "Running"
        poll_counter.append("CancelledRunningFinally")
        return "CancelledRunningFinally"

    cmds = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        cmds.append(list(cmd[:3]))
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", fake_status)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")

    assert result is True
    # The wait loop should have run all 40 iterations (plus the initial check).
    wait_loop_polls = [s for s in poll_counter if s == "CancelledRunningFinally"]
    assert len(wait_loop_polls) == 40, (
        f"Wait loop should have iterated all 40 times; got {len(wait_loop_polls)}"
    )
    # And the force-delete must still run as the last-resort safety net.
    delete_calls = [c for c in cmds if c == ["kubectl", "delete", "pipelinerun"]]
    assert len(delete_calls) == 1


def test_cancel_breaks_on_non_cancel_terminal_reason(monkeypatch):
    """If the PR independently transitions to Failed (or any other terminal
    reason) while we're waiting, the wait loop must break promptly — we don't
    need to keep polling once the PR is done.
    """
    poll_counter = []

    sequence = ["Running", "Failed"]
    seq_iter = iter(sequence)

    def fake_status(pr, ns):
        v = next(seq_iter, "Failed")
        poll_counter.append(v)
        return v

    cmds = []

    def fake_run(cmd, *, check=True, capture=False, cwd=None):
        cmds.append(list(cmd[:3]))
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        return _R()

    monkeypatch.setattr(mod, "run", fake_run)
    monkeypatch.setattr(mod, "_check_pipelinerun_status", fake_status)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    result = mod._cancel_and_delete_pipelinerun("my-pr", "ns-0")

    assert result is True
    # Initial Running + exactly one wait-loop poll observing Failed.
    assert poll_counter == ["Running", "Failed"]
    delete_calls = [c for c in cmds if c == ["kubectl", "delete", "pipelinerun"]]
    assert len(delete_calls) == 1
