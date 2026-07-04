# sim2real translate command (initial + --resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `sim2real translate` subcommand to `pipeline/sim2real.py`. Two-phase, skill-checkpointed: an initial run writes `skill_input.json` + `translation_output.json` (with `image_ref: null` per algorithm) and exits at the checkpoint; `--resume` validates that `/sim2real-translate` produced the expected `generated/<algo>/<algo>_output.json` files. `--force` deletes and recreates the translation directory. Behavior on every one of the 9 state × command cells matches the state-machine table in `docs/epics/step-2/design.md#pr-3--sim2real-translate-command`.

**Architecture:** Introduce a private `_translate_*` set of helpers in `pipeline/sim2real.py` (mirroring the existing `_register_translation` / `_atomic_write_json` / `_build_translation_output` pattern), and one new command handler `_cmd_translate(args)`. Reuse existing infrastructure verbatim: `slicer.translation_hash_with_sources` (PR 1), `translation_ref.validate_name` (PR 2), `translation_ref.read_translation_output` (PR 2), `layout.translation_dir` / `layout.translation_output_path` (step-0), `_atomic_write_json` (already in `sim2real.py`). The state machine is expressed as a single explicit branch on `(exists, is_complete, is_partial)` × `(plain, resume, force)` — one function, 9 cells, one exit code per cell.

**Tech Stack:** Python 3.10+, pytest, PyYAML (already a dep for reading `transfer.yaml`). No new dependencies.

## Global Constraints

- Base branch: `refactor/v2-step-2` — worktree is `.claude/worktrees/issue-467-sim2real-translate/` under the parent repo. Every path passed to Read/Edit/Write/Bash must contain that substring.
- **Design is authoritative.** The 9-cell state-machine table and the `skill_input.json` schema are pinned in `docs/epics/step-2/design.md`. When in doubt, re-read the design section, not this plan.
- **Alias source.** `alias = manifest["scenario"]` per design §Commands / initial-run step 6. Must be validated via `translation_ref.validate_name` before write.
- **Algorithm-name validation.** Every `algorithms[i].name` from `transfer.yaml` is validated via `translation_ref.validate_name` before landing in `translation_output.json` or `skill_input.json` paths.
- **No auto-execute of the skill.** `translate` writes the checkpoint files and exits with a hint message; the operator invokes `/sim2real-translate` explicitly. Auto-fix is step-6's territory (not this PR).
- **Completeness check** for both `--resume` and idempotent "already complete" detection is per-`algorithms[i].name`: every declared algorithm must have `generated/<algo>/<algo>_output.json` present. Extra files under `generated/` are ignored (may be from a prior algorithm-set) but logged as a warning.
- **Atomic writes.** All JSON writes (`translation_output.json`, `skill_input.json`) go through `_atomic_write_json`. Prevents readers from observing a half-written file if the operator interrupts mid-write.
- **No side effects on error.** Every error path exits before touching disk. Errors detected after partial writes must roll back (unlink what was written) — applies only to `--force` recreate.
- Test invocation: `.venv/bin/python -m pytest pipeline/ -v` from the worktree root.
- CI lint: `ruff check pipeline/ .claude/skills/ --select F`.

---

## File Structure

