"""AI review and human gate helpers for the prepare pipeline."""
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lib.llm import call_models_parallel, LLMError

_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


@dataclass
class ReviewResult:
    passed: bool
    verdicts: dict = field(default_factory=dict)

    def summary_lines(self) -> list[str]:
        """Format verdicts for terminal display."""
        lines = []
        for model, v in self.verdicts.items():
            if isinstance(v, dict):
                verdict = v.get("verdict", "unknown")
                if verdict == "error":
                    icon = "\u2717"
                    detail = "unreachable"
                    issues = v.get("issues", [])
                    if issues:
                        detail += f" — {issues[0]}"
                elif verdict == "complete":
                    icon = "\u2713"
                    detail = "complete"
                else:
                    icon = "\u26a0"
                    detail = verdict
                    issues = v.get("issues", [])
                    if issues:
                        detail += f" ({len(issues)} issue(s))"
                lines.append(f"  {icon} {model}: {detail}")
            else:
                lines.append(f"  \u2717 {model}: {v}")
        return lines


def _parse_review_response(raw: str) -> dict:
    """Parse JSON from an LLM review response, handling markdown fences."""
    clean = re.sub(r"^```(?:json)?\n?", "", raw.strip())
    clean = re.sub(r"\n?```$", "", clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"verdict": "incomplete", "issues": ["Failed to parse model response"], "suggestions": []}


def review_artifacts(
    artifact_paths: list[Path],
    review_prompt: str,
    models: list[str],
) -> ReviewResult:
    """Run multi-model AI review of artifacts.

    Args:
        artifact_paths: Paths to artifacts to include in the review prompt.
        review_prompt: The review prompt template. Artifact contents are appended.
        models: List of model names to call in parallel.

    Returns:
        ReviewResult with passed=True only if ALL models return verdict "complete".
    """
    # Build the full prompt with artifact contents
    artifact_text = ""
    for p in artifact_paths:
        if p.exists():
            artifact_text += f"\n## {p.name}\n```\n{p.read_text()}\n```\n"

    messages = [
        {"role": "system", "content": (
            "You are a technical reviewer. Review the provided artifacts for completeness "
            "and accuracy. Respond ONLY with JSON (no markdown fences):\n"
            '{"verdict": "complete"|"incomplete", "issues": [...], "suggestions": [...]}'
        )},
        {"role": "user", "content": f"{review_prompt}\n{artifact_text}"},
    ]

    raw_responses = call_models_parallel(models, messages)
    verdicts = {}
    successful_reviews = 0
    successful_complete = 0

    for model, resp in raw_responses.items():
        if isinstance(resp, LLMError):
            verdicts[model] = {"verdict": "error", "issues": [f"LLM error: {resp}"], "suggestions": []}
        else:
            parsed = _parse_review_response(resp)
            verdicts[model] = parsed
            successful_reviews += 1
            if parsed.get("verdict") == "complete":
                successful_complete += 1

    # LLM errors are non-votes: passed = all successful reviewers said "complete"
    if successful_reviews == 0:
        passed = False  # no model responded — cannot approve
    else:
        passed = successful_complete == successful_reviews

    return ReviewResult(passed=passed, verdicts=verdicts)


@dataclass
class GateResult:
    approved: bool
    modified: bool


