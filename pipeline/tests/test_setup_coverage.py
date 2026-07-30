"""Extended tests for pipeline/setup.py — coverage of interactive functions.

Complements test_setup_pipeline.py by covering:
- prompt() with env-var resolution and interactive fallback
- prompt_secret() with env-var resolution and env-var-name redirection
- _detect_container_runtime() with podman/docker detection
- collect_config() with various argument combinations
- step_test_push() with mocked subprocess calls
- _do_test_push() success/failure paths
- step_config_output() JSONDecodeError path
- main() end-to-end integration
"""
import json
import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest

import pipeline.setup as setup_mod
from pipeline.setup import (
    SetupConfig,
    build_parser,
    prompt,
    prompt_secret,
    _detect_container_runtime,
    collect_config,
    step_test_push,
    _do_test_push,
    step_config_output,
    main,
    which,
    run,
)


# ── prompt() ─────────────────────────────────────────────────────────


class TestPrompt:
    """Tests for the prompt() helper."""

    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.setenv("REGISTRY", "quay.io/test")
        result = prompt("registry", "Enter registry")
        assert result == "quay.io/test"

    def test_uses_explicit_env_var_name(self, monkeypatch):
        monkeypatch.setenv("MY_REG", "ghcr.io/org")
        result = prompt("registry", "Enter registry", env_var="MY_REG")
        assert result == "ghcr.io/org"

    def test_falls_back_to_interactive_input(self, monkeypatch):
        monkeypatch.delenv("REGISTRY", raising=False)
        with patch("builtins.input", return_value="user-typed"):
            result = prompt("registry", "Enter registry")
        assert result == "user-typed"

    def test_uses_default_when_input_empty(self, monkeypatch):
        monkeypatch.delenv("REGISTRY", raising=False)
        with patch("builtins.input", return_value=""):
            result = prompt("registry", "Enter registry", default="fallback")
        assert result == "fallback"

    def test_input_overrides_default(self, monkeypatch):
        monkeypatch.delenv("REGISTRY", raising=False)
        with patch("builtins.input", return_value="override"):
            result = prompt("registry", "Enter registry", default="fallback")
        assert result == "override"

    def test_env_var_takes_precedence_over_default(self, monkeypatch):
        monkeypatch.setenv("REGISTRY", "from-env")
        result = prompt("registry", "Enter registry", default="not-this")
        assert result == "from-env"


# ── prompt_secret() ──────────────────────────────────────────────────


class TestPromptSecret:
    """Tests for the prompt_secret() helper."""

    def test_returns_env_var_when_set(self, monkeypatch):
        monkeypatch.setenv("REGISTRY_TOKEN", "s3cr3t")
        result = prompt_secret("Token", env_var="REGISTRY_TOKEN")
        assert result == "s3cr3t"

    def test_prompts_interactively_when_no_env_var(self, monkeypatch):
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        with patch("getpass.getpass", return_value="typed-secret"):
            result = prompt_secret("Token", env_var="REGISTRY_TOKEN")
        assert result == "typed-secret"

    def test_resolves_env_var_name_typed_at_prompt(self, monkeypatch):
        """If user types an env-var name at the getpass prompt, resolve it."""
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.setenv("MY_TOKEN", "resolved-value")
        with patch("getpass.getpass", return_value="MY_TOKEN"):
            result = prompt_secret("Token", env_var="REGISTRY_TOKEN")
        assert result == "resolved-value"

    def test_returns_raw_if_env_var_name_not_found(self, monkeypatch):
        """If typed string looks like env var but doesn't resolve, return as-is."""
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        with patch("getpass.getpass", return_value="NONEXISTENT_VAR"):
            result = prompt_secret("Token", env_var="REGISTRY_TOKEN")
        assert result == "NONEXISTENT_VAR"

    def test_returns_empty_string_when_nothing_entered(self, monkeypatch):
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        with patch("getpass.getpass", return_value=""):
            result = prompt_secret("Token", env_var="REGISTRY_TOKEN")
        assert result == ""

    def test_no_env_var_kwarg_skips_env_lookup(self, monkeypatch):
        monkeypatch.setenv("REGISTRY_TOKEN", "should-not-use")
        with patch("getpass.getpass", return_value="direct"):
            result = prompt_secret("Token")  # no env_var=
        assert result == "direct"


# ── _detect_container_runtime() ──────────────────────────────────────


