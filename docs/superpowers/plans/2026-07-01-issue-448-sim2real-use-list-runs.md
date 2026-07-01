# step-1 PR 5 — `sim2real use` + `sim2real list runs` + delete `run.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two thin subcommands to `pipeline/sim2real.py` — `use --run RUN_NAME` (flip `current_run` in `setup_config.json`) and `list runs` (walk `workspace/runs/*/run_metadata.json`, print newest-first table with `*` marking active) — and delete the legacy `pipeline/run.py` + `pipeline/lib/run_manager.py` + their tests, plus documentation sweep.

**Architecture:** Two pure helper functions in `pipeline/sim2real.py` (kept together with `translation register` / `assemble` — the file already owns the CLI). Both walk paths under `layout.runs_dir()` and read `run_metadata.json` v1 (schema pinned by PR 2 / #445: fields `run_name`, `translation_hash`, `cluster_id`, `assembled_at`). `use` reads `setup_config.json`, mutates the `current_run` field only, and writes back — preserving unrelated keys. `list runs` degrades gracefully when the dir is missing or a metadata file is unreadable.

**Tech Stack:** Python 3.10, argparse, `pipeline.lib.layout` (workspace path helpers), `datetime` for the ASSEMBLED column, `pytest` + `capsys` / `tmp_path` for tests.

## Global Constraints

- All new code lives in the worktree at `.claude/worktrees/issue-448-sim2real-use-list-runs/`. Every `Read`/`Edit`/`Write` path must contain that substring.
- Base branch: `refactor/v2-step-1`. PR opens against `refactor/v2-step-1`, not `main`.
- The `run_metadata.json` schema is version 1 as written by `pipeline/lib/assemble_run.py` — fields: `version`, `run_name`, `translation_hash`, `cluster_id`, `params_hash`, `image_tag`, `assembled_at`.
- `list runs` output format (per design doc §Commands → `sim2real list runs`):
  ```
    RUN_NAME             TRANSLATION    CLUSTER     ASSEMBLED
  * trial-2              abc12345       ocp-east    2026-07-01 14:32
    trial-1              abc12345       ocp-east    2026-07-01 12:10
  ```
  Order: mtime desc (newest first). `*` in a leading marker column marks `current_run`.
- `sim2real use --run <nonexistent>` errors with **exact** text `"run doesn't exist; try 'sim2real list runs'"`.
- `sim2real list runs` on missing `workspace/runs/` prints `"no runs yet"` and exits 0.
- Deletion is authoritative: `pipeline/run.py`, `pipeline/lib/run_manager.py`, `pipeline/tests/test_run.py`, `pipeline/tests/test_run_manager.py` are removed in this PR.
- `run.py inspect`'s replacement is left as `cat` for now — no `sim2real inspect run <R>` in this scope.
- CI: `.github/workflows/test.yml` lists `test_run.py` and `test_run_manager.py` neither explicitly today (grep confirms only new tests are listed), so no CI change is needed for the deletion. Verify at end.

---

## File Structure

**Modify:**
- `pipeline/sim2real.py` — add argparse subparsers `use` and `list runs`, plus `_cmd_use` / `_cmd_list_runs` handlers; wire in `main()`.
- `pipeline/tests/test_sim2real.py` — append `TestUseCommand` and `TestListRunsCommand` classes.
- `pipeline/README.md` — replace the `## run.py` section with a `## Manage runs` section documenting the new subcommands; update the "Running with an Experiment Repo" snippet and the artifact-table row for `run_metadata.json`; drop the `run_manager.py` row from the Pipeline library table.
- `CLAUDE.md` — remove the `pipeline/run.py` bullet and the two example command lines that use it; update the artifact-table Read-by columns; drop the `run_manager.py` row from the Pipeline library table.
- `pipeline/deploy.py` (line 201) — update the `_write_build_metadata` docstring to remove the stale `run.py inspect (via run_manager.inspect_run)` phrasing.

**Delete:**
- `pipeline/run.py`
- `pipeline/lib/run_manager.py`
- `pipeline/tests/test_run.py`
- `pipeline/tests/test_run_manager.py`

**Do not touch:**
- `pipeline/lib/layout.py` — helpers used as-is; no new fields needed.
- `pipeline/lib/assemble_run.py` — the run_metadata writer already produces the schema we consume.
- `pipeline/setup.py` — already writes `current_run` to `setup_config.json`; nothing changes.

---

## Task 1 — Add `sim2real use` subcommand (TDD)

**Files:**
- Modify: `pipeline/sim2real.py`
- Test: `pipeline/tests/test_sim2real.py` (append `TestUseCommand`)

**Interfaces:**
- Consumes: `layout.runs_dir()`, `layout.setup_config_path()`, argparse subparser conventions from the existing file.
- Produces: `_cmd_use(args) -> int` (returns 0 on success, 2 on error), a top-level argparse subcommand `use` with a required `--run RUN_NAME` flag. `main()` dispatches `args.command == "use"` to `_cmd_use`.

- [ ] **Step 1: Add failing tests for `use` validation and happy path**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestUseCommand:
    def _setup_run_dir(self, tmp_path, run_name):
        run_dir = tmp_path / "workspace" / "runs" / run_name
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(
            json.dumps({
                "version": 1,
                "run_name": run_name,
                "translation_hash": "abc123",
                "cluster_id": "ocp-east",
                "params_hash": "def456",
                "image_tag": "ghcr.io/foo:v1",
                "assembled_at": "2026-07-01T14:00:00Z",
            })
        )
        return run_dir

    def test_use_updates_current_run(self, tmp_path):
        self._setup_run_dir(tmp_path, "trial-1")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "trial-1",
        ])
        assert rc == 0
        cfg = json.loads((tmp_path / "workspace" / "setup_config.json").read_text())
        assert cfg["current_run"] == "trial-1"

    def test_use_preserves_other_setup_config_keys(self, tmp_path):
        self._setup_run_dir(tmp_path, "trial-1")
        cfg_path = tmp_path / "workspace" / "setup_config.json"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps({
            "registry": "ghcr.io/me",
            "repo_name": "sim2real",
            "current_run": "trial-0",
            "orchestrator_image": "ghcr.io/me/orch:v1",
        }))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "trial-1",
        ])
        assert rc == 0
        cfg = json.loads(cfg_path.read_text())
        assert cfg["current_run"] == "trial-1"
        assert cfg["registry"] == "ghcr.io/me"
        assert cfg["repo_name"] == "sim2real"
        assert cfg["orchestrator_image"] == "ghcr.io/me/orch:v1"

    def test_use_nonexistent_run_errors_with_hint(self, tmp_path, capsys):
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "ghost",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "run doesn't exist; try 'sim2real list runs'" in err

    def test_use_run_without_metadata_errors(self, tmp_path, capsys):
        run_dir = tmp_path / "workspace" / "runs" / "half-baked"
        run_dir.mkdir(parents=True)
        # No run_metadata.json inside.
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "use", "--run", "half-baked",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "run doesn't exist; try 'sim2real list runs'" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_sim2real.py::TestUseCommand -v`
Expected: FAIL (subparser doesn't have `use`; argparse exits with 2).

- [ ] **Step 3: Implement `use` subparser + `_cmd_use`**

Inside `pipeline/sim2real.py`, in `build_parser()` add (after the `assemble` subparser block, before `return parser`):

```python
    use = sub.add_parser("use", help="Set the active run in setup_config.json")
    use.add_argument(
        "--run",
        required=True,
        metavar="RUN_NAME",
        help="run name — must correspond to workspace/runs/<RUN_NAME>/",
    )

    lst = sub.add_parser("list", help="List workspace-scoped resources")
    lsub = lst.add_subparsers(dest="subcommand", required=True)
    lsub.add_parser("runs", help="List runs, newest first")
