import json
import os
import subprocess
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
CLI = REPO_ROOT / "tools" / "transfer_cli.py"


def run_test_status(stdin_text: str) -> tuple[int, dict]:
    """Run test-status command with given stdin, return (exit_code, parsed_json)."""
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    result = subprocess.run(
        [sys.executable, str(CLI), "test-status"],
        input=stdin_text, capture_output=True, text=True,
        cwd=str(REPO_ROOT), env=env,
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        output = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
    return result.returncode, output


class TestTestStatusCompilation:
    """BC-1: test-status classifies Go build errors."""

    def test_classifies_compilation_error(self):
        """BC-1: Go compiler error is classified as 'compilation'."""
        go_output = (
            "# github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\n"
            "pkg/plugins/scorer/evolved_scorer.go:42:15: undefined: scheduling.InvalidType\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1, f"Expected exit 1, got {code}: {output}"
        assert output["status"] == "error"
        assert output["error_class"] == "compilation"
        assert output["error_count"] >= 1
        assert len(output["errors"]) >= 1
        assert "evolved_scorer.go" in output["errors"][0]["file"]

    def test_classifies_multiple_compilation_errors(self):
        """BC-1: Multiple errors counted correctly."""
        go_output = (
            "pkg/plugins/scorer/foo.go:10:5: undefined: bar\n"
            "pkg/plugins/scorer/foo.go:20:5: cannot use x (type int) as type string\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "compilation"
        assert output["error_count"] == 2

    def test_classifies_import_error(self):
        """BC-1: Import errors are compilation class."""
        go_output = (
            '# github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\n'
            'pkg/plugins/scorer/evolved.go:5:2: "context" imported and not used\n'
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "compilation"


class TestTestStatusTestFailure:
    """BC-2: test-status classifies Go test failures."""

    def test_classifies_test_failure(self):
        """BC-2: Failed Go test is classified as 'test_failure'."""
        go_output = (
            "--- FAIL: TestEvolvedScorer (0.01s)\n"
            "    evolved_scorer_test.go:25: expected 0.5, got 0.0\n"
            "FAIL\n"
            "FAIL\tgithub.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\t0.015s\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "test_failure"
        assert output["error_count"] >= 1

    def test_classifies_panic_in_test(self):
        """BC-2: Panic during test is classified as 'test_failure'."""
        go_output = (
            "--- FAIL: TestEvolvedScorer (0.00s)\n"
            "panic: runtime error: index out of range [recovered]\n"
            "FAIL\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "test_failure"


class TestTestStatusInfrastructure:
    """BC-3: test-status classifies infrastructure errors."""

    def test_classifies_module_error(self):
        """BC-3: Module resolution failure is 'infrastructure'."""
        go_output = (
            "go: github.com/example/missing@v1.0.0: reading "
            "https://proxy.golang.org/github.com/example/missing: 410 Gone\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_timeout(self):
        """BC-3: Context deadline exceeded is 'infrastructure'."""
        go_output = "context deadline exceeded\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_cannot_find_module(self):
        """BC-3: Cannot find module is 'infrastructure'."""
        go_output = "cannot find module providing package github.com/missing/pkg\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"


class TestTestStatusCleanOutput:
    """BC-4: test-status handles clean output and edge cases."""

    def test_clean_build_returns_zero(self):
        """BC-4, BC-12: No errors in input produces exit 0."""
        go_output = "ok  \tgithub.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\t0.015s\n"
        code, output = run_test_status(go_output)
        assert code == 0
        assert output["status"] == "ok"
        assert output["error_class"] == "none"
        assert output["error_count"] == 0
        assert output["errors"] == []

    def test_empty_input_returns_zero(self):
        """BC-4: Empty stdin is not an error."""
        code, output = run_test_status("")
        assert code == 0
        assert output["status"] == "ok"
        assert output["error_count"] == 0

    def test_output_is_valid_json(self):
        """BC-4: Output is always valid JSON."""
        code, output = run_test_status("some random text\n")
        assert isinstance(output, dict)
        assert "status" in output
        assert "error_class" in output
        assert "error_count" in output
        assert "errors" in output


class TestTestStatusExitCode2:
    """BC-12: test-status exit code 2 for CLI infrastructure errors."""

    def test_oversized_input_returns_exit_2(self):
        """BC-4, BC-12: Input exceeding 10 MB limit produces exit code 2 with valid JSON."""
        # Generate input just over the 10 MB limit
        huge_input = "x" * (10 * 1024 * 1024 + 1)
        code, output = run_test_status(huge_input)
        assert code == 2, f"Expected exit 2, got {code}: {output}"
        assert output["status"] == "error"
        assert output["error_class"] == "none"
        assert output["error_count"] == 0
        assert "errors" not in output or output.get("errors", []) == []
