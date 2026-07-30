"""Tests for deploy.py collect path coverage gaps.

Covers:
- skip_logs + scoped workload path (workload=... AND skip_logs=True)
- resources directory copy and redact_yaml_tree call in skip_logs path
- on_workload_done callback invocations for both success and error
- _copy_workload_iterations_full error propagation
- _copy_workload_iterations_full iteration skip when up-to-date
- skip_logs ls failure with allowed_workloads + on_workload_done
- full copy unscoped on_workload_done callback wiring

Issue: sim2real#792
"""

from unittest.mock import MagicMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────


def _fake_run(stdout="", stderr="", returncode=0):
    """Build a MagicMock mimicking subprocess.CompletedProcess."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def _mock_subprocess(monkeypatch, handler):
    """Monkeypatch subprocess.run with *handler* (receives cmd, **kwargs)."""
    import subprocess
    monkeypatch.setattr(subprocess, "run", handler)


# ── _copy_workload_iterations_full ───────────────────────────────────────────


class TestCopyWorkloadIterationsFull:
    """Unit tests for deploy._copy_workload_iterations_full."""

    def test_returns_errors_when_kubectl_cp_fails(self, tmp_path, monkeypatch):
        """When kubectl cp for an iteration fails, the error is in the returned list."""
        from pipeline import deploy

        wl_dest = tmp_path / "results" / "baseline" / "wl-smoke"
        wl_dest.mkdir(parents=True)

        call_log = []

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            call_log.append(cmd_str)
            # ls iteration dirs
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="i1\n")
            # stat (mtimes) — return empty so iterations aren't skipped
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            # kubectl cp — simulate failure
            if "cp" in cmd_str:
                return _fake_run(returncode=1, stderr="network timeout")
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        errors = deploy._copy_workload_iterations_full(
            "sim2real-extract", "run-1", "baseline", "wl-smoke", "ns-0",
            wl_dest, {},
        )
        assert len(errors) > 0
        assert any("network timeout" in e for e in errors)


# ── _extract_phases_from_pvc: skip_logs + scoped workload ────────────────────


class TestExtractPhasesSkipLogsScoped:
    """Tests for the skip_logs=True + workload=<name> branch in _extract_phases_from_pvc."""

    def test_skip_logs_scoped_workload_copies_trace_files(self, tmp_path, monkeypatch):
        """skip_logs + workload=<name> copies trace files for scoped workload only."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        cp_sources = []

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            # Pod cleanup / create / wait
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            # ls iteration dirs for the specific workload
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
                return _fake_run(stdout="i1\n")
            # ls workloads (shouldn't be called because workload is scoped)
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            # stat for mtime probe
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            # kubectl cp — record source
            if "cp" in cmd_str:
                cmd_list = cmd if isinstance(cmd, list) else cmd.split()
                if len(cmd_list) >= 3:
                    cp_sources.append(cmd_list[2])
                return _fake_run()
            # size probe (du)
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="1000\t/data/test-run/baseline\n")
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        errors = deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, workload="wl-smoke")

        assert errors.get("baseline") is None
        sources_joined = " ".join(cp_sources)
        # trace files should be copied
        assert "trace_data.csv" in sources_joined
        assert "trace_header.yaml" in sources_joined
        # epp_logs should be copied
        assert "/epp_logs/" in sources_joined
        # gpu_logs should be copied
        assert "/gpu_logs/" in sources_joined
        # metrics should be copied
        assert "/metrics/" in sources_joined
        # resources should be copied
        assert "/resources/" in sources_joined

    def test_skip_logs_scoped_workload_calls_redact_yaml_tree(self, tmp_path, monkeypatch):
        """skip_logs + workload path calls redact_yaml_tree on resources dir after copy."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        redact_calls = []

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="1000\t/data/test-run/baseline\n")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        def capture_redact(path):
            redact_calls.append(path)

        monkeypatch.setattr(deploy, "redact_yaml_tree", capture_redact)

        errors = deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, workload="wl-smoke")

        assert errors.get("baseline") is None
        assert len(redact_calls) == 1
        assert "resources" in str(redact_calls[0])

    def test_skip_logs_scoped_workload_error_not_fatal(self, tmp_path, monkeypatch):
        """When kubectl cp fails for a trace file (not 'no such file'), error is recorded."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="500\t/data/test-run/baseline\n")
            # Fail all cp commands with a real error (not 'no such file')
            if "cp" in cmd_str:
                return _fake_run(returncode=1, stderr="connection refused")
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)
        monkeypatch.setattr(deploy, "redact_yaml_tree", lambda p: None)

        errors = deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, workload="wl-smoke")

        # Should have an error for the baseline phase
        assert errors.get("baseline") is not None
        assert "connection refused" in str(errors["baseline"])


# ── on_workload_done callback tests ──────────────────────────────────────────