```

Add the two handlers below `_cmd_assemble`:

```python
def _cmd_use(args) -> int:
    run_dir = layout.runs_dir() / args.run
    if not run_dir.is_dir() or not (run_dir / "run_metadata.json").exists():
        print(
            "error: run doesn't exist; try 'sim2real list runs'",
            file=sys.stderr,
        )
        return 2

    cfg_path = layout.setup_config_path()
    existing = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            # Corrupted setup_config.json — treat as empty and rewrite. The
            # `use` command's contract is "flip current_run"; preserving
            # unreadable garbage isn't a goal.
            existing = {}
    existing["current_run"] = args.run
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"current_run → {args.run}")
    return 0
```

Wire in `main()` (add before the fallback `return 1`):

```python
    if args.command == "use":
        return _cmd_use(args)
```

- [ ] **Step 4: Run the use tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_sim2real.py::TestUseCommand -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(sim2real): add 'use --run' subcommand"
```

---

## Task 2 — Add `sim2real list runs` subcommand (TDD)

**Files:**
- Modify: `pipeline/sim2real.py`
- Test: `pipeline/tests/test_sim2real.py` (append `TestListRunsCommand`)

**Interfaces:**
- Consumes: `layout.runs_dir()`, `layout.setup_config_path()`. Reads each `runs/*/run_metadata.json` and each `run_metadata.json`'s mtime.
- Produces: `_cmd_list_runs(args) -> int` (returns 0 on success, always — malformed metadata surfaces as `?` cells, not as errors). `main()` dispatches `args.command == "list"` with `args.subcommand == "runs"` to `_cmd_list_runs`.

