#!/usr/bin/env python3
"""Build a structured review request for translation consistency checking."""

import json
import sys


def build_request(model: str, scorer_code: str, algorithm_summary: str,
                  signal_coverage: str, evolve_block: str, round_num: int) -> str:
    """Return JSON string for a /v1/chat/completions request."""
    system_prompt = (
        "You are a technical reviewer verifying that a generated Go scorer plugin "
        "faithfully implements an evolved routing algorithm. You will receive:\n"
        "1. The generated scorer Go code\n"
        "2. The algorithm summary (extracted metadata)\n"
        "3. The signal coverage mapping (sim signals → production equivalents)\n"
        "4. The original EVOLVE-BLOCK source (the ground truth)\n\n"
        "For EACH signal in the signal coverage, verify:\n"
        "- The scorer reads the correct production field\n"
        "- Normalization matches the algorithm summary\n"
        "- The weight/coefficient matches the EVOLVE-BLOCK\n"
        "- The scoring logic (comparison, threshold, combination) is faithful\n\n"
        "Respond with ONLY a valid JSON object (no markdown fences, no prose, no explanation outside the JSON). "
        "Keep rationale strings concise (under 200 characters each) to avoid truncation. "
        "Schema:\n"
        '{"verdict": "consistent"|"inconsistent", '
        '"per_signal": [{"signal": "name", "consistent": true|false, '
        '"rationale": "brief explanation"}], '
        '"issues": ["..."], "suggestions": ["..."]}\n\n'
        "IMPORTANT: The EffectiveLoad composite uses WaitingQueueSize + RunningRequestsSize in production "
        "(BatchSize is zeroed per F-10 double-counting fix documented in signal coverage). "
        "This is an intentional design decision, not a translation error."
    )

    user_content = (
        f"## Round {round_num}\n\n"
        f"## Generated Scorer Code\n```go\n{scorer_code}\n```\n\n"
        f"## Algorithm Summary\n```json\n{algorithm_summary}\n```\n\n"
        f"## Signal Coverage\n```json\n{signal_coverage}\n```\n\n"
        f"## EVOLVE-BLOCK Source\n```go\n{evolve_block}\n```\n"
    )

    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    return json.dumps(request)


def main():
    if len(sys.argv) < 6:
        print("Usage: build_review_request.py MODEL SCORER_FILE ALGO_FILE SIGNAL_FILE EVOLVE_FILE [ROUND]",
              file=sys.stderr)
        sys.exit(1)

    model = sys.argv[1]
    with open(sys.argv[2]) as f:
        scorer_code = f.read()
    with open(sys.argv[3]) as f:
        algorithm_summary = f.read()
    with open(sys.argv[4]) as f:
        signal_coverage = f.read()
    with open(sys.argv[5]) as f:
        evolve_block = f.read()
    round_num = int(sys.argv[6]) if len(sys.argv) > 6 else 1

    print(build_request(model, scorer_code, algorithm_summary,
                        signal_coverage, evolve_block, round_num))


if __name__ == "__main__":
    main()
