#!/usr/bin/env python3
"""Multi-model translation review orchestrator.

Sends generated plugin code to multiple LLM models for independent
consistency review against the original EVOLVE-BLOCK algorithm.

Usage:
    review_translation.py --plugin FILE --algorithm FILE --signals FILE \
        --evolve-block FILE --rounds N --out FILE

Environment:
    OPENAI_API_KEY / OPENAI_BASE_URL  (primary)
    ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL (fallback)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import ssl
from pathlib import Path

MODELS = [
    "Azure/gpt-4o",
    "GCP/gemini-2.5-flash",
    "aws/claude-opus-4-6",
]

SCRIPT_DIR = Path(__file__).parent
BUILD_REQUEST = SCRIPT_DIR / "build_review_request.py"

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"


def resolve_api_credentials():
    """Resolve API key and base URL from environment."""
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL", os.environ.get("OPENAI_URL", "https://api.openai.com"))

    if not api_key:
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")

    if not api_key:
        print(f"{RED}[ERROR]{NC} No API key found. Set OPENAI_API_KEY or ANTHROPIC_AUTH_TOKEN.",
              file=sys.stderr)
        sys.exit(1)

    return api_key, base_url


def extract_json_from_content(content):
    """Extract JSON object from LLM response content.

    Handles common LLM output quirks:
    - Markdown code fences (```json ... ```)
    - Prose before/after JSON
    - Truncated responses (unclosed braces/brackets)
    - Opus: sometimes wraps JSON in explanation text
    - Gemini: sometimes uses trailing commas
    """
    text = content.strip()

    # 1. Extract from code fences (greedy — take the largest fenced block)
    fence_matches = list(re.finditer(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL))
    if fence_matches:
        text = max(fence_matches, key=lambda m: len(m.group(1))).group(1).strip()
    elif re.match(r'^```(?:json)?\s*\n', text):
        # Truncated fence: starts with ``` but no closing
        text = re.sub(r'^```(?:json)?\s*\n', '', text).strip()

    # 2. Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Find outermost { ... } using stack-based brace matching
    start = text.find('{')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    end = start
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if depth == 0 and end > start:
        candidate = text[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Gemini fix: remove trailing commas before } or ]
        cleaned = re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # 4. Last resort: close unclosed braces (truncated response)
    candidate = text[start:]
    candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
    open_braces = candidate.count('{') - candidate.count('}')
    open_brackets = candidate.count('[') - candidate.count(']')
    quote_count = len(re.findall(r'(?<!\\)"', candidate))
    if quote_count % 2 == 1:
        candidate += '"'
    candidate += ']' * max(0, open_brackets)
    candidate += '}' * max(0, open_braces)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    return None


def call_model(model, plugin_file, algo_file, signal_file, evolve_file,
               round_num, api_key, base_url, extra_context=""):
    """Call a single model and return parsed review JSON."""
    cmd = ["python3", str(BUILD_REQUEST), model, plugin_file, algo_file,
           signal_file, evolve_file, str(round_num)]
    if extra_context:
        cmd += ["--extra-context", extra_context]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"{RED}[ERROR]{NC} Failed to build request for {model}: {result.stderr}",
              file=sys.stderr)
        return None

    request_json = result.stdout.strip()

    endpoint = f"{base_url}/v1/chat/completions"

    # Use urllib instead of curl to avoid shell argument size limits
    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        req = urllib.request.Request(endpoint, data=request_json.encode(), headers=headers)
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=300, context=ctx)
        response_body = resp.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200] if hasattr(e, 'read') else str(e)
        print(f"{RED}[ERROR]{NC} {model} returned HTTP {e.code}: {body}",
              file=sys.stderr)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"{RED}[ERROR]{NC} {model} request failed: {e}", file=sys.stderr)
        return None

    try:
        response = json.loads(response_body)
        content = response["choices"][0]["message"]["content"]
        finish_reason = response["choices"][0].get("finish_reason", "unknown")

        if finish_reason == "length":
            print(f"{YELLOW}[WARN]{NC} {model} response truncated (finish_reason=length)",
                  file=sys.stderr)

        review = extract_json_from_content(content)
        if review is None:
            print(f"{RED}[ERROR]{NC} Failed to extract JSON from {model} response",
                  file=sys.stderr)
            print(f"  Content preview: {content[:200]}...", file=sys.stderr)
            return None

        review["model"] = model
        review["round"] = round_num
        return review
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"{RED}[ERROR]{NC} Failed to parse {model} response: {e}",
              file=sys.stderr)
        return None


def run_review_round(round_num, args, api_key, base_url):
    """Run one round of reviews across all models. Return list of reviews."""
    print(f"\n{BLUE}━━━ Review Round {round_num}/{args.rounds} ━━━{NC}")
    reviews = []
    extra_context = getattr(args, "extra_context", "") or ""
    for model in MODELS:
        print(f"  Reviewing with {model}...", end=" ", flush=True)
        review = call_model(model, args.plugin, args.algorithm, args.signals,
                            args.evolve_block, round_num, api_key, base_url,
                            extra_context=extra_context)
        if review is None:
            print(f"{RED}FAILED{NC}")
            reviews.append({
                "model": model, "round": round_num,
                "verdict": "inconsistent",
                "per_signal": [],
                "issues": ["API call failed — treating as inconsistent"],
                "suggestions": [],
            })
        elif review.get("verdict") == "consistent":
            print(f"{GREEN}CONSISTENT{NC}")
            reviews.append(review)
        else:
            print(f"{YELLOW}INCONSISTENT{NC}")
            reviews.append(review)
    return reviews


def check_consensus(reviews):
    """Return True if all reviews are consistent."""
    return all(r.get("verdict") == "consistent" for r in reviews)


def main():
    parser = argparse.ArgumentParser(description="Multi-model translation review")
    parser.add_argument("--plugin", "--scorer", dest="plugin", required=True,
                        help="Path to generated plugin .go file")
    parser.add_argument("--algorithm", required=True, help="Path to algorithm_summary.json")
    parser.add_argument("--signals", required=True, help="Path to signal_coverage.json")
    parser.add_argument("--evolve-block", required=True, help="Path to EVOLVE-BLOCK source")
    parser.add_argument("--rounds", type=int, default=2, help="Number of review rounds (default: 2)")
    parser.add_argument("--out", required=True, help="Output path for translation_reviews.json")
    parser.add_argument("--extra-context", default="", metavar="FILE",
                        dest="extra_context",
                        help="Path to extra context file to append to each reviewer prompt")
    args = parser.parse_args()

    for path_attr in ("plugin", "algorithm", "signals", "evolve_block"):
        path = getattr(args, path_attr)
        if not os.path.isfile(path):
            print(f"{RED}[ERROR]{NC} File not found: {path}", file=sys.stderr)
            sys.exit(1)

    api_key, base_url = resolve_api_credentials()

    all_rounds = []
    final_verdict = "inconsistent"

    for round_num in range(1, args.rounds + 1):
        reviews = run_review_round(round_num, args, api_key, base_url)
        consensus = check_consensus(reviews)

        round_result = {
            "round": round_num,
            "reviews": reviews,
            "consensus": consensus,
            "fixes_applied": [],
        }
        all_rounds.append(round_result)

        if consensus:
            print(f"\n{GREEN}━━━ Consensus reached in round {round_num} ━━━{NC}")
            final_verdict = "consistent"
            break

        if round_num < args.rounds:
            all_issues = []
            for r in reviews:
                if r.get("verdict") == "inconsistent":
                    all_issues.extend(r.get("issues", []))
            print(f"\n{YELLOW}Issues to fix before round {round_num + 1}:{NC}")
            for issue in all_issues:
                print(f"  - {issue}")
            print(f"\n{YELLOW}FIX_NEEDED: Apply fixes to the plugin, then re-run this script.{NC}")
            partial = {
                "rounds": all_rounds,
                "final_verdict": "pending",
                "total_rounds": round_num,
                "models_used": MODELS,
                "issues_to_fix": all_issues,
            }
            with open(args.out, "w") as f:
                json.dump(partial, f, indent=2)
            sys.exit(2)
        else:
            print(f"\n{RED}━━━ Final round {round_num}: no consensus — HALT ━━━{NC}")
            final_verdict = "inconsistent"

    output = {
        "rounds": all_rounds,
        "final_verdict": final_verdict,
        "total_rounds": len(all_rounds),
        "models_used": MODELS,
    }
    with open(args.out, "w") as f:
        json.dump(output, f, indent=2)

    if final_verdict == "consistent":
        print(f"\n{GREEN}Review passed. Results written to {args.out}{NC}")
        sys.exit(0)
    else:
        print(f"\n{RED}Review FAILED. Results written to {args.out}{NC}")
        sys.exit(1)


if __name__ == "__main__":
    main()
