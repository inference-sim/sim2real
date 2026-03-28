import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from unittest.mock import patch
from lib.consensus import run_consensus_loop, ConsensusResult


def check_ok(responses):
    return all(v == "ok" for v in responses.values())


def check_never(responses):
    return False


def summarize(round_num, responses, consensus):
    return f"Round {round_num}: consensus={consensus}"


def test_consensus_reached_within_rounds():
    """Consensus on round 1 → loop exits, no user prompt needed."""
    call_count = [0]

    def fake_call(messages):
        call_count[0] += 1
        return {"Azure/gpt-4o": "ok", "GCP/gemini-2.5-flash": "ok", "aws/claude-opus-4-6": "ok"}

    result = run_consensus_loop(
        messages=[],
        call_fn=fake_call,
        check_fn=check_ok,
        summarize_fn=summarize,
        max_rounds=2,
        accept_on_consensus=True,
    )
    assert result.consensus is True
    assert result.rounds_run == 1
    assert call_count[0] == 1


def test_no_consensus_prompts_user_after_max_rounds():
    """No consensus after max_rounds → user is prompted."""
    fake_responses = {"Azure/gpt-4o": "a", "GCP/gemini-2.5-flash": "b", "aws/claude-opus-4-6": "c"}

    def fake_call(messages):
        return fake_responses

    with patch("builtins.input", return_value="a"):  # user accepts
        result = run_consensus_loop(
            messages=[],
            call_fn=fake_call,
            check_fn=check_never,
            summarize_fn=summarize,
            max_rounds=2,
            accept_on_consensus=False,
        )
    assert result.rounds_run == 2
    assert result.accepted_by_user is True


def test_user_quit_raises():
    """User types 'q' → SystemExit raised."""
    def fake_call(messages):
        return {"Azure/gpt-4o": "x"}

    with patch("builtins.input", return_value="q"):
        try:
            run_consensus_loop(
                messages=[],
                call_fn=fake_call,
                check_fn=check_never,
                summarize_fn=summarize,
                max_rounds=1,
            )
            assert False, "should raise SystemExit"
        except SystemExit:
            pass


def test_user_plus_n_adds_rounds():
    """User types '+1' → runs 1 more round, then user accepts."""
    call_count = [0]

    def fake_call(messages):
        call_count[0] += 1
        return {"m": "x"}

    inputs = iter(["+1", "a"])
    with patch("builtins.input", side_effect=lambda _: next(inputs)):
        result = run_consensus_loop(
            messages=[],
            call_fn=fake_call,
            check_fn=check_never,
            summarize_fn=summarize,
            max_rounds=1,
        )
    assert result.rounds_run == 2  # 1 initial + 1 from +1
    assert result.accepted_by_user is True
