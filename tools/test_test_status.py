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
        """BC-1: Go compiler error is classified as 'compilation'; file field is exact."""
        go_output = (
            "# github.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\n"
            "pkg/plugins/scorer/evolved_scorer.go:42:15: undefined: scheduling.InvalidType\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1, f"Expected exit 1, got {code}: {output}"
        assert output["status"] == "error"
        assert output["error_class"] == "compilation"
        assert output["error_count"] == 1
        assert len(output["errors"]) == 1
        assert output["errors"][0]["file"] == "pkg/plugins/scorer/evolved_scorer.go"
        assert output["errors"][0]["line"] == 42
        assert output["errors"][0]["column"] == 15

    def test_classifies_multiple_compilation_errors(self):
        """BC-1: Multiple errors counted correctly; error_count reflects only primary class."""
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

    def test_compilation_error_without_column(self):
        """BC-1: file:line: pattern (no column) is classified as compilation; column is None."""
        go_output = "pkg/plugins/scorer/evolved.go:10: syntax error: unexpected }\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "compilation"
        assert output["errors"][0]["file"] == "pkg/plugins/scorer/evolved.go"
        assert output["errors"][0]["line"] == 10
        assert output["errors"][0]["column"] is None


class TestTestStatusTestFailure:
    """BC-2: test-status classifies Go test failures."""

    def test_classifies_test_failure(self):
        """BC-2: Failed Go test is classified as 'test_failure'; count is exact."""
        go_output = (
            "--- FAIL: TestEvolvedScorer (0.01s)\n"
            "    evolved_scorer_test.go:25: expected 0.5, got 0.0\n"
            "FAIL\n"
            "FAIL\tgithub.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer\t0.015s\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "test_failure"
        assert output["error_count"] == 1

    def test_classifies_panic_in_test(self):
        """BC-2: Panic during test is classified as 'test_failure' with error details."""
        go_output = (
            "--- FAIL: TestEvolvedScorer (0.00s)\n"
            "panic: runtime error: index out of range [recovered]\n"
            "FAIL\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "test_failure"
        assert output["error_count"] >= 1
        panic_errors = [e for e in output["errors"] if "Panic" in e["message"]]
        assert len(panic_errors) >= 1
        assert "runtime error" in panic_errors[0]["message"]


class TestTestStatusInfrastructure:
    """BC-3: test-status classifies infrastructure errors."""

    def test_classifies_module_fetch_error(self):
        """BC-3: Module resolution failure via proxy is 'infrastructure'."""
        go_output = (
            "go: github.com/example/missing@v1.0.0: reading "
            "https://proxy.golang.org/github.com/example/missing: 410 Gone\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_module_get_connection_refused(self):
        """BC-3: Module proxy connection refused (Get format) is 'infrastructure'."""
        go_output = (
            'go: github.com/foo/bar@v1.0.0: Get "https://proxy.golang.org/foo/bar": '
            "dial tcp: connection refused\n"
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

    def test_classifies_no_required_module(self):
        """BC-3: 'no required module provides package' is 'infrastructure'."""
        go_output = "no required module provides package github.com/llm-d/missing\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_build_failed_summary(self):
        """BC-3: '[build failed]' summary line without individual errors is 'infrastructure'."""
        go_output = "FAIL\tgithub.com/llm-d/llm-d-inference-scheduler/pkg/plugins/scorer [build failed]\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_classifies_process_killed(self):
        """BC-3: OOM/SIGKILL ('signal: killed') is 'infrastructure'."""
        go_output = "signal: killed\n"
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"

    def test_infrastructure_takes_precedence_over_compilation(self):
        """BC-3: Infrastructure errors take precedence when both classes appear in input."""
        go_output = (
            "pkg/plugins/scorer/evolved.go:42:15: undefined: scheduling.InvalidType\n"
            "cannot find module providing package github.com/missing/pkg\n"
        )
        code, output = run_test_status(go_output)
        assert code == 1
        assert output["error_class"] == "infrastructure"
        # error_count reflects only infrastructure errors, not the compilation error
        assert output["error_count"] == 1
        assert all(e["class"] == "infrastructure" for e in output["errors"])


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

    def test_unrecognized_text_returns_zero_with_none_class(self):
        """BC-4: Unrecognized text produces exit 0 with error_class 'none'."""
        code, output = run_test_status("some random text\n")
        assert code == 0
        assert output["status"] == "ok"
        assert output["error_class"] == "none"
        assert output["error_count"] == 0
        assert isinstance(output, dict)
        assert "status" in output
        assert "error_class" in output
        assert "error_count" in output
        assert "errors" in output


class TestTestStatusExitCode2:
    """BC-12: test-status exit code 2 for CLI infrastructure errors."""

    def test_oversized_input_returns_exit_2(self):
        """BC-4, BC-12: Input exceeding 10 MB limit produces exit code 2 with structured errors."""
        huge_input = "x" * (10 * 1024 * 1024 + 1)
        code, output = run_test_status(huge_input)
        assert code == 2, f"Expected exit 2, got {code}: {output}"
        assert output["status"] == "error"
        assert output["error_class"] == "none"
        assert output["error_count"] == 0
        # exit-2 errors are in errors[] with class "cli_error", consistent with exit-1 schema
        assert len(output["errors"]) == 1
        assert output["errors"][0]["class"] == "cli_error"
        assert "10 MB" in output["errors"][0]["message"]