- [ ] **Step 1: Add failing tests for `list runs`**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestListRunsCommand:
    def _write_run(self, tmp_path, name, translation, cluster, assembled, mtime_offset=0):
        run_dir = tmp_path / "workspace" / "runs" / name
        run_dir.mkdir(parents=True)
        meta = run_dir / "run_metadata.json"
        meta.write_text(json.dumps({
            "version": 1,
            "run_name": name,
            "translation_hash": translation,
            "cluster_id": cluster,
            "params_hash": "p",
            "image_tag": "ghcr.io/foo:v1",
            "assembled_at": assembled,
        }))
        if mtime_offset:
            import os
            st = meta.stat()
            os.utime(meta, (st.st_atime, st.st_mtime + mtime_offset))
        return meta

    def _write_setup_config(self, tmp_path, current_run):
        cfg = tmp_path / "workspace" / "setup_config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"current_run": current_run}))

    def test_missing_runs_dir_prints_no_runs_yet(self, tmp_path, capsys):
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "no runs yet"

    def test_empty_runs_dir_prints_no_runs_yet(self, tmp_path, capsys):
        (tmp_path / "workspace" / "runs").mkdir(parents=True)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "no runs yet"

    def test_mtime_ordering_newest_first(self, tmp_path, capsys):
        # Write trial-1 first (older mtime), then trial-2 with a +100s bump.
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        self._write_run(tmp_path, "trial-2", "abc12345", "ocp-east",
                        "2026-07-01T14:32:00Z", mtime_offset=100)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # First line is the header, then trial-2 (newest), then trial-1.
        assert "RUN_NAME" in lines[0] and "TRANSLATION" in lines[0]
        assert "CLUSTER" in lines[0] and "ASSEMBLED" in lines[0]
        assert lines[1].split()[0] == "trial-2"
        assert lines[2].split()[0] == "trial-1"

    def test_current_run_marker(self, tmp_path, capsys):
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        self._write_run(tmp_path, "trial-2", "abc12345", "ocp-east",
                        "2026-07-01T14:32:00Z", mtime_offset=100)
        self._write_setup_config(tmp_path, "trial-1")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # trial-2 (newest, no marker); trial-1 (current, has "*").
        assert lines[1].startswith("  trial-2") or lines[1].lstrip().startswith("trial-2")
        assert lines[2].lstrip().startswith("* trial-1")

    def test_no_current_run_prints_no_marker(self, tmp_path, capsys):
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        lines = capsys.readouterr().out.splitlines()
        # No line starts with '*'.
        for line in lines[1:]:
            assert not line.lstrip().startswith("*")

    def test_translation_hash_truncated_to_8_chars(self, tmp_path, capsys):
        self._write_run(tmp_path, "trial-1", "a" * 64, "ocp-east",
                        "2026-07-01T12:10:00Z")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        # First 8 chars of hash appear as a token.
        assert "aaaaaaaa" in out
        # The full 64-char hash should NOT appear on the run's data line.
        data_line = [ln for ln in out.splitlines() if "trial-1" in ln][0]
        assert "a" * 64 not in data_line

    def test_malformed_metadata_shows_question_marks(self, tmp_path, capsys):
        run_dir = tmp_path / "workspace" / "runs" / "trial-broken"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text("{not valid json")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "trial-broken" in out
        assert "?" in out  # placeholder for unreadable metadata

    def test_missing_metadata_skips_directory(self, tmp_path, capsys):
        # Directory with no run_metadata.json is not a run.
        (tmp_path / "workspace" / "runs" / "not-a-run").mkdir(parents=True)
        self._write_run(tmp_path, "trial-1", "abc12345", "ocp-east",
                        "2026-07-01T12:10:00Z")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "list", "runs",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "trial-1" in out
        assert "not-a-run" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_sim2real.py::TestListRunsCommand -v`
Expected: FAIL (subparser `list runs` not implemented; dispatch missing).

- [ ] **Step 3: Implement `_cmd_list_runs` + wire into `main()`**

Append to `pipeline/sim2real.py` below `_cmd_use`:

```python
def _read_current_run() -> str:
    """Return current_run from setup_config.json, or "" if absent/unreadable."""
    cfg_path = layout.setup_config_path()
    if not cfg_path.exists():
        return ""
    try:
        return json.loads(cfg_path.read_text()).get("current_run", "") or ""
    except (json.JSONDecodeError, OSError):
        return ""