class TestDetectContainerRuntime:
    """Tests for the container runtime auto-detection."""

    def test_returns_podman_when_available(self, monkeypatch):
        monkeypatch.setattr(setup_mod, "which", lambda cmd: cmd == "podman")
        assert _detect_container_runtime() == "podman"

    def test_returns_docker_when_podman_absent(self, monkeypatch):
        monkeypatch.setattr(setup_mod, "which", lambda cmd: cmd == "docker")
        assert _detect_container_runtime() == "docker"

    def test_returns_empty_when_neither_found(self, monkeypatch):
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        assert _detect_container_runtime() == ""

    def test_prefers_podman_over_docker(self, monkeypatch):
        monkeypatch.setattr(setup_mod, "which", lambda cmd: True)
        assert _detect_container_runtime() == "podman"


# ── which() ──────────────────────────────────────────────────────────


class TestWhich:
    """Tests for the which() wrapper."""

    def test_returns_true_for_python3(self):
        assert which("python3") is True

    def test_returns_false_for_nonexistent(self):
        assert which("nonexistent_binary_xyz_12345") is False


# ── collect_config() ─────────────────────────────────────────────────


class TestCollectConfig:
    """Tests for the collect_config() function."""

    def test_uses_args_registry_directly(self, tmp_path, monkeypatch):
        """When --registry is specified, no interactive prompt is needed."""
        monkeypatch.setattr(setup_mod, "EXPERIMENT_ROOT", tmp_path)
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_IMAGE", raising=False)

        args = build_parser().parse_args([
            "--registry", "quay.io/myuser",
            "--repo-name", "myrepo",
            "--registry-user", "u",
            "--registry-token", "t",
            "--orchestrator-image", "ghcr.io/x/orch:v1",
        ])

        cfg, container_rt = collect_config(args)
        assert cfg.registry == "quay.io/myuser"
        assert cfg.repo_name == "myrepo"
        assert cfg.registry_user == "u"
        assert cfg.registry_token == "t"
        assert cfg.orchestrator_image == "ghcr.io/x/orch:v1"
        assert container_rt == ""

    def test_reads_defaults_from_existing_config(self, tmp_path, monkeypatch):
        """collect_config reads defaults from existing setup_config.json."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "setup_config.json").write_text(json.dumps({
            "registry": "existing.io/prev",
            "repo_name": "prev-repo",
            "orchestrator_image": "ghcr.io/old:v0",
        }))
        monkeypatch.setattr(setup_mod, "EXPERIMENT_ROOT", tmp_path)
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_IMAGE", raising=False)

        args = build_parser().parse_args([
            "--registry", "new.io/user",
            "--repo-name", "new-repo",
            "--registry-user", "u",
            "--registry-token", "t",
            "--orchestrator-image", "ghcr.io/new:v1",
        ])

        cfg, _ = collect_config(args)
        assert cfg.registry == "new.io/user"
        assert cfg.repo_name == "new-repo"

    def test_ghcr_auto_credentials_from_github_token(self, tmp_path, monkeypatch):
        """When registry is ghcr.io/* and GITHUB_TOKEN is set, auto-use it."""
        monkeypatch.setattr(setup_mod, "EXPERIMENT_ROOT", tmp_path)
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_IMAGE", raising=False)
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_faketoken123")

        args = build_parser().parse_args([
            "--registry", "ghcr.io/myorg",
            "--repo-name", "sched",
            "--orchestrator-image", "img:v1",
        ])

        cfg, _ = collect_config(args)
        assert cfg.registry_user == "myorg"
        assert cfg.registry_token == "ghp_faketoken123"

    def test_orchestrator_image_from_env(self, tmp_path, monkeypatch):
        """ORCHESTRATOR_IMAGE env var is used as fallback."""
        monkeypatch.setattr(setup_mod, "EXPERIMENT_ROOT", tmp_path)
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.setenv("ORCHESTRATOR_IMAGE", "env-orch:latest")

        args = build_parser().parse_args([
            "--registry", "quay.io/x",
            "--repo-name", "r",
            "--registry-user", "u",
            "--registry-token", "t",
        ])

        cfg, _ = collect_config(args)
        assert cfg.orchestrator_image == "env-orch:latest"


# ── _do_test_push() ─────────────────────────────────────────────────


class TestDoTestPush:
    """Tests for _do_test_push() with mocked subprocess calls."""

    def test_success_full_cycle(self, monkeypatch):
        """Successful pull-tag-login-push-rmi-pull-rmi cycle."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(setup_mod, "run", fake_run)
        success = _do_test_push("podman", "quay.io/u/r:test", "quay.io", "user", "token")
        assert success is True

    def test_pull_failure_returns_false(self, monkeypatch):
        """If the initial pull of busybox fails, return False."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            if "pull" in cmd and "busybox" in str(cmd):
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        monkeypatch.setattr(setup_mod, "run", fake_run)
        success = _do_test_push("podman", "quay.io/u/r:test", "quay.io", "user", "token")
        assert success is False

    def test_tag_failure_returns_false(self, monkeypatch):
        """If tagging fails, return False."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            if "tag" in cmd:
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        monkeypatch.setattr(setup_mod, "run", fake_run)
        success = _do_test_push("podman", "quay.io/u/r:test", "quay.io", "user", "token")
        assert success is False

    def test_push_failure_returns_false(self, monkeypatch):
        """If push fails, return False after cleanup."""
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            if "push" in cmd:
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        monkeypatch.setattr(setup_mod, "run", fake_run)
        success = _do_test_push("podman", "quay.io/u/r:test", "quay.io", "user", "token")
        assert success is False

    def test_pullback_failure_still_returns_true(self, monkeypatch):
        """Push succeeds but pull-back fails — still returns True (warn path)."""
        call_count = [0]
        def fake_run(cmd, **kwargs):
            result = MagicMock()
            if "pull" in cmd:
                call_count[0] += 1
                # First pull (busybox) succeeds, second (pullback) fails
                if call_count[0] == 2:
                    result.returncode = 1
                else:
                    result.returncode = 0
            else:
                result.returncode = 0
            return result

        monkeypatch.setattr(setup_mod, "run", fake_run)
        success = _do_test_push("podman", "quay.io/u/r:test", "quay.io", "user", "token")
        assert success is True

    def test_skips_login_when_no_credentials(self, monkeypatch):
        """If reg_user/reg_token are empty, login step is skipped."""
        call_log = []
        def fake_run(cmd, **kwargs):
            call_log.append(cmd)
            result = MagicMock()
            result.returncode = 0
            return result

        monkeypatch.setattr(setup_mod, "run", fake_run)
        _do_test_push("podman", "quay.io/u/r:test", "quay.io", "", "")
        login_calls = [c for c in call_log if "login" in c]
        assert login_calls == []


# ── step_test_push() ─────────────────────────────────────────────────


class TestStepTestPush:
    """Tests for step_test_push() orchestration."""

    def test_skips_when_no_registry(self, capsys):
        """If cfg.registry is empty, skip immediately."""
        cfg = SetupConfig(registry="", repo_name="r", registry_user="", registry_token="")
        step_test_push(cfg, "podman", "_test", auto_push=True)
        out = capsys.readouterr().out
        assert "skipped" in out.lower() or "no registry" in out.lower()

    def test_skips_when_no_container_runtime(self, capsys):
        """If container_rt is empty, skip."""
        cfg = SetupConfig(registry="quay.io/u", repo_name="r", registry_user="u", registry_token="t")
        step_test_push(cfg, "", "_test", auto_push=True)
        out = capsys.readouterr().out
        assert "no podman/docker" in out.lower() or "skipping" in out.lower()

    def test_auto_push_invokes_do_test_push(self, monkeypatch):
        """With auto_push=True, _do_test_push is called directly."""
        called = []
        monkeypatch.setattr(setup_mod, "_do_test_push",
                            lambda *a, **kw: (called.append(1), True)[-1])
        cfg = SetupConfig(registry="quay.io/u", repo_name="r",
                          registry_user="u", registry_token="t")
        step_test_push(cfg, "podman", "_test", auto_push=True)
        assert len(called) == 1

    def test_non_auto_skips_on_user_decline(self, monkeypatch):
        """When user declines the test push prompt, skip gracefully."""
        monkeypatch.setattr(setup_mod, "prompt", lambda *a, **kw: "s")
        called = []
        monkeypatch.setattr(setup_mod, "_do_test_push",
                            lambda *a, **kw: (called.append(1), True)[-1])
        cfg = SetupConfig(registry="quay.io/u", repo_name="r",
                          registry_user="u", registry_token="t")
        step_test_push(cfg, "podman", "_test", auto_push=False)
        assert len(called) == 0

    def test_non_interactive_exits_on_push_failure(self, monkeypatch):
        """In non-interactive mode, push failure causes sys.exit(1)."""
        monkeypatch.setattr(setup_mod, "_do_test_push", lambda *a, **kw: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        cfg = SetupConfig(registry="quay.io/u", repo_name="r",
                          registry_user="u", registry_token="t")
        with pytest.raises(SystemExit) as exc:
            step_test_push(cfg, "podman", "_test", auto_push=True)
        assert exc.value.code == 1

    def test_interactive_retry_then_success(self, monkeypatch):
        """In interactive mode, retry once then succeed."""
        attempts = [0]
        def fake_push(*a, **kw):
            attempts[0] += 1
            return attempts[0] >= 2

        monkeypatch.setattr(setup_mod, "_do_test_push", fake_push)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(setup_mod, "prompt", lambda *a, **kw: "r")
        cfg = SetupConfig(registry="quay.io/u", repo_name="r",
                          registry_user="u", registry_token="t")
        step_test_push(cfg, "podman", "_test", auto_push=True)
        assert attempts[0] == 2

    def test_interactive_skip_after_failure(self, monkeypatch):
        """In interactive mode, user can choose skip after a failure."""
        monkeypatch.setattr(setup_mod, "_do_test_push", lambda *a, **kw: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(setup_mod, "prompt", lambda *a, **kw: "s")
        cfg = SetupConfig(registry="quay.io/u", repo_name="r",
                          registry_user="u", registry_token="t")
        step_test_push(cfg, "podman", "_test", auto_push=True)

    def test_interactive_quit_after_failure(self, monkeypatch):
        """In interactive mode, user can choose quit after a failure."""
        monkeypatch.setattr(setup_mod, "_do_test_push", lambda *a, **kw: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(setup_mod, "prompt", lambda *a, **kw: "q")
        cfg = SetupConfig(registry="quay.io/u", repo_name="r",
                          registry_user="u", registry_token="t")
        with pytest.raises(SystemExit) as exc:
            step_test_push(cfg, "podman", "_test", auto_push=True)
        assert exc.value.code == 1


# ── step_config_output() JSONDecodeError path ────────────────────────


class TestStepConfigOutputEdgeCases:
    """Edge cases for step_config_output."""

    def test_handles_corrupt_json_gracefully(self, tmp_path):
        """If existing setup_config.json is corrupt, overwrite cleanly."""
        ws = tmp_path / "workspace"
        ws.mkdir(parents=True)
        (ws / "setup_config.json").write_text("{{not valid json")

        original = setup_mod.EXPERIMENT_ROOT
        setup_mod.EXPERIMENT_ROOT = tmp_path
        try:
            cfg = SetupConfig(registry="quay.io/x", repo_name="r",
                              registry_user="u", registry_token="t",
                              orchestrator_image="img:v1")
            step_config_output(cfg)
            data = json.loads((ws / "setup_config.json").read_text())
            assert data["registry"] == "quay.io/x"
            assert data["orchestrator_image"] == "img:v1"
        finally:
            setup_mod.EXPERIMENT_ROOT = original


# ── main() ───────────────────────────────────────────────────────────


class TestMain:
    """Integration tests for main() with mocked interactive prompts."""

    def test_end_to_end_with_all_flags(self, tmp_path, monkeypatch):
        """main() with all flags produces correct setup_config.json."""
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_IMAGE", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(sys, "argv", [
            "setup.py",
            "--registry", "quay.io/test",
            "--repo-name", "myrepo",
            "--registry-user", "u",
            "--registry-token", "t",
            "--orchestrator-image", "ghcr.io/x/orch:v1",
            "--experiment-root", str(tmp_path),
        ])

        result = main()
        assert result == 0

        config_path = tmp_path / "workspace" / "setup_config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["registry"] == "quay.io/test"
        assert data["repo_name"] == "myrepo"
        assert data["orchestrator_image"] == "ghcr.io/x/orch:v1"

    def test_experiment_root_defaults_to_cwd(self, tmp_path, monkeypatch):
        """Without --experiment-root, uses CWD."""
        monkeypatch.setattr(setup_mod, "which", lambda cmd: False)
        monkeypatch.delenv("REGISTRY_USER", raising=False)
        monkeypatch.delenv("REGISTRY_TOKEN", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_IMAGE", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "setup.py",
            "--registry", "quay.io/cwd-test",
            "--repo-name", "r",
            "--registry-user", "u",
            "--registry-token", "t",
            "--orchestrator-image", "img:v1",
        ])

        result = main()
        assert result == 0
        assert (tmp_path / "workspace" / "setup_config.json").exists()


# ── run() ────────────────────────────────────────────────────────────


class TestRunHelper:
    """Tests for the run() subprocess wrapper."""

    def test_run_returns_completed_process(self):
        result = run(["echo", "hello"], capture=True)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_run_raises_on_failure(self):
        with pytest.raises(subprocess.CalledProcessError):
            run(["false"], check=True)

    def test_run_no_raise_when_check_false(self):
        result = run(["false"], check=False)
        assert result.returncode != 0
