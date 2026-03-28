"""Consensus loop with interactive user prompting."""
import sys
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ConsensusResult:
    consensus: bool
    rounds_run: int
    responses: dict
    accepted_by_user: bool = False


def run_consensus_loop(
    messages: list[dict],
    call_fn: Callable[[list[dict]], dict],
    check_fn: Callable[[dict], bool],
    summarize_fn: Callable[[int, dict, bool], str],
    max_rounds: int = 2,
    accept_on_consensus: bool = True,
) -> ConsensusResult:
    """
    Run LLM calls in rounds until consensus or user accepts.

    Args:
        messages: The prompt messages to send each round.
        call_fn: Callable(messages) -> {model: response_text | LLMError}
        check_fn: Callable(responses) -> bool (True = consensus reached)
        summarize_fn: Callable(round_num, responses, consensus) -> str to print
        max_rounds: How many rounds to run before pausing for user input.
        accept_on_consensus: If True, auto-accept when consensus is reached.

    Returns ConsensusResult. Raises SystemExit if user quits.
    """
    rounds_run = 0
    responses: dict = {}
    remaining = max_rounds

    while True:
        rounds_run += 1
        remaining -= 1
        responses = call_fn(messages)
        consensus = check_fn(responses)
        print(summarize_fn(rounds_run, responses, consensus))

        if consensus and accept_on_consensus:
            return ConsensusResult(
                consensus=True, rounds_run=rounds_run, responses=responses
            )

        if remaining > 0:
            continue

        # Pause and prompt user
        if consensus:
            prompt = "\n  Consensus reached. [a]ccept / [c]ontinue / [q]uit: "
        else:
            prompt = "\n  No consensus. [c]ontinue 1 more / [+N] N more / [a]ccept-anyway / [q]uit: "

        while True:
            choice = input(prompt).strip().lower()
            if choice == "a":
                return ConsensusResult(
                    consensus=consensus, rounds_run=rounds_run,
                    responses=responses, accepted_by_user=True,
                )
            elif choice == "q":
                print("Aborted.")
                sys.exit(0)
            elif choice == "c":
                remaining = 1
                break
            elif choice.startswith("+"):
                try:
                    n = int(choice[1:])
                    if n < 1:
                        raise ValueError
                    remaining = n
                    break
                except ValueError:
                    print(f"  Invalid input '{choice}'. Enter 'c', '+N' (e.g. +3), 'a', or 'q'.")
            else:
                print(f"  Invalid input '{choice}'. Enter 'c', '+N', 'a', or 'q'.")