def _format_assembled(iso: str) -> str:
    """Turn an ISO-8601 UTC timestamp into "YYYY-MM-DD HH:MM" for display.

    Returns "?" if the input isn't parseable — the CLI degrades gracefully
    rather than erroring on one bad row.
    """
    try:
        # datetime.fromisoformat accepts "...Z" only in 3.11+; strip it for parity.
        s = iso[:-1] if iso.endswith("Z") else iso
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return "?"


def _cmd_list_runs(_args) -> int:
    runs_dir = layout.runs_dir()
    if not runs_dir.is_dir():
        print("no runs yet")
        return 0

    entries = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "run_metadata.json"
        if not meta_path.exists():
            continue
        mtime = meta_path.stat().st_mtime
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            meta = None
        entries.append((mtime, run_dir.name, meta))

    if not entries:
        print("no runs yet")
        return 0

    entries.sort(key=lambda e: e[0], reverse=True)
    current = _read_current_run()

    fmt = "{marker} {name:<20} {translation:<14} {cluster:<11} {assembled}"
    print(fmt.format(
        marker=" ", name="RUN_NAME", translation="TRANSLATION",
        cluster="CLUSTER", assembled="ASSEMBLED",
    ))
    for _mtime, name, meta in entries:
        if meta is None:
            translation = "?"
            cluster = "?"
            assembled = "?"
        else:
            thash = meta.get("translation_hash") or ""
            translation = thash[:8] if thash else "?"
            cluster = meta.get("cluster_id") or "?"
            assembled = _format_assembled(meta.get("assembled_at") or "")
        marker = "*" if name == current else " "
        print(fmt.format(
            marker=marker, name=name, translation=translation,
            cluster=cluster, assembled=assembled,
        ))
    return 0
```

Update `main()` — add:

```python
    if args.command == "list" and args.subcommand == "runs":
        return _cmd_list_runs(args)
```

- [ ] **Step 4: Run all sim2real tests to verify use + list runs pass together**

Run: `python -m pytest pipeline/tests/test_sim2real.py -v`
Expected: PASS (all pre-existing tests + new `TestUseCommand` + `TestListRunsCommand`).

- [ ] **Step 5: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(sim2real): add 'list runs' subcommand"
```

---

## Task 3 — Delete `run.py`, `run_manager.py`, and their tests

**Files:**
- Delete: `pipeline/run.py`, `pipeline/lib/run_manager.py`, `pipeline/tests/test_run.py`, `pipeline/tests/test_run_manager.py`
- Modify: `pipeline/deploy.py:201` (docstring only)

**Interfaces:** No public interface change; `_write_build_metadata` in `deploy.py` continues writing `stages.deploy.last_completed_step` — the field is now dormant (no consumer) but harmless.

- [ ] **Step 1: Confirm no other consumer imports `run_manager`**

Run:

```bash
grep -rn "from pipeline.lib.run_manager\|import run_manager" pipeline/ --include="*.py" | grep -v "^pipeline/run.py\|^pipeline/lib/run_manager\|^pipeline/tests/test_run"
```

Expected: no output (or, if any hit remains, add its fix to this task).

- [ ] **Step 2: Delete the four files**

Run:

