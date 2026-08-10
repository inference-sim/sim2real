# sim2real-specify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `sim2real-specify` skill — bundle generation from arbitrary provenance, with the algorithm specification gated by an independent citation audit, a blind re-derivation, and a mechanical citation lint.

**Architecture:** A skill directory mirroring `sim2real-translate`: one `SKILL.md` carrying phases 0–6 as prose instructions, two agent prompts substituted with `{PLACEHOLDER}` variables, and one Python script. The only production code is the lint plus an acceptance scorer; everything else is prompts. The gate's agents are spawned `subagent_type=general-purpose`, `model="opus"`, `run_in_background=true`, reporting to `main` — the same mechanics `sim2real-translate` Step 2 uses.

**Tech Stack:** Python 3.11 (stdlib only), pytest, Markdown + YAML frontmatter for skills.

## Global Constraints

- Work on branch `design/sim2real-specify` in the `sim2real` repo. The design spec is `docs/superpowers/specs/2026-08-10-sim2real-specify-design.md`.
- Python: use the repo-local venv. All commands run as `.venv/bin/python` / `.venv/bin/pytest` from the repo root. Never system Python.
- `scripts/lint_citations.py` and `scripts/acceptance.py` use **stdlib only** — no new entries in `requirements.txt`.
- Skill lives at `.claude/skills/sim2real-specify/`. Frontmatter keys, in this order: `name`, `description` (block scalar), `argument-hint`, `user-invocable`, `allowed-tools`.
- The skill's `name` is exactly `sim2real-specify`; invocation is `/sim2real-specify <experiment-root>`.
- Prompts use `{PLACEHOLDER}` substitution. Every placeholder appearing in a prompt file MUST be listed in SKILL.md's substitution table — Task 6 enforces this mechanically.
- Output contract is fixed by the spec: `algorithms/<arm>.go`, `README.md`, `config.md`, `workloads/`, `inputs/`, `sim_results/` + `CHECKSUMS.sha256`, `.gitignore`. **No `LIMITATIONS.md`.**
- The specification layer invents no in-source marker conventions. A specification may request things of downstream stages; it may never assert what they do.
- Line numbers cited below for the fixture refer to `algorithms/causal_slo_externality.go` as it exists at commit `08203b4` in the `pd-infocomm` repo (773 lines). Do not confuse them with the current, post-`ead56ea` file.

## File Structure

```
.claude/skills/sim2real-specify/
  SKILL.md                        # Task 5 — phases 0-6, interview, output contract
  prompts/audit.md                # Task 3 — independent citation audit
  prompts/rederive.md             # Task 4 — blind re-derivation, focal arm
  scripts/lint_citations.py       # Tasks 1-2 — mechanical path:line resolution
  scripts/acceptance.py           # Task 7 — fixture materialization + verdict scoring
  tests/__init__.py               # Task 1
  tests/test_lint_citations.py    # Tasks 1-2
  tests/test_skill_substitution.py# Task 6
  tests/test_acceptance.py        # Task 7
  tests/fixtures/ead56ea_labels.json   # Task 7 — the four labeled defects
```

Responsibilities: `lint_citations.py` resolves `path:line` tokens against pinned trees and knows nothing about bundles' meaning. `acceptance.py` materializes the labeled fixture and scores a verdict JSON; it knows nothing about how the verdict was produced. `SKILL.md` orchestrates. The two prompts are independently runnable against any bundle.

---

### Task 1: Citation extraction

Parsing only — no filesystem access. A citation is a path with a known source extension followed by `:` and a line spec.

**Files:**
- Create: `.claude/skills/sim2real-specify/scripts/lint_citations.py`
- Create: `.claude/skills/sim2real-specify/tests/__init__.py` (empty)
- Test: `.claude/skills/sim2real-specify/tests/test_lint_citations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Citation` dataclass with fields `path: str`, `lines: tuple[int, ...]`, `source_line: int`; `parse_citations(text: str) -> list[Citation]`; `parse_line_spec(spec: str) -> tuple[int, ...]`.

- [ ] **Step 1: Write the failing test**

Create `.claude/skills/sim2real-specify/tests/__init__.py` as an empty file, then write `.claude/skills/sim2real-specify/tests/test_lint_citations.py`:

```python
import importlib.util
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

def _load():
    spec = importlib.util.spec_from_file_location(
        "lint_citations", SKILL / "scripts" / "lint_citations.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

lint = _load()


def test_parses_simple_citation():
    cites = lint.parse_citations("see sim/edpp_var.go:168 for the charge")
    assert len(cites) == 1
    assert cites[0].path == "sim/edpp_var.go"
    assert cites[0].lines == (168,)
    assert cites[0].source_line == 1


def test_parses_range_and_comma_specs():
    cites = lint.parse_citations(
        "a sim/edpp_var.go:144-157 b\nc utilization/config.go:33,153 d"
    )
    assert cites[0].lines == (144, 157)
    assert cites[0].source_line == 1
    assert cites[1].lines == (33, 153)
    assert cites[1].source_line == 2


def test_parses_bare_basename():
    cites = lint.parse_citations("llm-d-router passes it through (extractor.go:127)")
    assert cites[0].path == "extractor.go"
    assert cites[0].lines == (127,)


def test_ignores_non_citation_noise():
    text = "priced 1.6x more; pinned v0.9.0 at 871b169b; ratio 0.554:0.906"
    assert lint.parse_citations(text) == []


def test_ignores_lint_skip_lines():
    text = "an example like foo/bar.go:12 is illustrative  # lint-skip"
    assert lint.parse_citations(text) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_lint_citations.py -v`