class TestOnWorkloadDoneCallbacks:
    """Verify on_workload_done fires for both skip_logs and full_copy paths."""

    def test_skip_logs_on_workload_done_fires_on_success(self, tmp_path, monkeypatch):
        """on_workload_done fires with error=None for each successful workload in skip_logs mode."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="100\t/data/test-run/baseline\n")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)
        monkeypatch.setattr(deploy, "redact_yaml_tree", lambda p: None)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, on_workload_done=on_done)

        assert len(callback_args) == 1
        phase, wl_name, ns, error = callback_args[0]
        assert phase == "baseline"
        assert wl_name == "wl-smoke"
        assert ns == "ns-0"
        assert error is None

    def test_skip_logs_on_workload_done_fires_on_error(self, tmp_path, monkeypatch):
        """on_workload_done fires with error when kubectl cp fails in skip_logs mode."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="100\t/data/test-run/baseline\n")
            # fail all cp with a real error
            if "cp" in cmd_str:
                return _fake_run(returncode=1, stderr="broken pipe")
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)
        monkeypatch.setattr(deploy, "redact_yaml_tree", lambda p: None)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, on_workload_done=on_done)

        assert len(callback_args) == 1
        phase, wl_name, ns, error = callback_args[0]
        assert phase == "baseline"
        assert wl_name == "wl-smoke"
        assert ns == "ns-0"
        assert error is not None
        assert "broken pipe" in str(error)

    def test_full_copy_on_workload_done_fires_per_workload(self, tmp_path, monkeypatch):
        """Unscoped full-copy path calls on_workload_done for each discovered workload."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            # First-level ls: list workloads
            if "exec" in cmd_str and "ls " in cmd_str and "baseline/" in cmd_str and "wl-" not in cmd_str:
                return _fake_run(stdout="wl-alpha wl-beta\n")
            # Second-level ls: list iterations per workload
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="500\t/data/test-run/baseline\n")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=False, on_workload_done=on_done)

        # Should fire for each of the 2 discovered workloads
        assert len(callback_args) == 2
        wl_names = {args[1] for args in callback_args}
        assert "wl-alpha" in wl_names
        assert "wl-beta" in wl_names
        # All should be success (no errors)
        for _, _, _, error in callback_args:
            assert error is None

    def test_full_copy_on_workload_done_fires_with_error_on_cp_failure(self, tmp_path, monkeypatch):
        """Full-copy path calls on_workload_done with error when kubectl cp fails."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "ls " in cmd_str and "baseline/" in cmd_str and "wl-" not in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="500\t/data/test-run/baseline\n")
            # All cp commands fail
            if "cp" in cmd_str:
                return _fake_run(returncode=1, stderr="timeout")
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=False, on_workload_done=on_done)

        assert len(callback_args) == 1
        phase, wl_name, ns, error = callback_args[0]
        assert phase == "baseline"
        assert wl_name == "wl-smoke"
        assert error is not None
        assert "timeout" in str(error)


# ── skip_logs ls failure with allowed_workloads + on_workload_done ────────────


class TestSkipLogsLsFailureWithAllowedWorkloads:
    """Tests for skip_logs path when ls fails and allowed_workloads is set."""

    def test_ls_failure_fires_on_workload_done_for_all_allowed(self, tmp_path, monkeypatch):
        """When ls fails in skip_logs mode with allowed_workloads, on_workload_done
        fires with error for every workload in the allowed set."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="100\t/data/test-run/baseline\n")
            # ls workloads FAILS
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(returncode=1, stderr="pod not found")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)
        monkeypatch.setattr(deploy, "redact_yaml_tree", lambda p: None)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        allowed = {"baseline": {"wl-a", "wl-b"}}

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, allowed_workloads=allowed,
            on_workload_done=on_done)

        # on_workload_done should fire for both allowed workloads with error
        assert len(callback_args) == 2
        wl_names = {args[1] for args in callback_args}
        assert wl_names == {"wl-a", "wl-b"}
        for _, _, _, error in callback_args:
            assert error is not None

    def test_full_copy_ls_failure_fires_on_workload_done_for_all_allowed(self, tmp_path, monkeypatch):
        """When ls fails in full-copy mode with allowed_workloads, on_workload_done
        fires with error for every workload in the allowed set."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="100\t/data/test-run/baseline\n")
            # ls workloads FAILS
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(returncode=1, stderr="timeout")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        allowed = {"baseline": {"wl-x", "wl-y", "wl-z"}}

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=False, allowed_workloads=allowed,
            on_workload_done=on_done)

        # on_workload_done should fire for all 3 allowed workloads with error
        assert len(callback_args) == 3
        wl_names = {args[1] for args in callback_args}
        assert wl_names == {"wl-x", "wl-y", "wl-z"}
        for _, _, _, error in callback_args:
            assert error is not None


# ── skip_logs: iteration-level ls failure in on_workload_done ─────────────────


