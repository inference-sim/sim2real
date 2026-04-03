"""Tests for lib/gates.py review and human gate helpers."""
import json
from unittest.mock import patch, MagicMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lib.gates import review_artifacts, ReviewResult


def test_review_artifacts_all_complete():
    """When all 3 models say 'complete', review passes."""
    mock_responses = {
        "Azure/gpt-4o": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
        "GCP/gemini-2.5-flash": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
        "aws/claude-opus-4-6": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
    }
    with patch("lib.gates.call_models_parallel", return_value=mock_responses):
        result = review_artifacts(
            artifact_paths=[Path("/tmp/algo.json")],
            review_prompt="Review these artifacts",
            models=["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"],
        )
    assert result.passed is True
    assert len(result.verdicts) == 3


def test_review_artifacts_one_incomplete():
    """When any model says 'incomplete', review fails."""
    mock_responses = {
        "Azure/gpt-4o": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
        "GCP/gemini-2.5-flash": json.dumps({"verdict": "incomplete", "issues": ["Missing type X"], "suggestions": []}),
        "aws/claude-opus-4-6": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
    }
    with patch("lib.gates.call_models_parallel", return_value=mock_responses):
        result = review_artifacts(
            artifact_paths=[Path("/tmp/algo.json")],
            review_prompt="Review these artifacts",
            models=["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"],
        )
    assert result.passed is False
    assert "Missing type X" in result.verdicts["GCP/gemini-2.5-flash"]["issues"]


def test_review_artifacts_llm_error_is_non_vote():
    """LLM errors are non-votes — remaining successful reviews decide."""
    from lib.llm import LLMError
    mock_responses = {
        "Azure/gpt-4o": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
        "GCP/gemini-2.5-flash": LLMError("timeout"),
        "aws/claude-opus-4-6": json.dumps({"verdict": "complete", "issues": [], "suggestions": []}),
    }
    with patch("lib.gates.call_models_parallel", return_value=mock_responses):
        result = review_artifacts(
            artifact_paths=[Path("/tmp/algo.json")],
            review_prompt="Review these artifacts",
            models=["Azure/gpt-4o", "GCP/gemini-2.5-flash", "aws/claude-opus-4-6"],
        )
    assert result.passed is True
    assert result.verdicts["GCP/gemini-2.5-flash"]["verdict"] == "error"


from lib.gates import human_gate, GateResult


def test_human_gate_done_no_changes(monkeypatch):
    """User types 'd' (done) — approved, not modified."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
    )
    assert result.approved is True
    assert result.modified is False


def test_human_gate_quit(monkeypatch):
    """User types 'q' — SystemExit raised."""
    monkeypatch.setattr("builtins.input", lambda _: "q")
    review = ReviewResult(passed=True, verdicts={})
    import pytest
    with pytest.raises(SystemExit):
        human_gate(
            stage_name="Extract",
            artifact_paths=[Path("/tmp/test.json")],
            ai_review=review,
            context_for_chat=[],
        )


def test_human_gate_displays_gaps(monkeypatch, capsys):
    """When gaps are non-empty, they are displayed before the gate menu."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    gaps = [
        {"name": "SessionID", "type": "string", "description": "Session affinity identifier"},
        {"name": "SLOClass", "type": "string", "description": "SLO tier classification"},
    ]
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
        gaps=gaps,
    )
    captured = capsys.readouterr()
    assert "Sim-side gaps" in captured.out
    assert "SessionID" in captured.out
    assert "SLOClass" in captured.out
    assert result.approved is True


def test_human_gate_no_gaps_when_none(monkeypatch, capsys):
    """When gaps is None, no gap section is displayed."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
        gaps=None,
    )
    captured = capsys.readouterr()
    assert "unused" not in captured.out.lower()
    assert result.approved is True


def test_human_gate_no_gaps_when_empty(monkeypatch, capsys):
    """When gaps is an empty list, no gap section is displayed."""
    monkeypatch.setattr("builtins.input", lambda _: "d")
    review = ReviewResult(passed=True, verdicts={})
    result = human_gate(
        stage_name="Extract",
        artifact_paths=[Path("/tmp/test.json")],
        ai_review=review,
        context_for_chat=[],
        gaps=[],
    )
    captured = capsys.readouterr()
    assert "unused" not in captured.out.lower()
    assert "production" not in captured.out.lower()
    assert result.approved is True
