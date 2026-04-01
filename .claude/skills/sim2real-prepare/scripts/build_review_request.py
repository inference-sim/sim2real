#!/usr/bin/env python3
"""Build a structured review request for translation consistency checking."""

import json
import sys


def build_request(model: str, plugin_code: str, algorithm_summary: str,
                  signal_coverage: str, evolve_block: str, round_num: int,
                  extra_context: str = "") -> str:
    """Return JSON string for a /v1/chat/completions request."""
    system_prompt = (
        "You are a technical reviewer verifying that a generated Go plugin "
        "faithfully implements an evolved routing algorithm. You will receive:\n"
        "1. The generated plugin Go code\n"
        "2. The algorithm summary (extracted metadata)\n"
        "3. The signal coverage mapping (sim signals → production equivalents)\n"
        "4. The original EVOLVE-BLOCK source (the ground truth)\n\n"
        "For EACH signal in the signal coverage, verify:\n"
        "- The plugin reads the correct production field\n"
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
        f"## Generated Plugin Code\n```go\n{plugin_code}\n```\n\n"
        f"## Algorithm Summary\n```json\n{algorithm_summary}\n```\n\n"
        f"## Signal Coverage\n```json\n{signal_coverage}\n```\n\n"
        f"## EVOLVE-BLOCK Source\n```go\n{evolve_block}\n```\n"
    )
    if extra_context:
        user_content += f"\n## System Context (for reviewer)\n{extra_context}\n"

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
        print("Usage: build_review_request.py MODEL PLUGIN_FILE ALGO_FILE SIGNAL_FILE EVOLVE_FILE [ROUND]",
              file=sys.stderr)
        sys.exit(1)

    import argparse as _ap
    parser = _ap.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("plugin_file")
    parser.add_argument("algo_file")
    parser.add_argument("signal_file")
    parser.add_argument("evolve_file")
    parser.add_argument("round_num", nargs="?", type=int, default=1)
    parser.add_argument("--extra-context", default="", metavar="FILE",
                        help="Path to extra context file to append to reviewer prompt")
    args = parser.parse_args()

    with open(args.plugin_file) as f:
        plugin_code = f.read()
    with open(args.algo_file) as f:
        algorithm_summary = f.read()
    with open(args.signal_file) as f:
        signal_coverage = f.read()
    with open(args.evolve_file) as f:
        evolve_block = f.read()

    extra_context = ""
    if args.extra_context:
        try:
            with open(args.extra_context) as f:
                extra_context = f.read()
        except OSError:
            pass

    print(build_request(args.model, plugin_code, algorithm_summary,
                        signal_coverage, evolve_block, args.round_num,
                        extra_context=extra_context))


if __name__ == "__main__":
    main()
