#!/usr/bin/env python3
"""Score a sim2real-specify audit against the ead56ea labeled fixture.

Sensitivity: the four defects that commit found in the focal arm at 08203b4.
Specificity: the two comparator arms it found clean and left unchanged.

The agent run itself is manual and nondeterministic; only the scoring here is
deterministic, and only the scoring is unit-tested.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ARM_FILES = {
    "causal_slo_externality": "algorithms/causal_slo_externality.go",
    "least_ttft_joint": "algorithms/least_ttft_joint.go",
    "kairos_paper": "algorithms/kairos_paper.go",
}

PROSE_FILES = ("README.md", "config.md")

CLEAN_ARMS = ("least_ttft_joint", "kairos_paper")

DEFECT_KINDS = ("WRONG", "UNSUPPORTED")


def load_labels(path: Path) -> list[dict]:
    return json.loads(path.read_text())["labels"]


def match(finding: dict, label: dict, tolerance: int = 6) -> bool:
    """Does a finding correspond to a labeled defect?

    Symbol match wins outright, so a finding that names the right function is
    credited even if its line drifted. Otherwise fall back to line proximity --
    an auditor may anchor a few lines off the exact expression.
    """
    if finding.get("file") != label["file"]:
        return False
    if finding.get("symbol") and finding["symbol"] == label["symbol"]:
        return True
    line = finding.get("line")
    if line is None:
        return False
    return any(abs(line - n) <= tolerance for n in label["lines"])


def undeclared_bridges(verdicts: list[dict]) -> list[dict]:
    """BRIDGE findings with no declared degradation. These block the gate.

    BRIDGE means no simulation counterpart exists BY CONSTRUCTION -- the code is
    there because the target lacks the simulator's state. That is not a defect and
    not a false claim; what makes it acceptable is a declared direction of bias in
    the specification header. A missing or null `declared_at` is the failure.
    """
    return [
        f
        for v in verdicts
        for f in v["findings"]
        if f.get("kind") == "BRIDGE" and not f.get("declared_at")
    ]


def score(verdicts: list[dict], labels: list[dict]) -> dict:
    # BRIDGE is deliberately excluded: it is neither a defect nor a false
    # positive, and is gated separately by undeclared_bridges().
    defects = [
        f for v in verdicts for f in v["findings"] if f.get("kind") in DEFECT_KINDS
    ]
    found = [
        label["id"] for label in labels if any(match(f, label) for f in defects)
    ]
    missed = [label["id"] for label in labels if label["id"] not in found]

    false_positives: dict[str, int] = {}
    for verdict in verdicts:
        arm = verdict["arm"]
        if arm not in CLEAN_ARMS:
            continue
        count = sum(1 for f in verdict["findings"] if f.get("kind") in DEFECT_KINDS)
        if count:
            false_positives[arm] = count

    return {
        "found": sorted(found),
        "missed": sorted(missed),
        "false_positive_arms": false_positives,
        "recall": (len(found) / len(labels)) if labels else 0.0,
    }


def materialize(repo: Path, commit: str, dest: Path) -> None:
    """Extract the three arm files plus prose at `commit` into `dest`."""
    (dest / "algorithms").mkdir(parents=True, exist_ok=True)
    for rel in [*ARM_FILES.values(), *PROSE_FILES]:
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        (dest / rel).write_text(blob)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("materialize", help="extract the fixture bundle")
    m.add_argument("--repo", required=True, type=Path)
    m.add_argument("--commit", default="08203b4")
    m.add_argument("--dest", required=True, type=Path)

    s = sub.add_parser("score", help="score verdict JSONs against the labels")
    s.add_argument("--labels", required=True, type=Path)
    s.add_argument("--verdict", action="append", required=True, type=Path)
    s.add_argument("--component", default="audit", choices=["audit", "rederive"])

    args = ap.parse_args(argv)

    if args.cmd == "materialize":
        materialize(args.repo, args.commit, args.dest)
        print(f"fixture at {args.dest} from {args.commit}")
        return 0

    labels = [
        label
        for label in load_labels(args.labels)
        if args.component in label["found_by"]
    ]
    verdicts = [json.loads(p.read_text()) for p in args.verdict]
    result = score(verdicts, labels)
    undeclared = undeclared_bridges(verdicts)
    result["undeclared_bridges"] = [
        f"{f.get('symbol')} at {f.get('file')}:{f.get('line')}" for f in undeclared
    ]
    print(json.dumps(result, indent=2))
    if result["missed"] or result["false_positive_arms"] or undeclared:
        print("ACCEPTANCE FAILED", file=sys.stderr)
        return 1
    print("ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