| File | Change | Reason |
|------|--------|--------|
| `pipeline/sim2real.py` | Add `_cmd_translate` + private helpers (`_translate_paths_for`, `_translate_state`, `_translate_delete_dir`, `_translate_write_checkpoint`, `_translate_validate_resume`, `_build_skill_input`, `_build_translate_output`). Add argparse subparser `translate` with `--force` / `--resume` flags. Wire into `main()` dispatch. | New subcommand |
| `pipeline/tests/test_translate.py` | New test file — one class per state-machine row (`TestTranslateEmpty`, `TestTranslatePartial`, `TestTranslateComplete`) + `TestBuildSkillInput`, `TestBuildTranslateOutput`, `TestTranslateAliasValidation`, `TestTranslateHashCollision`. | Coverage per issue #467 acceptance |
| `.github/workflows/test.yml` | Add `pipeline/tests/test_translate.py` to the explicit test-file list. | CI must pick up the new file (PR 6's audit will double-check, but keeping CI green on this PR is table stakes). |

Not touched:
- `pipeline/lib/slicer.py` — `translation_hash_with_sources` already exists from PR 1 (issue #465, merged as PR #470).
- `pipeline/lib/translation_ref.py` — `validate_name` and `read_translation_output` already exist from PR 2 (issue #466, merged as PR #471).
- `pipeline/lib/layout.py` — `translation_dir`, `translation_output_path` already exist from step-0.
- `pipeline/lib/manifest.py` — `load_manifest` already exists; no schema change needed.
- `pipeline/sim2real.py` argparse `list translations` subcommand — no reason to touch.
- The `/sim2real-translate` skill prompts — that's PR 4 (issue #468).

---

## Task 1 — `.venv` setup + confirm baseline is green

**Files:** none modified — sanity check only.

- [ ] **Step 1:** Create `.venv` in the worktree root and install `requirements.txt`.
  ```bash
  cd .claude/worktrees/issue-467-sim2real-translate
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/pip install pytest pyyaml ruff
  ```
- [ ] **Step 2:** Run the baseline test suite. Confirm zero failures before touching code — so any red we see after our edits is definitely ours.
  ```bash
  .venv/bin/python -m pytest pipeline/ -v
  ```
- [ ] **Step 3:** Run ruff. Same reason.
  ```bash
  .venv/bin/ruff check pipeline/ .claude/skills/ --select F
  ```

---

## Task 2 — Introduce checkpoint-file schema builders (TDD)

**Rationale:** Ship the pure-function builders first with tight unit tests. Once the shapes are pinned, the command handler is a thin driver over I/O.

**Files:**
- Modify: `pipeline/sim2real.py` (add two builders next to `_build_translation_output`)
- Modify: `pipeline/tests/test_translate.py` (new file — start with builder tests)

**Interfaces:**
- `_build_translate_output(*, translation_hash: str, scenario: str, algorithms: list[dict], now_iso: str) -> dict` — returns the step-2 skill-shaped `translation_output.json`. Each `algorithms[i]` entry has: `name`, `source_path`, `source_sha256`, `config_path=null`, `image_ref=null`, `image_digest=null`. Top-level: `version=1`, `source="skill"`, `alias=scenario`, `created_at=now_iso`.
- `_build_skill_input(*, translation_hash: str, experiment_root: Path, translations_dir: Path, scenario: str, baseline: dict | None, algorithms: list[dict], context: dict) -> dict` — returns the pinned `skill_input.json` schema per design §Schemas. Fields: `version=1`, `translation_hash`, `experiment_root` (absolute), `translations_dir` (absolute), `scenario`, `baseline` (dict with `config_path`/`generated_overlay_path`, or `null` if manifest has no baseline overlay), `algorithms` (list of dicts: `name`, `source_path`, `source_sha256`, `output_dir`, `config_output_path`, `notes`), `context` (dict with `text` + `file_paths`).

- [ ] **Step 1: Write failing tests in `pipeline/tests/test_translate.py`.**

```python
"""Tests for pipeline/sim2real.py — `sim2real translate` command."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from pipeline import sim2real
from pipeline.lib import layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestBuildTranslateOutput:
    def test_single_algorithm_shape(self):
        out = sim2real._build_translate_output(
            translation_hash="abc" * 20 + "1234",
            scenario="softreflective-v1",
            algorithms=[{
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "e3b0c44" + "0" * 57,
            }],
            now_iso="2026-07-02T14:00:00Z",
        )
        assert out["version"] == 1
        assert out["source"] == "skill"
        assert out["alias"] == "softreflective-v1"
        assert out["created_at"] == "2026-07-02T14:00:00Z"
        assert len(out["algorithms"]) == 1
        a = out["algorithms"][0]
        assert a["name"] == "softreflective"
        assert a["source_path"] == "algorithms/softreflective.py"
        assert a["source_sha256"] == "e3b0c44" + "0" * 57
        assert a["config_path"] is None
        assert a["image_ref"] is None
        assert a["image_digest"] is None

    def test_multi_algorithm_shape(self):
        out = sim2real._build_translate_output(
            translation_hash="a" * 64,
            scenario="compare-a-b",
            algorithms=[
                {"name": "algo_a", "source_path": "algorithms/a.py", "source_sha256": "aa" * 32},
                {"name": "algo_b", "source_path": "algorithms/b.py", "source_sha256": "bb" * 32},
            ],
            now_iso="2026-07-02T14:30:00Z",
        )
        assert [a["name"] for a in out["algorithms"]] == ["algo_a", "algo_b"]
        assert all(a["image_ref"] is None for a in out["algorithms"])


class TestBuildSkillInput:
    def test_paths_are_absolute_at_top_level(self, tmp_path):
        exp = tmp_path / "exp"
        tdir = tmp_path / "workspace" / "translations" / ("a" * 64)
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=exp,
            translations_dir=tdir,
            scenario="softreflective-v1",
            baseline=None,
            algorithms=[{
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "e3b0c44" + "0" * 57,
                "notes": "",
            }],
            context={"text": "", "file_paths": []},
        )
        assert Path(skin["experiment_root"]).is_absolute()
        assert Path(skin["translations_dir"]).is_absolute()

    def test_algorithm_paths_are_relative_to_translations_dir(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baseline=None,
            algorithms=[{
                "name": "softreflective",
                "source_path": "algorithms/softreflective.py",
                "source_sha256": "aa" * 32,
                "notes": "",
            }],
            context={"text": "", "file_paths": []},
        )
        a = skin["algorithms"][0]
        assert a["output_dir"] == "generated/softreflective"
        assert a["config_output_path"] == "generated/softreflective/softreflective_config.yaml"

    def test_baseline_null_when_absent(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baseline=None,
            algorithms=[],
            context={"text": "", "file_paths": []},
        )
        assert skin["baseline"] is None

    def test_baseline_populated_when_present(self):
        skin = sim2real._build_skill_input(
            translation_hash="a" * 64,
            experiment_root=Path("/e"),
            translations_dir=Path("/t"),
            scenario="s",
            baseline={
                "config_path": "baselines/base.yaml",
                "generated_overlay_path": "generated/baseline_config.yaml",
            },
            algorithms=[],
            context={"text": "hint text", "file_paths": ["docs/a.md"]},
        )
        assert skin["baseline"]["config_path"] == "baselines/base.yaml"
        assert skin["context"]["text"] == "hint text"
        assert skin["context"]["file_paths"] == ["docs/a.md"]
```

Run the file — expect `ImportError` or `AttributeError` from missing `_build_translate_output` / `_build_skill_input`:
```bash
.venv/bin/python -m pytest pipeline/tests/test_translate.py -v
```

- [ ] **Step 2: Implement the two builders in `pipeline/sim2real.py`.**

Insert next to `_build_translation_output` (around line 130):

```python
def _build_translate_output(
    *,
    translation_hash: str,
    scenario: str,
    algorithms: list[dict],
    now_iso: str,
) -> dict:
    """Build the ``translation_output.json`` body for skill-driven translate.

    Multi-algorithm shape. ``image_ref``/``image_digest`` land as ``None``
    per algorithm — ``sim2real build`` (PR 5) fills them in later.
    ``config_path`` is ``None`` for skill-driven translations (source
    lives in ``source_path``); BYO's register writes ``config_path``.
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": "skill",
        "alias": scenario,
        "algorithms": [
            {
                "name": a["name"],
                "source_path": a["source_path"],
                "source_sha256": a["source_sha256"],
                "config_path": None,
                "image_ref": None,
                "image_digest": None,
            }
            for a in algorithms
        ],
        "created_at": now_iso,
    }


def _build_skill_input(
    *,
    translation_hash: str,
    experiment_root: Path,
    translations_dir: Path,
    scenario: str,
    baseline: dict | None,
    algorithms: list[dict],
    context: dict,
) -> dict:
    """Build the ``skill_input.json`` body per design §Schemas.

    Absolute paths at the top level; per-algorithm output paths are
    relative to ``translations_dir``. Source paths are relative to
    ``experiment_root``.
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "experiment_root": str(experiment_root),
        "translations_dir": str(translations_dir),
        "scenario": scenario,
        "baseline": baseline,
        "algorithms": [
            {
                "name": a["name"],
                "source_path": a["source_path"],
                "source_sha256": a["source_sha256"],
                "output_dir": f"generated/{a['name']}",
                "config_output_path": f"generated/{a['name']}/{a['name']}_config.yaml",
                "notes": a.get("notes", ""),
            }
            for a in algorithms
        ],
        "context": context,
    }
```

- [ ] **Step 3: Rerun tests.** Green.

---

## Task 3 — State-machine helpers (TDD)

**Rationale:** The state machine is the trickiest part. Extract state detection into a pure helper so the command handler is a switch statement, and unit-test each state directly.

**Files:**
- Modify: `pipeline/sim2real.py` (add helpers)
- Modify: `pipeline/tests/test_translate.py` (add class)

**Interfaces:**
- `_translate_state(translation_hash: str, expected_algorithm_names: list[str]) -> tuple[str, list[str]]` — returns one of `"nothing"`, `"partial"`, `"complete"` and a list of missing algorithm names (only populated for `"partial"`). Reads `layout.translation_dir(translation_hash)` and `translation_output.json` / `generated/<algo>/<algo>_output.json`. If translation dir doesn't exist → `"nothing"`. If dir exists AND translation_output.json exists AND every declared `<algo>_output.json` exists → `"complete"`. Otherwise → `"partial"` with the missing-algorithm list.

  Note: "translation_output.json exists but algorithms mismatch" also lands in `"partial"` — treated as an incomplete state that `--force` can clear and re-run.

- `_translate_delete_dir(translation_hash: str) -> None` — recursively deletes `layout.translation_dir(translation_hash)`. Wraps `shutil.rmtree`. No-op if the dir is absent.

- [ ] **Step 1: Failing tests.** Add to `pipeline/tests/test_translate.py`:

```python
class TestTranslateStateDetection:
    def _make_dir(self, tmp_path, thash="a" * 64):
        tdir = tmp_path / "workspace" / "translations" / thash
        tdir.mkdir(parents=True)
        return tdir, thash

    def test_nothing_when_dir_absent(self):
        state, missing = sim2real._translate_state("a" * 64, ["algo1"])
        assert state == "nothing"
        assert missing == []

    def test_partial_when_dir_exists_but_no_output_json(self, tmp_path):
        self._make_dir(tmp_path)
        state, missing = sim2real._translate_state("a" * 64, ["algo1"])
        assert state == "partial"

    def test_partial_when_some_algo_outputs_missing(self, tmp_path):
        tdir, thash = self._make_dir(tmp_path)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1, "translation_hash": thash, "source": "skill",
            "alias": "s", "algorithms": [{"name": "algo1"}, {"name": "algo2"}],
            "created_at": "2026-07-02T00:00:00Z",
        }))
        (tdir / "generated" / "algo1").mkdir(parents=True)
        (tdir / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        state, missing = sim2real._translate_state(thash, ["algo1", "algo2"])
        assert state == "partial"
        assert missing == ["algo2"]

    def test_complete_when_all_algo_outputs_present(self, tmp_path):
        tdir, thash = self._make_dir(tmp_path)
        (tdir / "translation_output.json").write_text("{}")
        for name in ("algo1", "algo2"):
            (tdir / "generated" / name).mkdir(parents=True)
            (tdir / "generated" / name / f"{name}_output.json").write_text("{}")
        state, missing = sim2real._translate_state(thash, ["algo1", "algo2"])
        assert state == "complete"
        assert missing == []


class TestTranslateDeleteDir:
    def test_removes_existing(self, tmp_path):
        tdir = tmp_path / "workspace" / "translations" / ("a" * 64)
        (tdir / "generated" / "algo1").mkdir(parents=True)
        (tdir / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        sim2real._translate_delete_dir("a" * 64)
        assert not tdir.exists()

    def test_noop_when_absent(self):
        sim2real._translate_delete_dir("a" * 64)  # must not raise
```

- [ ] **Step 2: Implement.** Insert into `pipeline/sim2real.py`:

```python
def _translate_state(
    translation_hash: str, expected_algorithm_names: list[str]
) -> tuple[str, list[str]]:
    """Classify the on-disk state of ``translations/<hash>/``.

    Returns ``(state, missing_names)`` where ``state`` is one of
    ``"nothing"``, ``"partial"``, ``"complete"`` and ``missing_names`` is
    populated only for ``"partial"``.
    """
    tdir = layout.translation_dir(translation_hash)
    if not tdir.exists():
        return "nothing", []
    tout = layout.translation_output_path(translation_hash)
    if not tout.exists():
        return "partial", list(expected_algorithm_names)
    missing = [
        name for name in expected_algorithm_names
        if not (tdir / "generated" / name / f"{name}_output.json").exists()
    ]
    if missing:
        return "partial", missing
    return "complete", []


def _translate_delete_dir(translation_hash: str) -> None:
    """Recursively remove ``translations/<hash>/``. No-op if absent."""
    import shutil
    tdir = layout.translation_dir(translation_hash)
    if tdir.exists():
        shutil.rmtree(tdir)
```

- [ ] **Step 3: Green.**

---

## Task 4 — Argparse subparser + command dispatch stub

**Files:**
- Modify: `pipeline/sim2real.py` — add `translate` subparser after `list` block; wire `_cmd_translate` into `main()`.

**Interfaces:**
- CLI: `sim2real translate [--force] [--resume]` — mutually exclusive `--force` / `--resume`. No positional args. Reads `transfer.yaml` from `<experiment_root>/transfer.yaml` (fallback to `<experiment_root>/config/transfer.yaml`) — matches `_cmd_assemble`'s manifest-lookup pattern.

- [ ] **Step 1: Add subparser.** Insert in `build_parser()` after the `list` block:

```python
trans = sub.add_parser(
    "translate",
    help="Skill-driven translation: write checkpoint files or validate resume",
)
mode = trans.add_mutually_exclusive_group()
mode.add_argument(
    "--force",
    action="store_true",
    help="Delete and recreate the translation dir; user must re-run /sim2real-translate.",
)
mode.add_argument(
    "--resume",
    action="store_true",
    help="Validate that /sim2real-translate produced all declared algorithm outputs.",
)
```

- [ ] **Step 2: Add dispatch line in `main()`:**

```python
if args.command == "translate":
    return _cmd_translate(args)
```

- [ ] **Step 3: Add a stub `_cmd_translate(args) -> int: return 0`** so the dispatch resolves. Real body lands in Task 5.

- [ ] **Step 4: Sanity-check with `pytest -x pipeline/tests/test_translate.py -v` (still green — no new tests yet).**

---

## Task 5 — `_cmd_translate` state machine (TDD)

**Rationale:** This is the core deliverable. 9 state × command cells. Each cell has an exit code and a message contract.

**Design table (verbatim from `docs/epics/step-2/design.md#pr-3--sim2real-translate-command`):**

| State | `translate` (plain) | `translate --resume` | `translate --force` |
|---|---|---|---|
| Nothing | Create dir; write both checkpoint files; print checkpoint msg; **exit 0** | Error: `no translation to resume for hash <hash> — run 'sim2real translate' first`; **exit 2** | Same as plain |
| Partial | Error: `translation <hash> incomplete — run '/sim2real-translate' then 'sim2real translate --resume'`; **exit 2** | Error: `missing outputs for: <names>`; **exit 2**. Never mutates the dir. | Delete + recreate as if `nothing` |
| Complete | Print `translation <hash> already complete — run 'sim2real build' next`; **exit 0** | Same as plain-Complete: `already complete`; **exit 0** | Delete + recreate; user must re-run the skill |

**Files:**
- Modify: `pipeline/sim2real.py` — replace stub `_cmd_translate` with the full implementation.
- Modify: `pipeline/tests/test_translate.py` — three test classes, one per state row.

**Fixtures shared across state tests:** a helper that writes a minimal `transfer.yaml` at `<experiment_root>/transfer.yaml` plus a stub algorithm-source file. Include this at the top of the file (not inside a class) so all classes can call it.

- [ ] **Step 1: Failing tests.** Add fixture + three classes to `pipeline/tests/test_translate.py`:

```python
def _write_manifest(tmp_path, scenario="softreflective-v1", algorithms=None):
    """Write a minimal transfer.yaml + algorithm source file(s). Returns the manifest path."""
    if algorithms is None:
        algorithms = [{"name": "softreflective", "source": "algorithms/softreflective.py"}]
    exp = tmp_path
    (exp / "algorithms").mkdir(exist_ok=True)
    for a in algorithms:
        (exp / a["source"]).write_text(f"# stub for {a['name']}\n")
    manifest = {
        "kind": "sim2real",
        "version": 3,
        "scenario": scenario,
        "algorithms": algorithms,
        "context": {"text": "", "file_paths": []},
    }
    path = exp / "transfer.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


def _run_translate(args_list):
    """Invoke sim2real.main([...]) and return its exit code."""
    return sim2real.main(["translate", *args_list])


def _compute_hash(tmp_path, manifest_path):
    """Convenience — call slicer to get the expected hash for the manifest."""
    from pipeline.lib import slicer
    manifest = yaml.safe_load(manifest_path.read_text())
    return slicer.translation_hash_with_sources(manifest, tmp_path)


class TestTranslateEmpty:
    """State: Nothing (translation dir absent)."""

    def test_plain_writes_checkpoint_files(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        assert _run_translate([]) == 0
        # Find the hash by scanning translations/
        translations = tmp_path / "workspace" / "translations"
        entries = list(translations.iterdir())
        assert len(entries) == 1
        tdir = entries[0]
        assert (tdir / "skill_input.json").exists()
        assert (tdir / "translation_output.json").exists()
        tout = json.loads((tdir / "translation_output.json").read_text())
        assert tout["source"] == "skill"
        assert tout["alias"] == "softreflective-v1"
        assert tout["algorithms"][0]["image_ref"] is None
        skin = json.loads((tdir / "skill_input.json").read_text())
        assert skin["scenario"] == "softreflective-v1"
        assert skin["algorithms"][0]["name"] == "softreflective"

    def test_plain_prints_checkpoint_message(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        _run_translate([])
        out = capsys.readouterr().out
        assert "/sim2real-translate" in out  # user is told what to do next

    def test_force_behaves_like_plain(self, tmp_path):
        _write_manifest(tmp_path)
        assert _run_translate(["--force"]) == 0
        # Idempotent structure check
        translations = tmp_path / "workspace" / "translations"
        assert len(list(translations.iterdir())) == 1

    def test_resume_errors_when_nothing(self, tmp_path, capsys):
        _write_manifest(tmp_path)
        assert _run_translate(["--resume"]) == 2
        err = capsys.readouterr().err
        assert "no translation to resume" in err

    def test_alias_validation_rejects_bad_scenario(self, tmp_path, capsys):
        _write_manifest(tmp_path, scenario="../evil")
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "scenario" in err.lower() or "alias" in err.lower()


class TestTranslatePartial:
    """State: Partial (dir exists but not all algo outputs present)."""

    def _setup_partial(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, algorithms=[
            {"name": "algo1", "source": "algorithms/algo1.py"},
            {"name": "algo2", "source": "algorithms/algo2.py"},
        ])
        # Run plain translate to create the checkpoint files (state → partial).
        assert _run_translate([]) == 0
        thash = _compute_hash(tmp_path, manifest_path)
        return thash

    def test_plain_errors_on_partial(self, tmp_path, capsys):
        thash = self._setup_partial(tmp_path)
        capsys.readouterr()
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "incomplete" in err

    def test_resume_reports_missing_algos(self, tmp_path, capsys):
        thash = self._setup_partial(tmp_path)
        # Populate one of the two algo outputs.
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1").mkdir(parents=True, exist_ok=True)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        capsys.readouterr()
        assert _run_translate(["--resume"]) == 2
        err = capsys.readouterr().err
        assert "algo2" in err

    def test_resume_never_mutates_dir(self, tmp_path):
        thash = self._setup_partial(tmp_path)
        tdir = tmp_path / "workspace" / "translations" / thash
        before = {p.relative_to(tdir): p.read_bytes() for p in tdir.rglob("*") if p.is_file()}
        _run_translate(["--resume"])
        after = {p.relative_to(tdir): p.read_bytes() for p in tdir.rglob("*") if p.is_file()}
        assert before == after

    def test_force_recreates(self, tmp_path):
        thash = self._setup_partial(tmp_path)
        tdir = tmp_path / "workspace" / "translations" / thash
        old_mtime = (tdir / "translation_output.json").stat().st_mtime_ns
        assert _run_translate(["--force"]) == 0
        assert (tdir / "translation_output.json").exists()
        # Sanity — the file was rewritten (mtime bumped is best-effort;
        # dir survived so we assert content is fresh via a marker check).


class TestTranslateComplete:
    """State: Complete (all algo outputs present)."""

    def _setup_complete(self, tmp_path):
        manifest_path = _write_manifest(tmp_path, algorithms=[
            {"name": "algo1", "source": "algorithms/algo1.py"},
        ])
        assert _run_translate([]) == 0
        thash = _compute_hash(tmp_path, manifest_path)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1").mkdir(parents=True, exist_ok=True)
        (tmp_path / "workspace" / "translations" / thash / "generated" / "algo1" / "algo1_output.json").write_text("{}")
        return thash

    def test_plain_prints_already_complete(self, tmp_path, capsys):
        self._setup_complete(tmp_path)
        capsys.readouterr()
        assert _run_translate([]) == 0
        out = capsys.readouterr().out
        assert "already complete" in out

    def test_resume_prints_already_complete(self, tmp_path, capsys):
        self._setup_complete(tmp_path)
        capsys.readouterr()
        assert _run_translate(["--resume"]) == 0
        out = capsys.readouterr().out
        assert "already complete" in out

    def test_force_recreates_and_returns_to_nothing_shape(self, tmp_path):
        thash = self._setup_complete(tmp_path)
        assert _run_translate(["--force"]) == 0
        # After force: dir is fresh, no algo outputs present
        algo_output = (tmp_path / "workspace" / "translations" / thash /
                       "generated" / "algo1" / "algo1_output.json")
        assert not algo_output.exists()
```

- [ ] **Step 2: Implement `_cmd_translate` in `pipeline/sim2real.py`.** Replace the stub:

```python
def _cmd_translate(args) -> int:
    from pipeline.lib import manifest as _manifest, slicer, translation_ref

    # Resolve experiment root and locate transfer.yaml (same pattern as _cmd_assemble).
    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )
    manifest_path = exp_root / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = exp_root / "config" / "transfer.yaml"
    if not manifest_path.exists():
        print(f"error: transfer.yaml not found under {exp_root}", file=sys.stderr)
        return 2

    try:
        manifest = _manifest.load_manifest(manifest_path)
    except _manifest.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    scenario = manifest.get("scenario")
    if not scenario:
        print("error: transfer.yaml missing required 'scenario' field", file=sys.stderr)
        return 2

    # Alias validation — reject names that don't match the shared regex before
    # any disk writes. Design §Alias and algorithm-name validation rules.
    try:
        translation_ref.validate_name(scenario)
    except translation_ref.ValidationError as exc:
        print(f"error: invalid scenario name (used as alias): {exc}", file=sys.stderr)
        return 2

    # Algorithm-name validation + source-file existence — every declared
    # algorithm must be usable. The slicer will also error if a source is
    # missing, but we catch invalid names first to keep the error message
    # actionable.
    declared_algos = manifest.get("algorithms") or []
    if not declared_algos:
        print("error: transfer.yaml has no algorithms declared", file=sys.stderr)
        return 2
    for algo in declared_algos:
        try:
            translation_ref.validate_name(algo.get("name", ""))
        except translation_ref.ValidationError as exc:
            print(f"error: invalid algorithm name: {exc}", file=sys.stderr)
            return 2

    # Compute translation hash — folds algorithm source contents in.
    try:
        thash = slicer.translation_hash_with_sources(manifest, exp_root)
    except Exception as exc:  # slicer raises AssembleError; catch broadly for CLI robustness.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    expected_names = [a["name"] for a in declared_algos]
    state, missing = _translate_state(thash, expected_names)

    # Dispatch on (state, command). See design §"State machine" table.
    if args.resume:
        if state == "nothing":
            print(
                f"error: no translation to resume for hash {thash} — "
                f"run 'sim2real translate' first",
                file=sys.stderr,
            )
            return 2
        if state == "partial":
            print(
                f"error: missing outputs for: {', '.join(missing)} — "
                f"run '/sim2real-translate' first",
                file=sys.stderr,
            )
            return 2
        # state == "complete"
        print(f"translation {thash} already complete — run 'sim2real build' next")
        return 0

    if args.force:
        _translate_delete_dir(thash)
        return _translate_write_checkpoint(
            thash=thash,
            scenario=scenario,
            manifest=manifest,
            exp_root=exp_root,
            declared_algos=declared_algos,
        )

    # Plain translate.
    if state == "partial":
        print(
            f"error: translation {thash} incomplete — "
            f"run '/sim2real-translate' then 'sim2real translate --resume'",
            file=sys.stderr,
        )
        return 2
    if state == "complete":
        print(f"translation {thash} already complete — run 'sim2real build' next")
        return 0
    # state == "nothing"
    return _translate_write_checkpoint(
        thash=thash,
        scenario=scenario,
        manifest=manifest,
        exp_root=exp_root,
        declared_algos=declared_algos,
    )


def _translate_write_checkpoint(
    *,
    thash: str,
    scenario: str,
    manifest: dict,
    exp_root: Path,
    declared_algos: list[dict],
) -> int:
    """Write ``skill_input.json`` + ``translation_output.json`` and print the
    checkpoint message. Returns 0 on success.

    Caller has already ensured the state is ``nothing`` (either originally
    or after ``_translate_delete_dir``). All writes are atomic; a
    filesystem failure aborts and leaves the dir in whatever mid-write
    state it reached — the operator can rerun with ``--force`` to recover.
    """
    import hashlib as _hashlib

    tdir = layout.translation_dir(thash)
    tdir.mkdir(parents=True, exist_ok=True)

    # Assemble per-algorithm records for both output files.
    algo_records = []
    for algo in declared_algos:
        name = algo["name"]
        source_rel = algo.get("source")
        source_sha = None
        if source_rel:
            source_sha = _hashlib.sha256(
                (exp_root / source_rel).read_bytes()
            ).hexdigest()
        algo_records.append({
            "name": name,
            "source_path": source_rel,
            "source_sha256": source_sha,
            "notes": algo.get("notes", ""),
        })

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tout = _build_translate_output(
        translation_hash=thash,
        scenario=scenario,
        algorithms=algo_records,
        now_iso=now_iso,
    )
    _atomic_write_json(layout.translation_output_path(thash), tout)

    # Baseline overlay: manifest.baseline is optional per design.
    baseline_manifest = manifest.get("baseline") or None
    skin_baseline: dict | None
    if baseline_manifest:
        skin_baseline = {
            "config_path": baseline_manifest.get("config"),
            "generated_overlay_path": "generated/baseline_config.yaml",
        }
    else:
        skin_baseline = None

    context = manifest.get("context") or {"text": "", "file_paths": []}
    skin = _build_skill_input(
        translation_hash=thash,
        experiment_root=exp_root,
        translations_dir=layout.translations_dir(),
        scenario=scenario,
        baseline=skin_baseline,
        algorithms=algo_records,
        context=context,
    )
    _atomic_write_json(tdir / "skill_input.json", skin)

    print(
        f"translation {thash} checkpoint written — "
        f"run '/sim2real-translate' then 'sim2real translate --resume'"
    )
    return 0
```

- [ ] **Step 3: Green.** All 9 state-machine tests pass.

---

## Task 6 — Hash-collision & robustness tests

**Rationale:** Issue #467 acceptance calls out "hash-collision handling." The design's stance: `translation_hash_with_sources` collision requires an actual SHA-256 collision (vanishingly unlikely) — but we should at least assert that when the on-disk `translation_output.json` records a *different* algorithm set than the current manifest, plain `translate` refuses rather than clobbering (i.e., it lands in the `partial` state naturally through algo-name mismatch).

**Files:**
- Modify: `pipeline/tests/test_translate.py`

- [ ] **Step 1: Add a test that seeds `translation_output.json` with an unexpected algorithm set, runs plain translate, and asserts exit 2.** The state-machine helper treats this as `"partial"` because the declared-vs-recorded algorithms diverge. Message should still be actionable.

```python
class TestTranslateHashCollision:
    def test_recorded_algorithms_diverge_from_manifest(self, tmp_path, capsys):
        # Set up a "translation" dir whose translation_output.json declares
        # different algorithms than the current manifest would produce.
        # Plain translate lands in `partial` and exits 2.
        manifest_path = _write_manifest(tmp_path, algorithms=[
            {"name": "algo1", "source": "algorithms/algo1.py"},
        ])
        thash = _compute_hash(tmp_path, manifest_path)
        tdir = tmp_path / "workspace" / "translations" / thash
        (tdir / "generated" / "old_algo").mkdir(parents=True)
        # translation_output.json recorded a different algo set previously.
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1, "translation_hash": thash, "source": "skill",
            "alias": "s", "algorithms": [{"name": "old_algo"}],
            "created_at": "2026-07-02T00:00:00Z",
        }))
        (tdir / "generated" / "old_algo" / "old_algo_output.json").write_text("{}")
        # Plain translate: current manifest declares algo1, dir records old_algo →
        # partial (algo1's output missing).
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "incomplete" in err
```

- [ ] **Step 2: Green.**

---

## Task 7 — Full-suite verification

- [ ] Run `.venv/bin/python -m pytest pipeline/ -v` — every test passes (existing + new).
- [ ] Run `.venv/bin/ruff check pipeline/ .claude/skills/ --select F` — clean.
- [ ] Manual smoke: create a temp experiment repo with `transfer.yaml` + a stub algorithm; run `sim2real translate` and inspect the two written files by hand — schemas match the design's canonical examples.

---

## Task 8 — CI + stale-reference sweep

- [ ] Add `pipeline/tests/test_translate.py` to `.github/workflows/test.yml` (in the same explicit list as `test_sim2real.py` etc).
- [ ] Grep the repo for stale references to `prepare.py` / `runs/<run>/generated/` that this PR could have invalidated: `git grep -n 'prepare\.py\|runs/<run>/generated' .claude/` — nothing to update here (the design says PR 4 sweeps `.claude/skills/sim2real-translate/`, and PR 6 sweeps everywhere else — this PR shouldn't touch either).
- [ ] Update `CLAUDE.md` — add a one-line entry under the pipeline commands table pointing at `sim2real translate` as the skill-driven producer, symmetrical with `translation register`. (Optional; PR 5 will refresh docs comprehensively, so a small stub here is fine.)

---

## Task 9 — Commit, push, PR

- [ ] `git add -A` (verify only worktree paths in `git status`)
- [ ] Commit with a Conventional-Commits-style subject: `feat(sim2real): add sim2real translate command (initial + --resume)` and a body summarizing the state machine + closing `#467`.
- [ ] Push: `git push -u origin refactor/v2-step-2-issue-467-sim2real-translate`.
- [ ] Open PR against `refactor/v2-step-2` (NOT `main`):

```bash
gh pr create --base refactor/v2-step-2 --title "step-2 PR 3: sim2real translate command (initial + --resume)" --body-file <path>
```

PR body sections:
- **Summary** — one-paragraph what and why, referencing epic #463.
- **Design** — permalink to `docs/epics/step-2/design.md#pr-3--sim2real-translate-command`.
- **State machine** — inline the 3×3 table so reviewers don't have to click through.
- **What was NOT touched** — call out that `translation_ref` and `slicer` are unchanged (both already merged from PRs 1 and 2).
- **Sweep notes** — what I grep'd for and found nothing to update (per Task 8).
- **Closes #467.**

---

## Acceptance cross-check (from issue #467)

- [ ] `sim2real translate` subcommand implemented in `pipeline/sim2real.py` — Task 4 + Task 5.
- [ ] Initial run: hash via `translation_hash_with_sources`, creates `translations/<hash>/`, writes `skill_input.json` per pinned schema, writes `translation_output.json` with `alias`, `algorithms[]`, per-algo `image_ref: null`, prints checkpoint, exits 0 — Task 5 (state=nothing/plain) + Task 2 builders.
- [ ] `--resume` reads `translation_output.json:algorithms`, probes each `<algo>_output.json`, errors with `"missing outputs for: <names>"` — Task 5 (state=partial/resume) + Task 3.
- [ ] `--force` deletes and recreates per table — Task 5 (force branches).
- [ ] State machine matches design table exactly (9 cells) — Task 5.
- [ ] Tests enumerated in the issue's acceptance list — Tasks 2, 3, 5, 6 collectively cover them.