Expected: FAIL — collection error, `scripts/lint_citations.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `.claude/skills/sim2real-specify/scripts/lint_citations.py`:

```python
#!/usr/bin/env python3
"""Resolve `path:line` citations in a sim2real bundle against pinned checkouts.

Mechanical only: verifies that a cited path exists in exactly one pinned tree and
that the cited line numbers are within that file. Cannot detect a correct citation
attached to a wrong transcription -- that is the audit agent's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCE_EXTS = ("go", "py", "md", "yaml", "yml", "json")

CITATION_RE = re.compile(
    r"(?<![\w/.-])"                                    # not mid-token
    r"([A-Za-z0-9_][\w./-]*\.(?:" + "|".join(SOURCE_EXTS) + r"))"
    r":(\d+(?:[-,]\d+)*)"                              # 12 | 12-20 | 12,20
    r"(?!\d)"                                          # whole number only
)
# NOTE: do not add `.` to that final lookahead. Citations frequently end a
# sentence -- `sim/edpp.go:1707.` -- and excluding a trailing period silently
# drops every one of them. Version-like noise is already excluded by requiring a
# known source extension in the path group.

SKIP_MARKER = "lint-skip"


@dataclass(frozen=True)
class Citation:
    path: str
    lines: tuple[int, ...]
    source_line: int

    def __str__(self) -> str:
        return f"{self.path}:{','.join(str(n) for n in self.lines)}"


def parse_line_spec(spec: str) -> tuple[int, ...]:
    """'168' -> (168,); '144-157' -> (144, 157); '33,153' -> (33, 153)."""
    return tuple(int(part) for part in re.split(r"[-,]", spec) if part)


def parse_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if SKIP_MARKER in line:
            continue
        for path, spec in CITATION_RE.findall(line):
            out.append(Citation(path=path, lines=parse_line_spec(spec), source_line=lineno))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_lint_citations.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/sim2real-specify/scripts/lint_citations.py \
        .claude/skills/sim2real-specify/tests/__init__.py \
        .claude/skills/sim2real-specify/tests/test_lint_citations.py
git commit -m "feat(specify): citation extraction for the bundle lint"
```

---

### Task 2: Citation resolution and CLI

Resolve each citation by **suffix match** against one or more pinned trees. Suffix matching is required because real citations range from `sim/edpp_var.go` to a bare `extractor.go`. Ambiguity is a failure, not a guess — it forces the author to write a more specific citation.

**Files:**
- Modify: `.claude/skills/sim2real-specify/scripts/lint_citations.py`
- Test: `.claude/skills/sim2real-specify/tests/test_lint_citations.py`

**Interfaces:**
- Consumes: `Citation`, `parse_citations` from Task 1.
- Produces: `Failure` dataclass with `file: str`, `source_line: int`, `citation: str`, `kind: str`, `detail: str`; `index_tree(root: Path) -> dict[str, list[Path]]`; `resolve(cit, indexes) -> Failure | None`; `lint_bundle(bundle: Path, trees: list[Path], exts: tuple[str, ...]) -> list[Failure]`; `main(argv: list[str] | None = None) -> int`. Failure `kind` is one of `unresolved-path`, `ambiguous-path`, `line-out-of-range`. Exit codes: `0` clean, `1` failures found, `2` usage error.

- [ ] **Step 1: Write the failing test**

Append to `.claude/skills/sim2real-specify/tests/test_lint_citations.py`:

```python
import pytest


@pytest.fixture
def tree(tmp_path):
    """A fake pinned checkout with two files, one of them shadowed by name."""
    root = tmp_path / "checkout"
    (root / "sim").mkdir(parents=True)
    (root / "sim" / "edpp_var.go").write_text("\n".join(f"line{i}" for i in range(1, 201)))
    (root / "pkg" / "a").mkdir(parents=True)
    (root / "pkg" / "a" / "dup.go").write_text("x\ny\n")
    (root / "pkg" / "b").mkdir(parents=True)
    (root / "pkg" / "b" / "dup.go").write_text("x\ny\n")
    (root / ".git").mkdir()
    (root / ".git" / "edpp_var.go").write_text("noise\n")
    return root


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    (root / "algorithms").mkdir(parents=True)
    return root


def test_resolves_valid_citation(bundle, tree):
    (bundle / "algorithms" / "a.go").write_text("// ported from sim/edpp_var.go:168\n")
    assert lint.lint_bundle(bundle, [tree], (".go", ".md")) == []


def test_flags_unresolved_path(bundle, tree):
    (bundle / "README.md").write_text("see sim/nope.go:5\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert [f.kind for f in fails] == ["unresolved-path"]


def test_flags_line_out_of_range(bundle, tree):
    (bundle / "README.md").write_text("see sim/edpp_var.go:5000\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert [f.kind for f in fails] == ["line-out-of-range"]
    assert "200" in fails[0].detail


def test_flags_ambiguous_path(bundle, tree):
    (bundle / "README.md").write_text("see dup.go:1\n")
    fails = lint.lint_bundle(bundle, [tree], (".go", ".md"))
    assert [f.kind for f in fails] == ["ambiguous-path"]


def test_ignores_dot_directories(bundle, tree):
    """.git/edpp_var.go must not create ambiguity with sim/edpp_var.go."""
    (bundle / "README.md").write_text("see edpp_var.go:1\n")
    assert lint.lint_bundle(bundle, [tree], (".go", ".md")) == []


def test_bundle_is_its_own_tree(bundle, tree):
    """A self-reference like README.md:2 resolves against the bundle itself."""
    (bundle / "README.md").write_text("first\nsee README.md:2\n")
    assert lint.lint_bundle(bundle, [tree], (".go", ".md")) == []


def test_main_exit_codes(bundle, tree, capsys):
    (bundle / "README.md").write_text("see sim/nope.go:5\n")
    rc = lint.main(["--bundle", str(bundle), "--tree", str(tree)])
    assert rc == 1
    assert "unresolved-path" in capsys.readouterr().out
    (bundle / "README.md").write_text("see sim/edpp_var.go:168\n")
    assert lint.main(["--bundle", str(bundle), "--tree", str(tree)]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_lint_citations.py -v`
Expected: FAIL with `AttributeError: module 'lint_citations' has no attribute 'lint_bundle'`.

- [ ] **Step 3: Write minimal implementation**

Append to `.claude/skills/sim2real-specify/scripts/lint_citations.py`:

```python
import argparse
import sys
from pathlib import Path


@dataclass(frozen=True)
class Failure:
    file: str
    source_line: int
    citation: str
    kind: str
    detail: str


def index_tree(root: Path) -> dict[str, list[Path]]:
    """Map basename -> files, skipping dot-directories."""
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        index.setdefault(path.name, []).append(path)
    return index


def _candidates(cit: Citation, indexes: list[dict[str, list[Path]]]) -> list[Path]:
    basename = cit.path.rsplit("/", 1)[-1]
    out: list[Path] = []
    for index in indexes:
        for path in index.get(basename, []):
            if path.as_posix().endswith(cit.path):
                out.append(path)
    return out


def resolve(cit: Citation, indexes: list[dict[str, list[Path]]]) -> Failure | None:
    matches = _candidates(cit, indexes)
    if not matches:
        return Failure("", cit.source_line, str(cit), "unresolved-path",
                       "no file in any pinned tree ends with this path")
    if len(set(matches)) > 1:
        shown = ", ".join(sorted(p.as_posix() for p in set(matches))[:4])
        return Failure("", cit.source_line, str(cit), "ambiguous-path",
                       f"matches {len(set(matches))} files: {shown}")
    target = matches[0]
    nlines = len(target.read_text(errors="replace").splitlines())
    over = [n for n in cit.lines if n > nlines or n < 1]
    if over:
        return Failure("", cit.source_line, str(cit), "line-out-of-range",
                       f"{target.name} has {nlines} lines; cited {over}")
    return None


def lint_bundle(bundle: Path, trees: list[Path], exts: tuple[str, ...]) -> list[Failure]:
    indexes = [index_tree(t) for t in [*trees, bundle]]
    failures: list[Failure] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.suffix not in exts:
            continue
        if any(part.startswith(".") for part in path.relative_to(bundle).parts[:-1]):
            continue
        text = path.read_text(errors="replace")
        for cit in parse_citations(text):
            failure = resolve(cit, indexes)
            if failure is not None:
                rel = path.relative_to(bundle).as_posix()
                failures.append(Failure(rel, failure.source_line, failure.citation,
                                        failure.kind, failure.detail))
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint path:line citations in a sim2real bundle.")
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--tree", action="append", default=[], type=Path,
                    help="pinned checkout root; repeatable")
    ap.add_argument("--ext", default=".go,.md",
                    help="comma-separated file extensions to scan")
    args = ap.parse_args(argv)

    if not args.bundle.is_dir():
        print(f"usage error: --bundle {args.bundle} is not a directory", file=sys.stderr)
        return 2
    for tree in args.tree:
        if not tree.is_dir():
            print(f"usage error: --tree {tree} is not a directory", file=sys.stderr)
            return 2

    exts = tuple(e if e.startswith(".") else f".{e}" for e in args.ext.split(","))
    failures = lint_bundle(args.bundle, args.tree, exts)
    for f in failures:
        print(f"FAIL {f.kind} {f.file}:{f.source_line} cite={f.citation} -- {f.detail}")
    print(f"{len(failures)} citation failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_lint_citations.py -v`
Expected: PASS — 12 passed.

- [ ] **Step 5: Smoke-test against the real citation-dense file**

The post-`ead56ea` focal arm is the densest real citation source available. Run it against the BLIS checkout (adjust paths to your machine):

```bash
mkdir -p /tmp/lintsmoke/algorithms
cp ../pd-infocomm/algorithms/causal_slo_externality.go /tmp/lintsmoke/algorithms/
.venv/bin/python .claude/skills/sim2real-specify/scripts/lint_citations.py \
  --bundle /tmp/lintsmoke --tree inference-sim
```

Expected: `sim/...` citations resolve. Citations into `llm-d-router` and `vllm` report `unresolved-path` because those trees were not passed — confirming the tool reports rather than guesses. Re-run adding `--tree ../pd-infocomm/llm-d-router --tree ../pd-infocomm/vllm` and confirm the count drops. Record the before/after counts in the commit message.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/sim2real-specify/scripts/lint_citations.py \
        .claude/skills/sim2real-specify/tests/test_lint_citations.py
git commit -m "feat(specify): resolve bundle citations against pinned trees"
```

---

### Task 3: Audit prompt

The independent citation audit, run on every arm. It must perform **correspondence discovery**, not mere citation-checking: the pre-fix fixture carries one citation, so an auditor that only verifies what is already cited returns clean on a file containing four defects.

**Files:**
- Create: `.claude/skills/sim2real-specify/prompts/audit.md`

**Interfaces:**
- Consumes: nothing at runtime; SKILL.md substitutes its placeholders.
- Produces: placeholders `{BUNDLE_ROOT}`, `{ARM_FILE}`, `{ARM_NAME}`, `{SIM_TREE}`, `{SIM_PIN}`, `{TARGET_TREE}`, `{TARGET_PIN}`, `{VERDICT_PATH}`, `{MAIN_SESSION_NAME}`. Writes a verdict JSON to `{VERDICT_PATH}` with schema `{"arm": str, "findings": [{"kind": "CONFIRMED"|"WRONG"|"UNSUPPORTED"|"BRIDGE", "file": str, "line": int, "symbol": str, "claim": str, "upstream": str, "settled_by": str, "declared_at": str | null, "detail": str}]}`. `declared_at` is required on `BRIDGE` findings and null elsewhere.

- [ ] **Step 1: Write the prompt**

Create `.claude/skills/sim2real-specify/prompts/audit.md`:

````markdown
# Independent specification audit

You are auditing ONE algorithm specification in a sim2real bundle. You have no
generation transcript and must not ask for one. Your only sources of truth are the
two checkouts named below. Treat the bundle as a claim to be tested.

## Inputs

- Bundle root: `{BUNDLE_ROOT}`
- Specification under audit: `{ARM_FILE}` (arm name: `{ARM_NAME}`)
- Simulation checkout: `{SIM_TREE}` pinned at `{SIM_PIN}`
- Target component checkout: `{TARGET_TREE}` pinned at `{TARGET_PIN}`
- Write your verdict to: `{VERDICT_PATH}`

## Method: correspondence discovery

Do NOT assume the specification's citations are complete. Citation density is an
audit OUTPUT, not an input.

For every non-trivial ported expression in `{ARM_FILE}` -- every arithmetic
formula, every unit conversion, every rounding decision, every loop bound, every
guard that skips or censors a term -- do this:

1. Locate the upstream counterpart. If the expression carries a citation, use it
   as a hint and verify it points where it claims. If it carries none, SEARCH the
   simulation checkout for the corresponding code by symbol name, by formula
   shape, and by the surrounding concept. Report `UNSUPPORTED` only after
   searching, never merely because a citation was absent.
2. Compare term by term. Enumerate the upstream terms and the ported terms and
   check the sets match. A missing additive term is the most common defect and
   the easiest to overlook, because the remaining terms look correct.
3. Check units explicitly. A name is not a unit. If the upstream reads a metric,
   find that metric's definition in the target or engine checkout and confirm the
   scale factor. Never trust a prose mapping document over source.
4. Check rounding and boundary handling. Whether a quantity is truncated,
   floored, or ceiled changes results and is invisible in aggregate.
5. Check quantities recomputed per call site upstream. If upstream derives a
   value locally at each use (for example a per-request budget clamped to a
   request-specific size), a ported constant is a defect even when an algebraically
   equivalent downstream quantity hides it.
6. Decide whether the code is PORTED or BRIDGE, and audit it accordingly.
   - PORTED: the simulation computes this quantity, so a counterpart exists.
     Compare against it. This includes quantities the simulation reads directly
     from its own state — if the simulation knows the value exactly and the port
     obtains it some other way, the counterpart still exists and the acquisition
     is what you are checking. A unit error here is `WRONG`, not `BRIDGE`.
   - BRIDGE: the code exists ONLY because the target lacks the simulation's
     state, so no counterpart exists and none should. Do not report it
     `UNSUPPORTED` — absence of a counterpart is the expected finding. Instead
     verify its assumptions against the target or engine checkout, and check that
     the specification header declares the degradation AND the direction of the
     resulting bias. Record that header line in `declared_at`. If no declaration
     exists, emit the finding with `declared_at: null`.

Then audit the prose. For each non-obvious claim in `{BUNDLE_ROOT}/README.md` and
`{BUNDLE_ROOT}/config.md`, resolve it against a checkout. Apply one rule:

> A specification may REQUEST things of downstream stages. It may never ASSERT
> what they do.

A sentence asserting the behavior of another pipeline stage, tool, or agent is
`UNSUPPORTED` unless that component's own source says so. Verify against the
component's source, including any section declaring what it does not do.

## Verdicts

Emit one finding per checked claim, using exactly these kinds:

- `CONFIRMED` -- the port matches upstream, or the prose claim is substantiated.
- `WRONG` -- a counterpart exists and the port disagrees with it. State the
  correct form.
- `UNSUPPORTED` -- no counterpart could be found after searching, AND the claim
  purports to describe one. The claim rests on nothing.
- `BRIDGE` -- no counterpart by construction, per method step 6. Set
  `declared_at` to the specification-header line declaring the degradation and its
  direction of bias, or `null` if no such declaration exists.

Do NOT use `UNSUPPORTED` for bridge code. Absence of a counterpart is that code's
expected property, not evidence against it.

Every finding MUST carry `settled_by`: the `path:line` in a checkout that settles
it. For `BRIDGE`, that is the target or engine line establishing what the code can
actually observe. A finding without `settled_by` is not a finding.

Be specific about consequence. "Units are wrong" is not useful; "the divisor
under-counts by 100x, collapsing the term that carries hardware heterogeneity" is.

## Output

Write JSON to `{VERDICT_PATH}`:

```json
{
  "arm": "{ARM_NAME}",
  "findings": [
    {
      "kind": "WRONG",
      "file": "algorithms/example.go",
      "line": 335,
      "symbol": "enclosingFunctionName",
      "claim": "what the specification says or computes",
      "upstream": "sim/example.go:168",
      "settled_by": "vllm/v1/metrics/loggers.py:563",
      "declared_at": null,
      "detail": "what is wrong and what the correct form is, with consequence"
    }
  ]
}
```

When done, send one message to `{MAIN_SESSION_NAME}`:
`audit-complete: {ARM_NAME} <n> CONFIRMED, <n> WRONG, <n> UNSUPPORTED`

Do not edit any file other than `{VERDICT_PATH}`. Fixing is not your job.
````

- [ ] **Step 2: Verify the prompt's placeholders are extractable**

Run: `grep -o '{[A-Z_]*}' .claude/skills/sim2real-specify/prompts/audit.md | sort -u`
Expected: exactly `{ARM_FILE}`, `{ARM_NAME}`, `{BUNDLE_ROOT}`, `{MAIN_SESSION_NAME}`, `{SIM_PIN}`, `{SIM_TREE}`, `{TARGET_PIN}`, `{TARGET_TREE}`, `{VERDICT_PATH}` — nine names, and no stray uppercase braces from the JSON example.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-specify/prompts/audit.md
git commit -m "feat(specify): audit prompt with correspondence discovery"
```

---

### Task 4: Re-derivation prompt

Blind re-derivation of the focal arm. Justified by anchoring: an auditor reading an existing port rationalizes it. The `chunk = ChunkTokens` defect hid precisely because the derived chunk count is algebraically identical either way — a class a blind writer surfaces and a reviewer talks itself out of. It is also the only component that can find a term omitted *and* undeclared, since there is no claim to audit.

**Files:**
- Create: `.claude/skills/sim2real-specify/prompts/rederive.md`

**Interfaces:**
- Consumes: nothing at runtime.
- Produces: placeholders `{SIM_TREE}`, `{SIM_PIN}`, `{POLICY_ENTRY_POINTS}`, `{ARM_NAME}`, `{DERIVATION_PATH}`, `{MAIN_SESSION_NAME}`. Writes JSON `{"arm": str, "terms": [{"name": str, "expression": str, "upstream": str, "units": str, "notes": str}]}` to `{DERIVATION_PATH}`.

- [ ] **Step 1: Write the prompt**

Create `.claude/skills/sim2real-specify/prompts/rederive.md`:

````markdown
# Blind re-derivation

Derive an algorithm's decision rule from simulation source alone. You will NOT be
shown any existing port, and you must not look for one. If you encounter a file
that appears to be a port of this policy, stop reading it and continue from the
simulation source.

## Inputs

- Simulation checkout: `{SIM_TREE}` pinned at `{SIM_PIN}`
- Entry points for the policy: `{POLICY_ENTRY_POINTS}`
- Arm name: `{ARM_NAME}`
- Write your derivation to: `{DERIVATION_PATH}`

## Method

Read the entry points and follow every call they make that contributes to the
decision. Then write down, as a flat list of named terms, the complete
computation: what is summed, multiplied, compared, rounded, skipped, and returned.

For each term record:

- `name` -- a short identifier you choose
- `expression` -- the algebra, in terms of other names you have defined
- `upstream` -- the `path:line` in `{SIM_TREE}` it comes from
- `units` -- microseconds, tokens, requests, dimensionless
- `notes` -- anything a reimplementation would get wrong

Rules that matter for this comparison:

1. Be exhaustive about ADDITIVE terms. If the upstream sums four contributions,
   list four. Omission is the defect class this exercise exists to catch.
2. Record every rounding operation as its own note. Floor, ceil, and truncate are
   not interchangeable.
3. Record any quantity the upstream recomputes at each call site, and say what it
   is clamped to. Do not summarize it as a constant.
4. Record guards that skip a term -- censoring, sentinel values, early
   `continue` -- and what the skipped contribution would have been.
5. Where the upstream selects between branches by a flag, follow the branch this
   policy actually forces and say which flag forces it. Ignore unreachable
   branches, and note that you ignored them.
6. Note every quantity that comes from simulator-internal state with no
   real-cluster analogue. Do not invent a substitute.

## Output

Write JSON to `{DERIVATION_PATH}`:

```json
{
  "arm": "{ARM_NAME}",
  "terms": [
    {
      "name": "decodeIterationTime",
      "expression": "alpha + c0*batchSize + c1*kvTokens + cPf*prefillTokens",
      "upstream": "sim/example_coeffs.go:41",
      "units": "microseconds",
      "notes": "recomputed per candidate under that candidate's own coefficients"
    }
  ]
}
```

When done, send one message to `{MAIN_SESSION_NAME}`:
`rederive-complete: {ARM_NAME} <n> terms`

Do not edit any file other than `{DERIVATION_PATH}`.
````

- [ ] **Step 2: Verify the prompt's placeholders are extractable**

Run: `grep -o '{[A-Z_]*}' .claude/skills/sim2real-specify/prompts/rederive.md | sort -u`
Expected: exactly `{ARM_NAME}`, `{DERIVATION_PATH}`, `{MAIN_SESSION_NAME}`, `{POLICY_ENTRY_POINTS}`, `{SIM_PIN}`, `{SIM_TREE}` — six names.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-specify/prompts/rederive.md
git commit -m "feat(specify): blind re-derivation prompt for the focal arm"
```

---

### Task 5: SKILL.md

**Files:**
- Create: `.claude/skills/sim2real-specify/SKILL.md`

**Interfaces:**
- Consumes: `prompts/audit.md` and `prompts/rederive.md` placeholder sets from Tasks 3–4; `scripts/lint_citations.py` CLI from Task 2.
- Produces: the substitution table that Task 6's test parses — a Markdown section titled exactly `## Placeholder substitution` containing one list item per placeholder in the form `` - `{NAME}` → ... ``.

- [ ] **Step 1: Write SKILL.md**

Create `.claude/skills/sim2real-specify/SKILL.md`:

````markdown
---
name: sim2real-specify
description: |
  Turn arbitrary upstream provenance (a BLIS commit, a Nous campaign, a paper
  plus code) into the canonical sim2real bundle inputs: algorithms/<arm>.go as a
  cited specification layer, README.md, config.md, workloads/, inputs/, and
  sim_results/ with checksums. Establishes the five properties that make an
  algorithm worth transferring -- causal mechanism, required structural shape,
  observability, isolation, and named threats to validity -- then gates the
  specification with an independent citation audit, a blind re-derivation of the
  focal arm, and a mechanical citation lint. Run BEFORE /sim2real-bootstrap.
argument-hint: "<experiment-root> [--focal ARM]"
user-invocable: true
allowed-tools:
  - Agent
  - SendMessage
  - TaskCreate
  - TaskUpdate
  - TaskList
  - AskUserQuestion
  - Bash(python3 *)
  - Bash(.venv/bin/python *)
  - Bash(git *)
  - Bash(test *)
  - Bash(shasum *)
  - Bash(cp *)
  - Bash(mkdir *)
  - Glob
  - Grep
  - Read
  - Write
  - Edit
---

# sim2real-specify

Produce a bundle whose algorithm specification is worth transferring. This runs
before `/sim2real-bootstrap`, which consumes `algorithms/` and `workloads/` as
pre-existing inputs.

The product is a MEANINGFUL algorithm specification. Correctness of the algebra is
necessary but not sufficient; an algorithm that is unimplementable on the target,
or indistinguishable from the baseline, is not worth transferring even when its
arithmetic is perfect.

## What this skill does NOT do

- Does not create `transfer.yaml` or `baselines/` — `/sim2real-bootstrap`.
- Does not wire submodules — bootstrap Task 1 derives the ref, Task 2 adds it.
- Does not produce compiling code — `/sim2real-translate`. The specification layer
  is non-compiling by design and its adapters stay declared unknowns.
- Does not build images or touch cluster config.
- Does not emit a limitations register or signal reference document. Degradations
  are declared in the specification layer's own header.

## Output contract

Produce exactly these paths under `<experiment-root>`:

| path | authored or copied |
|---|---|
| `algorithms/<arm>.go` | authored |
| `README.md` | authored |
| `config.md` | authored |
| `workloads/` | copied verbatim |
| `inputs/` | copied verbatim |
| `sim_results/` + `CHECKSUMS.sha256` | copied verbatim |
| `.gitignore` | generated |

Do NOT create `LIMITATIONS.md`, `docs/`, or `scripts/`.

**Verbatim-copy principle.** Anything that already exists upstream is copied, never
re-expressed: workloads, coefficients, results, patches. Re-authoring is reserved
for the specification layer, the one artifact that must change form. Re-authored
algebra is where defects come from; copied artifacts have never been a defect
source.

## Phase 0 — Ground

Obtain and verify two readable checkouts. Ask the operator for whichever is not
already present:

- the simulation source at a specific commit;
- the target component at a specific ref.

Record both pins. Every later claim is a citation against these two trees.

HALT if either is unreadable at its stated pin. Say which one and stop — nothing
downstream can be substantiated.

## Phase 1 — Mechanism

Establish, by interview plus source reading:

- the decision rule, and where it lives in the simulation source;
- the CAUSAL story: not that it wins, but why. Name the quantity that differs
  between conditions and the mechanism that exploits it.
- the cited evidence that it wins: which cells, what margin, against what
  baseline.

Copy the results artifacts verbatim into `sim_results/` and write
`CHECKSUMS.sha256` over them with `shasum -a 256`.

HALT if the results do not match their own checksums, or if the commit that
produced them is not the pin from Phase 0. A disagreement here invalidates every
cited margin.

## Phase 2 — Shape

Determine the structural form the decision takes: per-endpoint score, joint argmin
over a cross product, admission gate, dispatch ceiling, or something else. Then
read the target checkout and find an extension point that can express it.

Compare against the target's NATURAL decomposition. Where the natural
decomposition is weaker, quantify what would be lost, using the simulation's own
ablation rather than an estimate.

HALT if no extension point can express the required shape. The hazard is silent
fallback to the weaker natural decomposition, which transfers a different
algorithm while resembling success. Say so loudly instead.

## Phase 3 — Observability

Enumerate every quantity the decision rule reads. Classify each:

| status | meaning |
|---|---|
| direct | an existing target or engine value with the same definition |
| derivable | computable from existing values; record the derivation |
| degraded | an approximation is available; record the direction of bias |
| unobtainable | no real signal exists |

Every row carries a citation to the value that supplies it. This is a
classification pass, not a reference document — keep it to a table.

If a quantity carrying the CORE mechanism is `unobtainable`, stop and put the case
to the operator with `AskUserQuestion`. Degraded may be acceptable with reasoning,
but that judgment is the operator's, not yours.

## Phase 4 — Isolation

Identify the comparator arms and ablations that isolate the mechanism, each with
cited simulation evidence. A comparator that shares the machinery and differs only
in the objective is worth more than a weaker baseline, because it attributes the
effect to the mechanism rather than to the machinery.

If nothing isolates the mechanism, say so: the transfer may proceed but its
results will not be attributable.

## Phase 5 — Specify

Write `algorithms/<arm>.go` per arm, plus `config.md`.

The specification layer:

- states the policy against REAL target interface names;
- leaves adapters as declared unknowns rather than guesses, so a wrong guess fails
  loudly rather than mis-scoring silently;
- carries an upstream `path:line` citation on every non-obvious expression;
- declares each Phase 3 degradation in its header WITH the direction of bias;
- invents no in-source marker conventions.

One rule governs references to other pipeline stages:

> A specification may REQUEST things of downstream stages. It may never ASSERT
> what they do.

"Confirm X against the pinned checkout" is a legitimate request. "Stage Y resolves
X" asserts another component's behavior and is forbidden unless that component's
source says so.

`README.md` states the mechanism, the required shape, the honest status including
where the policy did NOT win, the pre-registered expectation, the pins table, and
the layout. If Phase 1 found no results, write the expectation as a hypothesis with
no margin attached and say that no measurement backs it.

## Phase 6 — Gate

### Placeholder substitution

Read `prompts/audit.md` and `prompts/rederive.md` and substitute every
`{PLACEHOLDER}`:

- `{BUNDLE_ROOT}` → the absolute experiment root
- `{ARM_FILE}` → absolute path to the arm under audit
- `{ARM_NAME}` → the arm's name, matching its filename stem
- `{SIM_TREE}` → absolute path to the simulation checkout
- `{SIM_PIN}` → the simulation commit recorded in Phase 0
- `{TARGET_TREE}` → absolute path to the target checkout
- `{TARGET_PIN}` → the target ref recorded in Phase 0
- `{VERDICT_PATH}` → `/tmp/specify-verdict-<arm>.json`
- `{POLICY_ENTRY_POINTS}` → newline-separated `path:symbol` entry points from Phase 1
- `{DERIVATION_PATH}` → `/tmp/specify-derivation-<arm>.json`
- `{MAIN_SESSION_NAME}` → `"main"`

### Run the three components

1. **Citation audit, every arm.** Spawn one agent per arm in a single tool-call
   message: `Agent(name="audit-<arm>", run_in_background=true,
   subagent_type=general-purpose, model="opus", prompt=<substituted audit.md>)`.
2. **Blind re-derivation, focal arm only.** Spawn
   `Agent(name="rederive", run_in_background=true, subagent_type=general-purpose,
   model="opus", prompt=<substituted rederive.md>)`. Scoped to the focal arm
   because that is where the algebra carrying the claim lives.
3. **Citation lint.** Run:

   ```bash
   .venv/bin/python .claude/skills/sim2real-specify/scripts/lint_citations.py \
     --bundle <experiment-root> --tree <sim-tree> --tree <target-tree>
   ```

### Reconcile

- Every `WRONG` finding: fix the specification. Cite the `settled_by` path in the
  fix.
- Every `UNSUPPORTED` finding: delete the claim, or convert it to a stated open
  question in `README.md` owned by the bundle.
- Every `BRIDGE` finding with `declared_at: null`: write the declaration into the
  specification header, naming the degradation AND the direction of the resulting
  bias. Do NOT delete the code — absence of a simulation counterpart is that
  code's expected property. This is prose in the header; do not introduce a marker
  convention for it.
- Every `BRIDGE` finding whose assumptions the auditor found wrong against the
  target or engine checkout: fix as for `WRONG`.
- Every re-derivation term absent from the specification: either add it, or
  declare its omission in the header with the direction of bias. A silent omission
  is a defect even when the omission is defensible.
- Every lint failure: correct the citation, or add `lint-skip` on that line if the
  reference is illustrative rather than a citation.

### Pass condition

Zero `UNSUPPORTED` claims remain, every `BRIDGE` finding has a non-null
`declared_at`, the lint exits 0, and every re-derivation divergence is resolved or
declared.

If `UNSUPPORTED` findings survive three fix rounds, STOP. Report what remains
unresolved. Do not describe the bundle as audited.

## After specify

Tell the operator:

```
Specification complete at <experiment-root>. Gate: <n> arms audited,
<n> re-derivation divergences declared, lint clean.

Next: /sim2real-bootstrap <experiment-root>
```
````

- [ ] **Step 2: Verify the frontmatter parses**

Run:
```bash
.venv/bin/python -c "
import re, sys, pathlib
t = pathlib.Path('.claude/skills/sim2real-specify/SKILL.md').read_text()
m = re.match(r'^---\n(.*?)\n---\n', t, re.S)
assert m, 'no frontmatter'
import yaml; fm = yaml.safe_load(m.group(1))
assert fm['name'] == 'sim2real-specify', fm['name']
assert fm['user-invocable'] is True
print('frontmatter ok:', list(fm))
"
```
Expected: `frontmatter ok: ['name', 'description', 'argument-hint', 'user-invocable', 'allowed-tools']`

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-specify/SKILL.md
git commit -m "feat(specify): SKILL.md with phases 0-6 and the gate"
```

---

### Task 6: Placeholder-consistency test

A prompt placeholder that SKILL.md forgets to substitute reaches the agent as a literal `{BRACE}` string, which the agent then treats as a path. This is silent and has bitten the translate skill before. Make it mechanical.

**Files:**
- Create: `.claude/skills/sim2real-specify/tests/test_skill_substitution.py`

**Interfaces:**
- Consumes: `SKILL.md`'s `## Placeholder substitution` section and the two prompt files.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

Create `.claude/skills/sim2real-specify/tests/test_skill_substitution.py`:

```python
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def _documented() -> set[str]:
    text = (SKILL_DIR / "SKILL.md").read_text()
    start = text.index("### Placeholder substitution")
    section = text[start : text.index("### Run the three components", start)]
    return set(PLACEHOLDER.findall(section))


def _used() -> dict[str, set[str]]:
    return {
        p.name: set(PLACEHOLDER.findall(p.read_text()))
        for p in sorted((SKILL_DIR / "prompts").glob("*.md"))
    }


def test_every_prompt_placeholder_is_documented():
    documented = _documented()
    for name, used in _used().items():
        missing = used - documented
        assert not missing, f"{name} uses undocumented placeholders: {sorted(missing)}"


def test_no_documented_placeholder_is_unused():
    all_used = set().union(*_used().values())
    unused = _documented() - all_used
    assert not unused, f"SKILL.md documents unused placeholders: {sorted(unused)}"


def test_expected_placeholder_sets():
    used = _used()
    assert used["audit.md"] == {
        "ARM_FILE", "ARM_NAME", "BUNDLE_ROOT", "MAIN_SESSION_NAME",
        "SIM_PIN", "SIM_TREE", "TARGET_PIN", "TARGET_TREE", "VERDICT_PATH",
    }
    assert used["rederive.md"] == {
        "ARM_NAME", "DERIVATION_PATH", "MAIN_SESSION_NAME",
        "POLICY_ENTRY_POINTS", "SIM_PIN", "SIM_TREE",
    }
```

- [ ] **Step 2: Run test to verify it passes or reveals a real gap**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_skill_substitution.py -v`
Expected: PASS — 3 passed. If it fails, the failure is real: SKILL.md's substitution list and the prompts disagree. Fix SKILL.md's list, not the test.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/sim2real-specify/tests/test_skill_substitution.py
git commit -m "test(specify): enforce prompt/SKILL.md placeholder agreement"
```

---

### Task 7: Labeled-fixture acceptance harness

Score the audit against ground truth established independently and by hand in `pd-infocomm` commit `ead56ea`. Sensitivity: four known defects in the focal arm at `08203b4`. Specificity: two comparator arms at the same commit that `ead56ea` found clean and left unchanged.

**Files:**
- Create: `.claude/skills/sim2real-specify/tests/fixtures/ead56ea_labels.json`
- Create: `.claude/skills/sim2real-specify/scripts/acceptance.py`
- Test: `.claude/skills/sim2real-specify/tests/test_acceptance.py`

**Interfaces:**
- Consumes: `Failure`-free; independent of `lint_citations.py`.
- Produces: `load_labels(path: Path) -> list[dict]`; `match(finding: dict, label: dict, tolerance: int = 6) -> bool`; `score(verdicts: list[dict], labels: list[dict]) -> dict` returning `{"found": [ids], "missed": [ids], "false_positive_arms": {arm: count}, "recall": float}`; `undeclared_bridges(verdicts: list[dict]) -> list[dict]`; `materialize(repo: Path, commit: str, dest: Path) -> None`; `main(argv) -> int`. `BRIDGE` is neither a defect nor a false positive; it is gated separately by `undeclared_bridges`.

- [ ] **Step 1: Write the labels fixture**

Create `.claude/skills/sim2real-specify/tests/fixtures/ead56ea_labels.json`. Line numbers are in `algorithms/causal_slo_externality.go` at `08203b4`, verified against that blob:

```json
{
  "fixture": {
    "repo_hint": "pd-infocomm",
    "commit": "08203b4",
    "focal_arm": "causal_slo_externality",
    "clean_arms": ["least_ttft_joint", "kairos_paper"],
    "authority": "pd-infocomm commit ead56ea"
  },
  "labels": [
    {
      "id": "missing-prefill-attention-charge",
      "file": "algorithms/causal_slo_externality.go",
      "lines": [483],
      "symbol": "cLocalAfter",
      "found_by": ["audit", "rederive"],
      "detail": "overlap*tIterOverlap replaces the baseline rate plus the arrival's exact marginal prefill work; the causal prefill-attention term is absent. Upstream charges it in both branches of varReTiming.cLocalAfter and the focal arm force-selects the exact one."
    },
    {
      "id": "kv-usage-fraction-not-percent",
      "file": "algorithms/causal_slo_externality.go",
      "lines": [335],
      "symbol": "kvTokensFor",
      "found_by": ["audit"],
      "detail": "spurious /100.0 -- KVCacheUsagePercent is a fraction in [0,1] despite its name, so KV is under-counted 100x, collapsing the C1*KV term that carries hardware heterogeneity."
    },
    {
      "id": "admission-steps-not-ceiled",
      "file": "algorithms/causal_slo_externality.go",
      "lines": [567, 589],
      "symbol": "Score",
      "found_by": ["audit", "rederive"],
      "detail": "admissionSteps and arrivalSteps must be math.Ceil -- admission lands on an iteration boundary, so the wait is a whole number of baseline decode steps."
    },
    {
      "id": "chunk-not-clamped-to-uncached-suffix",
      "file": "algorithms/causal_slo_externality.go",
      "lines": [718],
      "symbol": "chunkTerms",
      "found_by": ["audit", "rederive"],
      "detail": "chunk = ChunkTokens where upstream recomputes min(ap, ChunkTokens) at every call site. Hides because nChunks is algebraically identical either way; only the token count differs."
    },
    {
      "id": "undeclared-colloc-prefill-omission",
      "file": "algorithms/causal_slo_externality.go",
      "lines": [500],
      "symbol": "externality",
      "found_by": ["rederive"],
      "detail": "upstream externality is a three-way breakdown and the focal arm enables all three; this port returns only the decode component and declares neither omission. Findable only by re-derivation -- there is no claim to audit."
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `.claude/skills/sim2real-specify/tests/test_acceptance.py`:

```python
import importlib.util
import json
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
LABELS = SKILL / "tests" / "fixtures" / "ead56ea_labels.json"


def _load():
    spec = importlib.util.spec_from_file_location(
        "acceptance", SKILL / "scripts" / "acceptance.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

acc = _load()


def test_labels_fixture_is_wellformed():
    data = json.loads(LABELS.read_text())
    assert data["fixture"]["commit"] == "08203b4"
    ids = [l["id"] for l in data["labels"]]
    assert len(ids) == len(set(ids))
    audit_findable = [l for l in data["labels"] if "audit" in l["found_by"]]
    assert len(audit_findable) == 4, "ead56ea established four audit-findable defects"


def test_match_by_line_within_tolerance():
    label = {"file": "a.go", "lines": [483], "symbol": "cLocalAfter"}
    assert acc.match({"file": "a.go", "line": 485, "symbol": "other"}, label)
    assert not acc.match({"file": "a.go", "line": 600, "symbol": "other"}, label)


def test_match_by_symbol_when_line_drifts():
    label = {"file": "a.go", "lines": [483], "symbol": "cLocalAfter"}
    assert acc.match({"file": "a.go", "line": 999, "symbol": "cLocalAfter"}, label)


def test_score_reports_recall_and_false_positives():
    labels = acc.load_labels(LABELS)
    audit_labels = [l for l in labels if "audit" in l["found_by"]]
    verdicts = [
        {"arm": "causal_slo_externality", "findings": [
            {"kind": "WRONG", "file": "algorithms/causal_slo_externality.go",
             "line": 335, "symbol": "kvTokensFor"},
            {"kind": "WRONG", "file": "algorithms/causal_slo_externality.go",
             "line": 483, "symbol": "cLocalAfter"},
        ]},
        {"arm": "least_ttft_joint", "findings": [
            {"kind": "WRONG", "file": "algorithms/least_ttft_joint.go",
             "line": 12, "symbol": "somethingElse"},
        ]},
    ]
    result = acc.score(verdicts, audit_labels)
    assert set(result["found"]) == {
        "kv-usage-fraction-not-percent", "missing-prefill-attention-charge"}
    assert set(result["missed"]) == {
        "admission-steps-not-ceiled", "chunk-not-clamped-to-uncached-suffix"}
    assert result["recall"] == 0.5
    assert result["false_positive_arms"] == {"least_ttft_joint": 1}


def test_confirmed_and_bridge_are_not_counted_as_false_positives():
    labels = [l for l in acc.load_labels(LABELS) if "audit" in l["found_by"]]
    verdicts = [{"arm": "kairos_paper", "findings": [
        {"kind": "CONFIRMED", "file": "algorithms/kairos_paper.go",
         "line": 5, "symbol": "x"},
        {"kind": "BRIDGE", "file": "algorithms/kairos_paper.go",
         "line": 9, "symbol": "sPfFor", "declared_at": "algorithms/kairos_paper.go:44"},
    ]}]
    assert acc.score(verdicts, labels)["false_positive_arms"] == {}


def test_undeclared_bridge_findings_are_reported():
    verdicts = [{"arm": "causal_slo_externality", "findings": [
        {"kind": "BRIDGE", "file": "algorithms/causal_slo_externality.go",
         "line": 362, "symbol": "sPfFor", "declared_at": None},
        {"kind": "BRIDGE", "file": "algorithms/causal_slo_externality.go",
         "line": 231, "symbol": "residentTable",
         "declared_at": "algorithms/causal_slo_externality.go:48"},
        {"kind": "WRONG", "file": "algorithms/causal_slo_externality.go",
         "line": 335, "symbol": "kvTokensFor"},
    ]}]
    undeclared = acc.undeclared_bridges(verdicts)
    assert [f["symbol"] for f in undeclared] == ["sPfFor"]


def test_missing_declared_at_key_counts_as_undeclared():
    verdicts = [{"arm": "a", "findings": [
        {"kind": "BRIDGE", "file": "a.go", "line": 1, "symbol": "noKey"},
    ]}]
    assert [f["symbol"] for f in acc.undeclared_bridges(verdicts)] == ["noKey"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_acceptance.py -v`
Expected: FAIL — collection error, `scripts/acceptance.py` does not exist.

- [ ] **Step 4: Write minimal implementation**

Create `.claude/skills/sim2real-specify/scripts/acceptance.py`:

```python
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


def load_labels(path: Path) -> list[dict]:
    return json.loads(path.read_text())["labels"]


def match(finding: dict, label: dict, tolerance: int = 6) -> bool:
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
    return [f for v in verdicts for f in v["findings"]
            if f.get("kind") == "BRIDGE" and not f.get("declared_at")]


def score(verdicts: list[dict], labels: list[dict]) -> dict:
    # BRIDGE is deliberately excluded: it is neither a defect nor a false
    # positive, and is gated separately by undeclared_bridges().
    defects = [f for v in verdicts for f in v["findings"]
               if f.get("kind") in ("WRONG", "UNSUPPORTED")]
    found = [l["id"] for l in labels if any(match(f, l) for f in defects)]
    missed = [l["id"] for l in labels if l["id"] not in found]

    false_positives: dict[str, int] = {}
    for verdict in verdicts:
        arm = verdict["arm"]
        if arm not in ("least_ttft_joint", "kairos_paper"):
            continue
        count = sum(1 for f in verdict["findings"]
                    if f.get("kind") in ("WRONG", "UNSUPPORTED"))
        if count:
            false_positives[arm] = count

    return {
        "found": sorted(found),
        "missed": sorted(missed),
        "false_positive_arms": false_positives,
        "recall": (len(found) / len(labels)) if labels else 0.0,
    }


def materialize(repo: Path, commit: str, dest: Path) -> None:
    """Extract the three arm files at `commit` into `dest` as a bundle skeleton."""
    (dest / "algorithms").mkdir(parents=True, exist_ok=True)
    for rel in ARM_FILES.values():
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True, check=True, text=True,
        ).stdout
        (dest / rel).write_text(blob)
    for rel in ("README.md", "config.md"):
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True, check=True, text=True,
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

    labels = [l for l in load_labels(args.labels) if args.component in l["found_by"]]
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/tests/test_acceptance.py -v`
Expected: PASS — 7 passed.

- [ ] **Step 6: Run the whole skill's test suite**

Run: `.venv/bin/pytest .claude/skills/sim2real-specify/ -v`
Expected: PASS — 22 passed (5 + 7 from lint, 3 substitution, 7 acceptance).

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/sim2real-specify/scripts/acceptance.py \
        .claude/skills/sim2real-specify/tests/test_acceptance.py \
        .claude/skills/sim2real-specify/tests/fixtures/ead56ea_labels.json
git commit -m "test(specify): acceptance harness scored against the ead56ea fixture"
```

- [ ] **Step 8: Execute the acceptance run for real**

This step runs agents and is not automated. Materialize the fixture, run the audit prompt against it, and score.

```bash
.venv/bin/python .claude/skills/sim2real-specify/scripts/acceptance.py \
  materialize --repo ../pd-infocomm --commit 08203b4 --dest /tmp/specify-fixture
```

Then, for each of the three arms, substitute `prompts/audit.md` with
`{BUNDLE_ROOT}=/tmp/specify-fixture`, `{ARM_FILE}=/tmp/specify-fixture/algorithms/<arm>.go`,
`{SIM_TREE}=inference-sim`, `{SIM_PIN}=871b169b`,
`{TARGET_TREE}=../pd-infocomm/llm-d-router`, `{TARGET_PIN}=v0.9.0`,
`{VERDICT_PATH}=/tmp/specify-verdict-<arm>.json`, and spawn one background
`general-purpose` agent per arm on `model="opus"`. Then:

```bash
.venv/bin/python .claude/skills/sim2real-specify/scripts/acceptance.py \
  score --labels .claude/skills/sim2real-specify/tests/fixtures/ead56ea_labels.json \
  --verdict /tmp/specify-verdict-causal_slo_externality.json \
  --verdict /tmp/specify-verdict-least_ttft_joint.json \
  --verdict /tmp/specify-verdict-kairos_paper.json \
  --component audit
```

Expected: `recall` 1.0 with empty `missed` and empty `false_positive_arms`.

If recall is below 1.0, the audit prompt is too weak — strengthen the specific
`Method` clause corresponding to each missed label (for example, a missed
`chunk-not-clamped-to-uncached-suffix` means clause 5 on per-call-site recomputation
needs sharpening) and re-run. Do NOT weaken the labels. If `false_positive_arms`
is non-empty, the prompt is over-flagging; tighten the requirement that every
finding carry a `settled_by` line that actually contradicts the port.

Record the final scores in the commit message.

- [ ] **Step 9: Commit any prompt strengthening**

```bash
git add .claude/skills/sim2real-specify/prompts/audit.md
git commit -m "fix(specify): strengthen audit prompt to reach full recall on the fixture"
```

---

## Self-Review

**Spec coverage.** Five properties → SKILL.md Phases 1–5 (Task 5). Placement and non-goals → Task 5's "What this skill does NOT do". Output contract incl. no `LIMITATIONS.md` → Task 5. Verbatim-copy principle → Task 5. Request-vs-assert rule → Tasks 3 and 5. Gate's three components → Tasks 3, 4, 2 respectively, orchestrated in Task 5 Phase 6. Correspondence discovery → Task 3. Failure modes: HALTs → Phases 0–2; operator decisions → Phases 3–4 via `AskUserQuestion`; degraded output → Phase 5; gate non-convergence → Phase 6 three-round bound. Structure → File Structure section. Testing: sensitivity/specificity → Task 7; lint units → Tasks 1–2; lint on the citation-dense post-fix file → Task 2 Step 5; placeholder consistency → Task 6. Deferred items (`provenance.yaml`, bootstrap pin check) are correctly absent from all tasks.

**Known gap, deliberate.** The spec's acceptance criterion "regenerate the bundle from the same provenance and confirm the four defects never appear" is a full end-to-end run of an interview-driven skill. It is not scriptable and is not a task here; Task 7 Step 8 covers the gate half of it, which is where the four defects are caught. Run the end-to-end regeneration once manually after this plan completes.

**Type consistency.** `Citation(path, lines, source_line)` and `Failure(file, source_line, citation, kind, detail)` are used identically in Tasks 1–2. `lint_bundle(bundle, trees, exts)` matches its call in Task 2's tests and Task 5's CLI invocation. Verdict JSON keys (`arm`, `findings`, `kind`, `file`, `line`, `symbol`, `claim`, `upstream`, `settled_by`, `declared_at`, `detail`) are identical in Task 3's prompt, Task 7's scorer, and Task 7's tests. The four verdict kinds `CONFIRMED`/`WRONG`/`UNSUPPORTED`/`BRIDGE` appear consistently in Task 3's prompt, Task 5's reconcile rules and pass condition, and Task 7's `score` / `undeclared_bridges`. `found_by` values `audit`/`rederive` match `--component` choices. Placeholder sets in Task 6's `test_expected_placeholder_sets` match Tasks 3 and 4 exactly and Task 5's substitution list — the `BRIDGE` change adds no placeholders.

**BRIDGE coverage.** Spec's four-verdict table → Task 3 method step 6 and the verdict list. Spec's `declared_at` requirement → Task 3's schema, Task 5's reconcile rule, and Task 7's `undeclared_bridges` plus three tests. Spec's `kvTokensFor`-is-`WRONG`-not-`BRIDGE` boundary → Task 3 method step 6's PORTED clause, and the `kv-usage-fraction-not-percent` label remaining `found_by: ["audit"]` so the fixture still demands it be caught as a defect.