class TestSkipLogsIterationLsFailure:
    """Tests for skip_logs when _list_pvc_iterations fails for a specific workload."""

    def test_iteration_ls_failure_fires_on_workload_done_with_error(self, tmp_path, monkeypatch):
        """When iteration ls fails for one workload, on_workload_done fires with error
        for that workload, while other workloads succeed."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="100\t/data/test-run/baseline\n")
            # Top-level ls returns two workloads
            if "exec" in cmd_str and "ls " in cmd_str and "/baseline/" in cmd_str and "wl-" not in cmd_str:
                return _fake_run(stdout="wl-good wl-bad\n")
            # Iteration ls for wl-good succeeds
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-good/" in cmd_str:
                return _fake_run(stdout="i1\n")
            # Iteration ls for wl-bad fails
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-bad/" in cmd_str:
                return _fake_run(returncode=1, stderr="permission denied")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)
        monkeypatch.setattr(deploy, "redact_yaml_tree", lambda p: None)

        callback_args = []

        def on_done(phase, wl_name, ns, error):
            callback_args.append((phase, wl_name, ns, error))

        deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True, on_workload_done=on_done)

        assert len(callback_args) == 2
        results = {args[1]: args[3] for args in callback_args}
        # wl-good should succeed
        assert results["wl-good"] is None
        # wl-bad should have an error
        assert results["wl-bad"] is not None
        assert "permission denied" in str(results["wl-bad"]) or "failed to list" in str(results["wl-bad"])


# ── skip_logs with allowed_workloads filtering ───────────────────────────────


class TestSkipLogsAllowedWorkloadsFilter:
    """Tests for skip_logs path filtering workloads via allowed_workloads."""


# ── Size probe > 1GB behavior (skip_logs=True bypasses prompt) ───────────────


class TestSizeProbeSkipLogs:
    """Tests for size-probe behavior when total > 1GB with skip_logs enabled."""

    def test_skip_logs_bypasses_interactive_prompt_on_large_data(self, tmp_path, monkeypatch):
        """When data > 1GB and skip_logs=True, no input() prompt is issued."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        input_called = []

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
                return _fake_run(stdout="i1\n")
            if "exec" in cmd_str and "ls " in cmd_str:
                return _fake_run(stdout="wl-smoke\n")
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            # Return > 1GB size
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(stdout="2000000000\t/data/test-run/baseline\n")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)
        monkeypatch.setattr(deploy, "redact_yaml_tree", lambda p: None)

        # Mock builtins.input to detect if it's called
        import builtins

        def capture_input(prompt=""):
            input_called.append(prompt)
            return "n"  # Would abort if called

        monkeypatch.setattr(builtins, "input", capture_input)

        errors = deploy._extract_phases_from_pvc(
            ["baseline"], "test-run", "ns-0", run_dir,
            skip_logs=True)

        # input() should NOT have been called because skip_logs=True
        assert input_called == []
        # But phases should still be collected
        assert errors.get("baseline") is None


# ── _extract_phases_from_pvc: pod create failure ─────────────────────────────


class TestExtractPhasePodCreateFailure:
    """Tests for pod lifecycle error handling in _extract_phases_from_pvc."""


    def test_pod_wait_failure_raises_runtime_error(self, tmp_path, monkeypatch):
        """When kubectl wait (pod ready) fails, RuntimeError is raised."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str:
                return _fake_run()
            if "run " in cmd_str:
                return _fake_run()  # create succeeds
            if "wait" in cmd_str:
                return _fake_run(returncode=1, stderr="timed out")
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        with pytest.raises(RuntimeError, match="not ready"):
            deploy._extract_phases_from_pvc(
                ["baseline"], "test-run", "ns-0", run_dir)


# ── Multi-phase collection ───────────────────────────────────────────────────


class TestMultiPhaseCollection:
    """Tests for collecting multiple phases in a single call."""

    def test_collects_multiple_phases_independently(self, tmp_path, monkeypatch):
        """Each phase is processed independently; failure in one doesn't block others."""
        from pipeline import deploy

        run_dir = tmp_path / "workspace" / "runs" / "test-run"
        (run_dir / "cluster").mkdir(parents=True)

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
            if "delete" in cmd_str or "run " in cmd_str or "wait" in cmd_str:
                return _fake_run()
            if "exec" in cmd_str and "stat" in cmd_str:
                return _fake_run(stdout="")
            if "exec" in cmd_str and "du" in cmd_str:
                return _fake_run(
                    stdout="100\t/data/test-run/baseline\n200\t/data/test-run/treatment\n")
            # iteration ls for wl-a (contains i<N> dir pattern)
            if "exec" in cmd_str and "ls " in cmd_str and "/wl-a/" in cmd_str:
                return _fake_run(stdout="i1\n")
            # top-level ls for baseline succeeds (returns workload names)
            if "exec" in cmd_str and "ls " in cmd_str and "/baseline/" in cmd_str:
                return _fake_run(stdout="wl-a\n")
            # ls for treatment fails
            if "exec" in cmd_str and "ls " in cmd_str and "/treatment/" in cmd_str:
                return _fake_run(returncode=1, stderr="not found")
            if "cp" in cmd_str:
                return _fake_run()
            return _fake_run()

        _mock_subprocess(monkeypatch, mock_run)

        errors = deploy._extract_phases_from_pvc(
            ["baseline", "treatment"], "test-run", "ns-0", run_dir,
            skip_logs=False)

        # baseline should succeed
        assert errors.get("baseline") is None
        # treatment should have an error
        assert errors.get("treatment") is not None
        assert "failed to list" in str(errors["treatment"])
