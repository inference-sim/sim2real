"""Every `generate_from_config.py` flag documented in a SKILL.md must exist.

Why this file exists (#911): renaming `--emit-observe-yaml` to
`--emit-measurement-yaml` updated the bootstrap skill, this script's own tests and
pipeline/README.md — but missed `/sim2real-specify`'s SKILL.md, which documents the
same script independently as part of its own operator checklist. The stale command
exits 2 at argparse, and nothing failed: no test read the skill prose, and the
grep that would have caught it was scoped to the files the author already had in
mind rather than the repo.

`sim2real-specify`'s own text says the check exists "so the two skills cannot
drift". That was true of the field list and false of the flag name, because nothing
enforced it. This does.

Scope is deliberately narrow: flag NAMES in fenced commands, not whole-command
execution. Running the documented commands would need a real config.md and a
bundle, which is what the skills themselves are for.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parents[2]
SCRIPT = SKILLS_DIR / "sim2real-bootstrap" / "generate_from_config.py"

# `--flag` or `--flag=value`, long-form only. Short flags are not used in the
# documented invocations and would collide with prose like "-o".
_FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _supported_flags() -> set[str]:
    """Long flags the script's argparse actually accepts, read from --help.

    Taken from the live parser rather than a hardcoded list, so this cannot drift
    from the CLI the way the docs did.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, check=True,
    )
    return set(_FLAG_RE.findall(proc.stdout))


def _documented_invocations() -> list[tuple[Path, int, str]]:
    """Every line across all SKILL.md files that invokes the script.

    Returns (path, line number, line) so a failure names the file and line to fix.
    Continuation lines (trailing backslash) are joined, since the documented
    commands wrap.
    """
    out: list[tuple[Path, int, str]] = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        lines = skill_md.read_text().splitlines()
        i = 0
        while i < len(lines):
            if "generate_from_config.py" in lines[i]:
                start = i + 1
                joined = lines[i].rstrip()
                while joined.endswith("\\") and i + 1 < len(lines):
                    i += 1
                    joined = joined[:-1].rstrip() + " " + lines[i].strip()
                out.append((skill_md, start, joined))
            i += 1
    return out


def test_script_help_is_invocable():
    """Non-vacuousness: if --help failed, _supported_flags would be empty and
    every assertion below would be comparing against nothing."""
    flags = _supported_flags()
    assert "--emit-measurement-yaml" in flags
    assert "--dry-run" in flags


def test_at_least_one_invocation_is_documented():
    """Non-vacuousness for the parametrized test: if the scan found nothing,
    the flag check would pass by default."""
    found = _documented_invocations()
    assert found, "no generate_from_config.py invocations found in any SKILL.md"


def test_every_documented_flag_exists_in_the_cli():
    supported = _supported_flags()
    problems = []
    for path, lineno, cmd in _documented_invocations():
        for flag in _FLAG_RE.findall(cmd):
            if flag not in supported:
                rel = path.relative_to(SKILLS_DIR)
                problems.append(f"{rel}:{lineno}: {flag} is not a CLI flag — {cmd}")
    assert not problems, (
        "SKILL.md documents flags generate_from_config.py does not accept "
        "(an operator following these instructions gets an argparse error):\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.parametrize("dead_flag", ["--emit-observe-yaml"])
def test_renamed_flags_are_gone_from_every_skill(dead_flag):
    """Named regression for #911's miss. A rename must not leave the old spelling
    in any skill's prose, in this or any other skill directory."""
    offenders = [
        f"{p.relative_to(SKILLS_DIR)}:{n}"
        for p, n, cmd in _documented_invocations()
        if dead_flag in cmd
    ]
    assert not offenders, f"{dead_flag} still documented at: {offenders}"