```bash
git rm pipeline/run.py pipeline/lib/run_manager.py \
       pipeline/tests/test_run.py pipeline/tests/test_run_manager.py
```

- [ ] **Step 3: Update the stale docstring in `deploy.py`**

In `pipeline/deploy.py`, the `_write_build_metadata` docstring (around line 197-204) currently reads:

```python
def _write_build_metadata(run_dir: Path, epp_image: str) -> None:
    """Record a successful EPP build in run_metadata.json.

    Sets ``epp_image`` and ``stages.deploy.last_completed_step = "build"`` so
    ``run.py inspect`` (via ``run_manager.inspect_run``) shows the deploy
    progress. No-op if run_metadata.json is missing or unparseable — the
    caller's earlier load/validate path already surfaces those errors.
    """
```

Replace with:

```python
def _write_build_metadata(run_dir: Path, epp_image: str) -> None:
    """Record a successful EPP build in run_metadata.json.

    Sets ``epp_image`` and ``stages.deploy.last_completed_step = "build"``.
    Currently no reader consumes these fields — they remain for future
    inspect tooling (see epic #443). No-op if run_metadata.json is missing
    or unparseable — the caller's earlier load/validate path already
    surfaces those errors.
    """
```

- [ ] **Step 4: Run the full pipeline test suite to confirm nothing broke**

Run: `python -m pytest pipeline/ -v`
Expected: PASS (with test_run.py and test_run_manager.py absent).

- [ ] **Step 5: Commit**

```bash
git add -u pipeline/deploy.py
git commit -m "refactor(sim2real): delete legacy run.py and run_manager"
```

---

## Task 4 — Documentation sweep

**Files:**
- Modify: `CLAUDE.md`
- Modify: `pipeline/README.md`

**Interfaces:** N/A — docs only.

- [ ] **Step 1: Update `CLAUDE.md`**

Three edits, all in the current worktree copy:

Edit 1 — remove the two `run.py` lines from the "Run all pipeline commands" example block (lines 45-46). Delete the block outright:

```
python pipeline/run.py       --experiment-root ../admission-control list
python pipeline/run.py       --experiment-root ../admission-control switch <run-name>
```

Replace with:

```
python pipeline/sim2real.py --experiment-root ../admission-control list runs
python pipeline/sim2real.py --experiment-root ../admission-control use --run <run-name>
```

Edit 2 — remove the `**pipeline/run.py**` bullet (line 61). Replace with:

```
**`pipeline/sim2real.py` (`use`, `list runs`)** — Manage runs. `use --run <name>` flips `current_run` in `setup_config.json`. `list runs` prints all runs newest-first with the current run marked `*`.
```

Edit 3 — remove the `run_manager.py` row from the Pipeline library table (line 75).

Edit 4 — in the Workspace Artifacts table, replace `run.py` in the Read-by columns:
- Line 88: `setup_config.json` Read by: was `deploy.py, run.py` → `deploy.py, sim2real.py`
- Line 90: `translation_output.json` Read by: was `sim2real assemble, deploy.py, run.py` → `sim2real assemble, deploy.py`
- Line 94: `run_metadata.json` Read by: was `deploy.py, run.py` → `deploy.py, sim2real.py`

- [ ] **Step 2: Update `pipeline/README.md`**

Edit 1 — top of file (line 13), replace the sentence:

```
`run.py` manages runs independently of the main flow.
```

with:

```
`sim2real.py`'s `use` and `list runs` subcommands manage runs independently of the main flow.
```

Edit 2 — replace the `## run.py` section (lines 353-366) with a `## Manage runs` section:

```markdown
## Manage runs

`pipeline/sim2real.py` exposes two run-management subcommands.

```bash
python pipeline/sim2real.py --experiment-root ../admission-control list runs
python pipeline/sim2real.py --experiment-root ../admission-control use --run <name>
```

**`sim2real list runs`** — Walks `workspace/runs/*/run_metadata.json` and prints one row per run, newest first (mtime desc). The active run (`current_run` in `setup_config.json`) is marked with `*`. Prints `no runs yet` and exits 0 if `workspace/runs/` is empty or missing.

**`sim2real use --run <name>`** — Sets `current_run` in `setup_config.json` to the given run. Errors if `workspace/runs/<name>/run_metadata.json` does not exist.

Today's inspect debug view is dropped without replacement — `cat workspace/runs/<name>/run_metadata.json` is the shortest path. If a structured inspect surfaces demand, a follow-up `sim2real inspect run <name>` can be filed against the epic.
```

Edit 3 — Pipeline library table (line 379): remove the `run_manager.py` row.

Edit 4 — Workspace artifacts table:
- Line 394: `setup_config.json` Read by: `deploy.py, run.py` → `deploy.py, sim2real.py`
- Line 396: `translation_output.json` Read by: `sim2real assemble, deploy.py, run.py` → `sim2real assemble, deploy.py`
- Line 399: `run_metadata.json` Read by: `deploy.py, run.py` → `deploy.py, sim2real.py`

- [ ] **Step 3: Grep to catch any remaining `run.py` references in tracked docs/config**

Run:

```bash
grep -rn "run\.py\|run_manager\|inspect_run\|switch_run" \
    pipeline/README.md CLAUDE.md .github/workflows/ 2>&1 \
    | grep -v "test_deploy_run\|deploy_run\|test_run.py:" \
    | grep -v "__pycache__"
```

Expected: empty output — every remaining hit that is NOT for `test_deploy_run.py` (different file, unrelated) must have been addressed above.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md pipeline/README.md
git commit -m "docs: rewrite run.py references for sim2real use/list runs"
```

---

## Task 5 — Final verification

**Files:** N/A (verification only).

**Interfaces:** N/A.

- [ ] **Step 1: Full pipeline test suite**

Run: `python -m pytest pipeline/ -v`
Expected: All tests pass, including `TestUseCommand` and `TestListRunsCommand`. No hits for `test_run.py` or `test_run_manager.py` (deleted).

- [ ] **Step 2: CI-mirror test suite**

Run:

```bash
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  -v
```

Expected: PASS.

- [ ] **Step 3: Lint**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: no errors.

- [ ] **Step 4: Sanity dry-run of both new commands with an empty workspace**

Run:

```bash
python pipeline/sim2real.py --experiment-root /tmp/does-not-exist list runs
```

Expected: prints `no runs yet`, exits 0.

```bash
python pipeline/sim2real.py --experiment-root /tmp/does-not-exist use --run trial-1
```

Expected: exits 2 with `error: run doesn't exist; try 'sim2real list runs'` on stderr.

- [ ] **Step 5: Path-discipline check**

Run: `git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status`
Expected: `nothing to commit, working tree clean` — the parent repo received no leaked edits.

Run: `git status`
Expected: `nothing to commit, working tree clean` — the worktree's commits are all staged.

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin refactor/v2-step-1-issue-448-sim2real-use-list-runs
```

Then `gh pr create --base refactor/v2-step-1 --title "..." --body-file <(cat <<'EOF' ...` per fix-issue Step 7 instructions.

---

## Self-review

**1. Spec coverage** (mapping the issue's acceptance list to tasks):

| Acceptance criterion | Task |
|---|---|
| `sim2real use --run trial-1` updates `setup_config.json:current_run` | Task 1 |
| `sim2real use --run <nonexistent>` errors with exact hint | Task 1 |
| `sim2real list runs` prints columns `RUN_NAME`, `TRANSLATION`, `CLUSTER`, `ASSEMBLED`, newest first | Task 2 |
| `*` marks `current_run` in list output | Task 2 |
| `workspace/runs/` missing prints `"no runs yet"` and exits 0 | Task 2 |
| Unit tests: mtime ordering, missing-run behavior, `current_run` marker, empty runs dir | Tasks 1+2 |
| `pipeline/run.py` deleted | Task 3 |
| `CLAUDE.md` reference to `run.py` removed | Task 4 |
| `pipeline/README.md` gets a "Manage runs" section | Task 4 |

Every criterion is covered.

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later" markers. Every code step contains complete code.

**3. Type consistency:** `_cmd_use(args) -> int`, `_cmd_list_runs(_args) -> int`, `_read_current_run() -> str`, `_format_assembled(iso: str) -> str`. All referenced under `main()` dispatch. All argparse subparser names line up with `args.command == "..."` and `args.subcommand == "..."` checks. Tests reference `sim2real.main([...])` and read via `layout.runs_dir()` / `layout.setup_config_path()` — matches the module's convention.