def human_gate(
    stage_name: str,
    artifact_paths: list[Path],
    ai_review: ReviewResult,
    context_for_chat: list[Path],
    repo_root: Path | None = None,
    gaps: list[dict] | None = None,
) -> GateResult:
    """Interactive human review gate.

    Displays artifact paths and AI review summary, then offers:
      [e]dit — pause for manual file editing
      [c]hat — spawn claude -p for interactive refinement
      [d]one — approve and continue
      [q]uit — abort pipeline

    Args:
        stage_name: Display name (e.g., "Extract", "Translate").
        artifact_paths: Files the human should review.
        ai_review: AI review result to display.
        context_for_chat: Additional files loaded into claude -p context during chat.
        repo_root: Repository root for claude -p --add-dir. Defaults to REPO_ROOT.

    Returns:
        GateResult(approved, modified). Raises SystemExit on quit.
    """
    # Display banner
    print(f"\n{'=' * 55}")
    print(f"  HUMAN REVIEW REQUIRED: {stage_name}")
    print(f"{'=' * 55}\n")
    for p in artifact_paths:
        print(f"  Artifact: {p}")
    print()

    # Display AI review summary
    if ai_review.verdicts:
        print("  AI Review Summary:")
        for line in ai_review.summary_lines():
            print(line)
        print()

    # Display gaps if present
    if gaps:
        if stage_name == "Extract":
            print("  Sim-side gaps (attributes available but unused by algorithm):")
        else:
            print("  Production-side gaps (available in llm-d but unused by algorithm):")
        for g in gaps:
            name = g.get("name", "?")
            gtype = g.get("type") or g.get("category", "")
            desc = g.get("description", "")
            if gtype:
                print(f"    - {name} ({gtype}) \u2014 {desc}")
            else:
                print(f"    - {name} \u2014 {desc}")
        print()
        if stage_name == "Extract":
            print("  These source-file attributes exist but are NOT referenced by the EVOLVE-BLOCK.")
            print("  If any are important to the algorithm's behavior, the extraction may be incomplete.")
        else:
            print("  These production capabilities are NOT covered by this algorithm's translation.")
            print("  If any are critical to your use case, the generated plugin may be incomplete.")
        print()

    print(f"  {'=' * 51}")
    print("  These documents are the foundation of the entire")
    print("  transfer. If signals are missing or incorrectly")
    print("  mapped here, the generated code will be wrong")
    print("  regardless of how many review rounds follow.")
    print("  Please review carefully.")
    print(f"  {'=' * 51}\n")

    modified = False
    while True:
        choice = input("  [e] Edit file directly  [c] Chat with model  [d] Done  [q] Quit\n  > ").strip().lower()
        if choice == "d":
            return GateResult(approved=True, modified=modified)
        elif choice == "q":
            print("Aborted.")
            sys.exit(0)
        elif choice == "e":
            print(f"\n  Edit the file(s) listed above, then press Enter to continue...")
            input("  Press Enter when done editing > ")
            modified = True
            print("  File(s) updated. You can review again or press [d] to continue.\n")
        elif choice == "c":
            modified = _run_chat_loop(artifact_paths, context_for_chat, repo_root)
        else:
            print(f"  Invalid choice '{choice}'.")


def _run_chat_loop(
    artifact_paths: list[Path],
    context_for_chat: list[Path],
    repo_root: Path | None,
) -> bool:
    """Spawn an interactive claude session for artifact refinement.

    Launches claude in interactive mode with artifact context pre-loaded.
    The user gets a full multi-turn conversation with tool use. When they
    /exit, control returns to the gate loop.

    Returns True (assumes modifications were made — the human chose to chat).
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent

    # Build system prompt with artifact context
    artifact_reads = "\n".join(f"- {p}" for p in artifact_paths)
    context_reads = "\n".join(f"- {p}" for p in context_for_chat if p.exists())
    system_prompt = (
        "You are helping the user refine pipeline artifacts. "
        "The user will give you corrections and context.\n\n"
        f"Artifacts to review/edit:\n{artifact_reads}\n"
    )
    if context_reads:
        system_prompt += f"\nAdditional context files:\n{context_reads}\n"

    # Initial message: read artifacts so context is loaded from turn 1
    initial_msg = (
        "Read the artifact file(s) listed in your system prompt, "
        "then summarize their current state and ask what I'd like to change."
    )

    print("\n  Launching interactive claude session...")
    print("  Use /exit when you're done to return to the gate menu.\n")

    cmd = [
        "claude",
        "--system-prompt", system_prompt,
        "--dangerously-skip-permissions",
        "--add-dir", str(repo_root),
        initial_msg,
    ]
    # Inherit stdin/stdout/stderr so the user interacts directly
    proc = subprocess.run(cmd, cwd=repo_root)

    if proc.returncode != 0:
        print(f"\n  claude exited with code {proc.returncode}.")

    print("\n  Returned to gate menu.\n")
    # Assume artifacts were modified — the user chose to chat for a reason
    return True
