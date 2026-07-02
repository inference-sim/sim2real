# Fix empty benchmarkGit*/blisGit* params in sim2real assemble Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the framework-submodule SHA + URL discovery from the deleted `pipeline/prepare.py:_get_submodule_shas` into `pipeline/lib/assemble_run.py` so `sim2real assemble` populates `benchmarkGitRepoUrl`, `benchmarkGitCommit`, `blisGitRepoUrl`, `blisGitCommit` in generated PipelineRun YAMLs. Today they land empty and every cluster-side `git clone` step breaks.

**Architecture:** Add a `discover_framework_submodules() -> tuple[dict[str,str], dict[str,str], list[str]]` helper in `assemble_run.py` that returns `(shas, urls, missing_names)`. Shas come from `git rev-parse HEAD` in each submodule directory (falls back to `"unknown"`). URLs come from parsing `.gitmodules` at the framework's `REPO_ROOT` (declarative, doesn't depend on submodule being populated). The `assemble` orchestrator calls the helper, forwards `shas` + `urls` to `generate_pipelineruns`, and stores `missing_names` on the side-band attribute `assemble_run.missing_submodules` (matches the existing `skipped_algorithms` pattern) for `sim2real.py:_cmd_assemble` to surface as operator warnings.

**Tech Stack:** Python 3.10+, pytest, `configparser` (stdlib — parses `.gitmodules` since it's INI-shaped), `subprocess` (already imported transitively). No new deps.

## Global Constraints

- Base branch: `refactor/v2-step-1` (not `main`)
- `assemble_run.py` is a **pure module**: no argparse, no `print`, no `sys.exit`. Warnings surface via side-band attributes (`assemble_run.skipped_algorithms`, and now `assemble_run.missing_submodules`); the CLI wrapper in `pipeline/sim2real.py` reads those and prints.
- Framework `REPO_ROOT` is computed as `Path(__file__).resolve().parent.parent.parent` — same pattern as `pipeline/lib/cluster_ops.py:70`. Do NOT use `layout.experiment_root()` here; the two framework submodules always live in the framework repo, not the experiment repo (matches legacy `_get_submodule_shas`).
- Framework submodule names are hardcoded: `("inference-sim", "llm-d-benchmark")`. Matches legacy. The `component` submodule handling that legacy `_get_submodule_shas` also did is out of scope (per issue).
- `"unknown"` sentinel for missing SHAs matches legacy — the string reaches the PipelineRun spec verbatim; the cluster-side `git clone` will fail on it. That's intended: local `assemble` succeeds so the operator can inspect the run dir; the cluster run visibly fails at the right step.
- Path discipline: every path passed to `Read`/`Edit`/`Write`/`Bash` must contain `.claude/worktrees/issue-458-submodule-git-params/`. After every edit batch, verify `git -C <parent-repo-root> status` is clean.
- Test invocation: `.venv/bin/python -m pytest pipeline/ -v` (venv at worktree root).

---

## File Structure

| File | Change | Reason |
|------|--------|--------|
| `pipeline/lib/assemble_run.py` | Add `discover_framework_submodules` helper (module-scope); replace the empty-dict deferral at :505-509 with a call to it; add `missing_submodules` side-band attr | Actual fix |
| `pipeline/tests/test_assemble_run.py` | Append `TestDiscoverFrameworkSubmodules` (4 tests: both present, one missing, both missing, URL parsing) + one integration test asserting `assemble()` populates the params on a happy path | Coverage |
| `pipeline/sim2real.py:387-392` | Extend the `for name in skipped_algorithms` warning block with a symmetric `for name in missing_submodules` block | Operator visibility |
| `pipeline/README.md` | Add a one-paragraph note that `sim2real assemble` requires framework submodules initialized before assemble; documents the `"unknown"` fallback | Documentation per AC |

Not touched:
- `pipeline/lib/tekton.py` — already accepts the four kwargs and threads them through; no behavior change.
- `pipeline/lib/layout.py` — not adding `repo_root()` there; localized computation in `assemble_run.py` matches `cluster_ops.py`'s pattern and keeps `layout.py`'s "experiment-workspace paths only" boundary intact.
- Existing tests in `TestGeneratePipelineruns` (which pass shas/urls explicitly) — they already cover the downstream propagation, so no changes needed.

---

## Task 1 — Add `discover_framework_submodules` helper + tests

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (add helper near top of file, after imports at :29)
- Modify: `pipeline/tests/test_assemble_run.py` (append new test class)

**Interfaces:**
- Consumes: `configparser.ConfigParser` (stdlib), `subprocess.run` (via a local import — matching cluster_ops.py's minimal-surface style).
- Produces: `discover_framework_submodules(repo_root: Path) -> tuple[dict[str, str], dict[str, str], list[str]]`. Returns `(shas_by_name, urls_by_name, missing_names)`. `shas_by_name` keys: subset of `{"inference-sim", "llm-d-benchmark"}` (values are commit SHA strings, or `"unknown"` when the submodule dir exists but the SHA lookup fails). `urls_by_name`: same key set, values are URL strings from `.gitmodules` (or empty string if `.gitmodules` is missing/absent for that name). `missing_names` lists submodule names whose directory does not exist or is not populated — a downstream operator-visible warning.

- [ ] **Step 1: Write the failing tests**

Append to `pipeline/tests/test_assemble_run.py`:

```python
import configparser


class TestDiscoverFrameworkSubmodules:
    """discover_framework_submodules(repo_root) reads submodule state from
    the framework repo layout: .gitmodules for URLs, git rev-parse HEAD for
    SHAs, and returns a list of missing submodule names for the CLI wrapper
    to warn about."""

    def _write_gitmodules(self, repo_root: Path, entries: dict[str, str]) -> None:
        """Write a .gitmodules with the given {name: url} entries."""
        cfg = configparser.ConfigParser()
        for name, url in entries.items():
            section = f'submodule "{name}"'
            cfg[section] = {"path": name, "url": url}
        with (repo_root / ".gitmodules").open("w") as f:
            cfg.write(f)

    def _fake_submodule(self, repo_root: Path, name: str, sha: str) -> None:
        """Create a submodule directory that answers `git rev-parse HEAD`."""
        sub = repo_root / name
        sub.mkdir(parents=True)
        # Minimal real git repo — matches what `git submodule update --init`
        # produces on disk closely enough for `rev-parse HEAD` to succeed.
        import subprocess
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=sub, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-q", "-m", "init"],
                       cwd=sub, check=True)
        # Reset to the given SHA if one is provided — we let the caller
        # accept whatever HEAD we produce.

    def test_both_submodules_present(self, tmp_path):
        self._write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        })
        self._fake_submodule(tmp_path, "inference-sim", "aa")
        self._fake_submodule(tmp_path, "llm-d-benchmark", "bb")

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert set(shas) == {"inference-sim", "llm-d-benchmark"}
        assert shas["inference-sim"] and shas["inference-sim"] != "unknown"
        assert shas["llm-d-benchmark"] and shas["llm-d-benchmark"] != "unknown"
        assert urls == {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        }
        assert missing == []

    def test_one_missing_reports_it(self, tmp_path):
        """Submodule directory absent → name in missing list; shas has no
        entry; urls entry is present (comes from .gitmodules, not disk)."""
        self._write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        })
        self._fake_submodule(tmp_path, "inference-sim", "aa")
        # llm-d-benchmark deliberately missing on disk.

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert "inference-sim" in shas
        assert shas.get("llm-d-benchmark") == "unknown"
        assert missing == ["llm-d-benchmark"]
        # URL still populated from .gitmodules even though the dir is missing.
        assert urls["llm-d-benchmark"] == "https://github.com/llm-d/llm-d-benchmark.git"

    def test_both_missing(self, tmp_path):
        """Neither submodule on disk; .gitmodules present. All three return
        values populate with unknown/empty state, no crash."""
        self._write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        })

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert shas == {"inference-sim": "unknown", "llm-d-benchmark": "unknown"}
        assert urls == {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
        }
        assert sorted(missing) == ["inference-sim", "llm-d-benchmark"]

    def test_no_gitmodules_file(self, tmp_path):
        """No .gitmodules at repo_root → URLs default to empty string; SHAs
        still probed. Matches legacy 'do not crash if the file is absent'."""
        self._fake_submodule(tmp_path, "inference-sim", "aa")

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert "inference-sim" in shas
        assert shas["inference-sim"] != "unknown"
        # URL key present (matches shas key set) but empty since no source.
        assert urls == {"inference-sim": "", "llm-d-benchmark": ""}
        assert missing == ["llm-d-benchmark"]

    def test_gitmodules_lists_extra_submodule_ignored(self, tmp_path):
        """`.gitmodules` may list submodules other than the two framework
        ones (e.g. tektonc-data-collection). Only the framework pair is
        returned — extras are ignored."""
        self._write_gitmodules(tmp_path, {
            "inference-sim": "https://github.com/inference-sim/inference-sim.git",
            "llm-d-benchmark": "https://github.com/llm-d/llm-d-benchmark.git",
            "tektonc-data-collection": "https://github.com/x/y.git",
        })
        self._fake_submodule(tmp_path, "inference-sim", "aa")
        self._fake_submodule(tmp_path, "llm-d-benchmark", "bb")

        shas, urls, missing = assemble_run.discover_framework_submodules(tmp_path)

        assert set(shas) == {"inference-sim", "llm-d-benchmark"}
        assert set(urls) == {"inference-sim", "llm-d-benchmark"}
        assert "tektonc-data-collection" not in shas
        assert "tektonc-data-collection" not in urls
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py::TestDiscoverFrameworkSubmodules -v`
Expected: FAIL — `AttributeError: module 'pipeline.lib.assemble_run' has no attribute 'discover_framework_submodules'`.

- [ ] **Step 3: Implement the helper**

Insert into `pipeline/lib/assemble_run.py` right after the imports block (after the existing `from pipeline.lib.values import deep_merge` at :27, before `class AssembleError` at :30):

```python
import configparser
import subprocess


# Framework submodule pair — pinned. These names appear in the PipelineRun
# spec's benchmarkGit*/blisGit* params, and the cluster-side pipeline
# clones them by URL and checks out the recorded SHA. The component
# submodule (tracked by `manifest["component"]["path"]`) is deliberately
# out of scope: the component image reference comes from the registered
# translation, not from a git ref.
_FRAMEWORK_SUBMODULE_NAMES: tuple[str, ...] = ("inference-sim", "llm-d-benchmark")


def discover_framework_submodules(
    repo_root: Path,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Read framework submodule state from ``repo_root``.

    Returns ``(shas, urls, missing)``:

    - ``shas``: ``{name: sha}`` for each framework submodule directory that
      exists on disk (populated via ``git rev-parse HEAD`` in that dir).
      An entry with value ``"unknown"`` means the directory exists but the
      SHA lookup failed. A missing entry means the directory was absent.
      Callers pass this through to the PipelineRun spec verbatim; the
      cluster-side clone step fails visibly on ``"unknown"``, which is the
      intended posture — assemble succeeds locally, cluster fails at the
      right step (matches legacy ``pipeline/prepare.py:_get_submodule_shas``).
    - ``urls``: ``{name: url}`` for every framework submodule, sourced from
      ``<repo_root>/.gitmodules``. Value is ``""`` when ``.gitmodules`` is
      absent or has no entry for that name. URL discovery is declarative
      and does not depend on the submodule directory being populated.
    - ``missing``: sorted list of framework submodule names whose directory
      does not exist under ``repo_root``. The CLI wrapper surfaces this as
      an operator warning via the side-band ``missing_submodules`` attr.

    ``repo_root`` is the framework repo root (three levels up from this
    file), not the experiment root.
    """
    shas: dict[str, str] = {}
    missing: list[str] = []
    for name in _FRAMEWORK_SUBMODULE_NAMES:
        sub = repo_root / name
        if not sub.exists() or not (sub / ".git").exists():
            missing.append(name)
            continue
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=sub,
                capture_output=True,
                text=True,
                check=True,
            )
            shas[name] = result.stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            shas[name] = "unknown"

    urls: dict[str, str] = {name: "" for name in _FRAMEWORK_SUBMODULE_NAMES}
    gitmodules_path = repo_root / ".gitmodules"
    if gitmodules_path.exists():
        parser = configparser.ConfigParser()
        try:
            parser.read(gitmodules_path)
        except configparser.Error:
            # Corrupt .gitmodules — leave urls empty; missing list already
            # reflects any absent-on-disk submodules.
            return shas, urls, sorted(missing)
        for section in parser.sections():
            # Sections look like: submodule "<name>"
            if not section.startswith('submodule "') or not section.endswith('"'):
                continue
            name = section[len('submodule "'):-1]
            if name not in _FRAMEWORK_SUBMODULE_NAMES:
                continue
            urls[name] = parser.get(section, "url", fallback="")

    # For submodules on the missing list, `shas` has no entry — but the
    # legacy protocol expected callers to see ``"unknown"`` for these too
    # so the value propagates into the PipelineRun. Fill it now.
    for name in missing:
        shas.setdefault(name, "unknown")

    return shas, urls, sorted(missing)
```

Also update the shebang of `assemble_run.py`'s imports region: `configparser` and `subprocess` are added at the top of the module, after the existing stdlib imports (before the third-party `yaml`). Concretely, after `import shutil` at line 19, add `import subprocess` and after `import json` add `import configparser` — alphabetical.

- [ ] **Step 4: Run helper tests to verify pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py::TestDiscoverFrameworkSubmodules -v`
Expected: PASS — 5 tests. If any fails on `git init`, the CI runner does not have `git` available (unlikely — CI runs `actions/checkout` which requires git). Confirm `which git` if you need to debug.

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_run.py
git commit -m "$(cat <<'EOF'
feat(assemble): add discover_framework_submodules helper (#458)

Ports the framework-submodule discovery from the deleted
pipeline/prepare.py:_get_submodule_shas. Adds a pure helper that
returns (shas, urls, missing) for the pinned framework pair
(inference-sim, llm-d-benchmark).

- shas: git rev-parse HEAD per submodule dir; "unknown" fallback.
- urls: parsed from .gitmodules (declarative, doesn't require the
  submodule to be populated).
- missing: names whose dir doesn't exist — surfaced by the CLI wrapper
  as an operator warning.

No call site changed yet — Task 2 wires this into assemble().

Refs: #458
EOF
)"
```

---

## Task 2 — Wire the helper into `assemble()` and add operator warnings

**Files:**
- Modify: `pipeline/lib/assemble_run.py:505-510` (replace empty-dict deferral) and `:530` (add module-scope attr init)
- Modify: `pipeline/sim2real.py:387-392` (extend warning loop)
- Modify: `pipeline/tests/test_assemble_run.py` (append integration test on `TestAssembleRun`)

**Interfaces:**
- Consumes: `discover_framework_submodules(repo_root: Path) -> (shas, urls, missing)` from Task 1.
- Produces: side-band attribute `assemble_run.missing_submodules: list[str]` — cleared to `[]` on entry to `assemble()`, set at the point of PipelineRun generation, read by `pipeline/sim2real.py:_cmd_assemble` after the top-level call returns. Same protocol as `assemble_run.skipped_algorithms`.

- [ ] **Step 1: Write the failing integration test**

Append to `pipeline/tests/test_assemble_run.py` (inside `class TestAssembleRun`, matching the class's existing patterns):

```python
def test_pipelinerun_params_include_framework_submodule_shas(
    self, tmp_path, monkeypatch
):
    """assemble() must populate benchmarkGit*/blisGit* params in every
    generated PipelineRun YAML (issue #458). Verified end-to-end: build a
    minimal experiment + a fake framework repo layout, patch the helper's
    REPO_ROOT computation, run assemble, read one generated PipelineRun,
    check the four params."""
    exp_root, translation_hash, cluster_id = _make_experiment(tmp_path)

    # Fake a framework repo with both submodules initialized.
    fake_repo = tmp_path / "framework"
    fake_repo.mkdir()
    _write_yaml_string(fake_repo / ".gitmodules", (
        '[submodule "inference-sim"]\n'
        '\tpath = inference-sim\n'
        '\turl = https://example.com/inference-sim.git\n'
        '[submodule "llm-d-benchmark"]\n'
        '\tpath = llm-d-benchmark\n'
        '\turl = https://example.com/llm-d-benchmark.git\n'
    ))
    for name in ("inference-sim", "llm-d-benchmark"):
        sub = fake_repo / name
        sub.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=sub, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "--allow-empty", "-q", "-m", "init"],
            cwd=sub, check=True,
        )

    # Patch the helper's default repo_root — assemble() computes it from
    # __file__; the test overrides the module's private constant via
    # monkeypatching the discover_framework_submodules call chain.
    from pipeline.lib import assemble_run as _ar
    orig = _ar.discover_framework_submodules
    monkeypatch.setattr(
        _ar, "discover_framework_submodules",
        lambda repo_root=None: orig(fake_repo),
    )

    _ar.assemble_run(
        translation_hash=translation_hash, cluster_id=cluster_id,
        run_name="trial-1", experiment_root=exp_root,
        manifest_path=exp_root / "transfer.yaml",
        force=False, now_iso="2026-07-02T00:00:00Z",
    )

    pr_path = exp_root / "workspace/runs/trial-1/cluster/pipelinerun-wl-a-baseline.yaml"
    pr = yaml.safe_load(pr_path.read_text())
    params_by_name = {p["name"]: p["value"] for p in pr["spec"]["params"]}

    assert params_by_name["benchmarkGitRepoUrl"] == "https://example.com/llm-d-benchmark.git"
    assert params_by_name["blisGitRepoUrl"] == "https://example.com/inference-sim.git"
    assert params_by_name["benchmarkGitCommit"] not in ("", "unknown")
    assert params_by_name["blisGitCommit"] not in ("", "unknown")
    assert _ar.missing_submodules == []


def test_missing_submodule_populates_side_band_and_uses_unknown(
    self, tmp_path, monkeypatch
):
    """When a framework submodule is missing on disk, assemble() must
    still succeed (write PipelineRun YAMLs) with 'unknown' commit values,
    and expose the missing name via the side-band attr."""
    exp_root, translation_hash, cluster_id = _make_experiment(tmp_path)

    fake_repo = tmp_path / "framework"
    fake_repo.mkdir()
    _write_yaml_string(fake_repo / ".gitmodules", (
        '[submodule "inference-sim"]\n'
        '\tpath = inference-sim\n'
        '\turl = https://example.com/inference-sim.git\n'
        '[submodule "llm-d-benchmark"]\n'
        '\tpath = llm-d-benchmark\n'
        '\turl = https://example.com/llm-d-benchmark.git\n'
    ))
    # ONLY inference-sim is initialized; llm-d-benchmark is missing.
    sub = fake_repo / "inference-sim"
    sub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk"], cwd=sub, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=sub, check=True,
    )

    from pipeline.lib import assemble_run as _ar
    orig = _ar.discover_framework_submodules
    monkeypatch.setattr(
        _ar, "discover_framework_submodules",
        lambda repo_root=None: orig(fake_repo),
    )

    _ar.assemble_run(
        translation_hash=translation_hash, cluster_id=cluster_id,
        run_name="trial-1", experiment_root=exp_root,
        manifest_path=exp_root / "transfer.yaml",
        force=False, now_iso="2026-07-02T00:00:00Z",
    )

    pr_path = exp_root / "workspace/runs/trial-1/cluster/pipelinerun-wl-a-baseline.yaml"
    pr = yaml.safe_load(pr_path.read_text())
    params_by_name = {p["name"]: p["value"] for p in pr["spec"]["params"]}

    assert params_by_name["benchmarkGitCommit"] == "unknown"
    assert params_by_name["blisGitCommit"] not in ("", "unknown")
    # URL still comes through — parsed from .gitmodules regardless of dir state.
    assert params_by_name["benchmarkGitRepoUrl"] == "https://example.com/llm-d-benchmark.git"
    assert _ar.missing_submodules == ["llm-d-benchmark"]
```

Add the helper `_write_yaml_string` near `_write_yaml` at line 307 if not already present:

```python
def _write_yaml_string(path: Path, content: str) -> None:
    """Write raw string content — used for non-YAML formats like .gitmodules."""
    path.write_text(content)
```

Also ensure `subprocess` is imported at the top of `test_assemble_run.py`.

- [ ] **Step 2: Run new tests — expect FAIL**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py -k "pipelinerun_params_include_framework or missing_submodule_populates" -v`
Expected: FAIL — the assertions on `benchmarkGitRepoUrl` / `blisGitRepoUrl` will fail because the current code passes `{}` (URLs come back empty).

- [ ] **Step 3: Wire the helper into `assemble()`**

In `pipeline/lib/assemble_run.py`, add a module-level `_REPO_ROOT` computation near the top of the module (after imports, before the framework-submodule constant added in Task 1):

```python
# Framework repo root — three levels up from pipeline/lib/assemble_run.py.
# Mirrors pipeline/lib/cluster_ops.py:_REPO_ROOT. Used to locate framework
# submodules (inference-sim, llm-d-benchmark), which always live in the
# framework repo — NOT in the experiment repo.
_REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
```

Change the call site in `assemble()` from (lines 505-510):

```python
        # Step-1 does not read git submodule state — PR 3 will. Empty strings
        # accepted by tekton.make_pipelinerun_scenario; downstream chart handles
        # the absence.
        submodule_shas={},
        submodule_urls={},
```

to:

```python
        submodule_shas=submodule_shas,
        submodule_urls=submodule_urls,
```

Right before the `generate_pipelineruns(` call at line 496, add:

```python
    # Framework submodule discovery — populates the four benchmarkGit*/
    # blisGit* params on every generated PipelineRun (issue #458). Missing
    # submodules are recorded on the side-band attr for the CLI wrapper.
    submodule_shas, submodule_urls, missing_submodules = (
        discover_framework_submodules(_REPO_ROOT)
    )
    assemble_run.missing_submodules = missing_submodules  # type: ignore[attr-defined]
```

And add the module-scope initializer near the existing `assemble_run.skipped_algorithms = []` at line 530:

```python
assemble_run.missing_submodules = []  # type: ignore[attr-defined]
```

Also update `assemble_run()`'s docstring at line 362-363: after the line about `skipped_algorithms`, add:

```
    Missing framework submodules (``inference-sim``, ``llm-d-benchmark``)
    are similarly recorded on ``assemble_run.missing_submodules`` so the
    CLI wrapper can warn without failing the run — the four PipelineRun
    params fall back to ``"unknown"`` and the cluster-side clone will
    fail visibly at the right step.
```

And clear the attr on function entry (right after `assemble_run.skipped_algorithms = []` at line 367):

```python
    assemble_run.missing_submodules = []  # type: ignore[attr-defined]
```

- [ ] **Step 4: Wire the operator warning in `sim2real.py`**

In `pipeline/sim2real.py:387-392`, extend the existing `skipped_algorithms` warning block with a symmetric `missing_submodules` block. Change:

```python
    for name in getattr(_assemble_run_lib.assemble_run, "skipped_algorithms", []):
        print(
            f"warning: algorithm '{name}' declared in transfer.yaml but not "
            "in translation_output.json — skipped",
            file=sys.stderr,
        )
    print(f"assembled run {args.run}")
    return 0
```

to:

```python
    for name in getattr(_assemble_run_lib.assemble_run, "skipped_algorithms", []):
        print(
            f"warning: algorithm '{name}' declared in transfer.yaml but not "
            "in translation_output.json — skipped",
            file=sys.stderr,
        )
    for name in getattr(_assemble_run_lib.assemble_run, "missing_submodules", []):
        print(
            f"warning: framework submodule '{name}' not initialized — "
            "PipelineRun params will use 'unknown' as the commit SHA; "
            "cluster-side clone will fail. Run `git submodule update --init` "
            "in the sim2real repo to fix.",
            file=sys.stderr,
        )
    print(f"assembled run {args.run}")
    return 0
```

- [ ] **Step 5: Run new + adjacent tests — expect PASS**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py -v 2>&1 | tail -20`
Expected: 5 new discover tests + 2 new assemble-integration tests + all existing pass.

- [ ] **Step 6: Run full pipeline suite — regression check**

Run: `.venv/bin/python -m pytest pipeline/ 2>&1 | tail -5`
Expected: 1151 (baseline after #457) + 7 new = 1158 pass, 2 xfailed.

- [ ] **Step 7: Sanity-check on the real workspace (no cluster needed)**

Run:
```bash
grep -c "value: ''" pipeline/lib/assemble_run.py || true
grep -n "benchmarkGitRepoUrl\|blisGitCommit" pipeline/lib/tekton.py | head
```
Expected: `pipeline/lib/assemble_run.py` no longer has the empty-dict deferral. `tekton.py` still emits the four params.

- [ ] **Step 8: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/sim2real.py pipeline/tests/test_assemble_run.py
git commit -m "$(cat <<'EOF'
fix(assemble): populate benchmarkGit*/blisGit* PipelineRun params (#458)

The empty-dict placeholder at assemble_run.py:505-509 (introduced by
#453 as a deferred port) was never picked up by #446/#447/#448/#449.
Result: every PipelineRun generated by `sim2real assemble` on
refactor/v2-step-1 had empty benchmarkGitRepoUrl / benchmarkGitCommit
/ blisGitRepoUrl / blisGitCommit — the cluster-side git clone step
fails against those.

Wires the Task 1 discover_framework_submodules helper into assemble()
and drops the stale deferral comment. Missing submodules surface via a
new side-band attr `assemble_run.missing_submodules` — matched by a
corresponding warning loop in sim2real.py:_cmd_assemble (same shape as
skipped_algorithms).

Refs: #458
EOF
)"
```

---

## Task 3 — Sweep and README

**Files:**
- Modify: `pipeline/README.md` — add a note that framework submodules must be initialized before `sim2real assemble` (or the four params default to `"unknown"` with a warning).

**Interfaces:** documentation only.

- [ ] **Step 1: Grep for stale references**

Run:
```bash
grep -rnE "benchmarkGit|blisGit|_get_submodule_shas" pipeline/ docs/ .claude/skills/ CLAUDE.md README.md 2>&1 | head -30
```
Expected hits: `pipeline/lib/tekton.py` (params defined here — keep), `pipeline/lib/assemble_run.py` (populated after Task 2), the new tests, plus possibly design/plan docs (historical — leave alone). No `_get_submodule_shas` references should remain in `refactor/v2-step-1` code (the function was deleted with prepare.py by #453). Nothing to update in this step.

- [ ] **Step 2: Add README note**

In `pipeline/README.md`, find the `## Assemble a run` section (around line 161). Locate the `**Inputs read:**` block and add a bullet at the end:

```markdown
- `<sim2real-repo>/.gitmodules` and `<sim2real-repo>/{inference-sim,llm-d-benchmark}/` — read for the framework submodules' clone URLs and HEAD SHAs, which populate the `benchmarkGitRepoUrl` / `benchmarkGitCommit` / `blisGitRepoUrl` / `blisGitCommit` params in every generated PipelineRun. Initialize with `git submodule update --init` in the sim2real repo before running `sim2real assemble`; missing submodules fall back to `"unknown"` commit values (a warning is printed) and the cluster-side `git clone` will then fail visibly.
```

- [ ] **Step 3: Verify structure holds**

Run: `grep -n "^## \|^### " pipeline/README.md 2>&1 | head -20`
Expected: heading order is unchanged (no ranges shifted). Also skim the appended bullet visually.

- [ ] **Step 4: Commit**

```bash
git add pipeline/README.md
git commit -m "$(cat <<'EOF'
docs(pipeline): document framework submodule requirement for assemble (#458)

The `sim2real assemble` step now reads inference-sim and llm-d-benchmark
submodule state to populate PipelineRun git params. Document the
initialization requirement and the "unknown" fallback so operators know
what the warning means.

Refs: #458
EOF
)"
```

---

## Task 4 — Final verification and PR

**Files:** none.

- [ ] **Step 1: Run lint gate**

Run: `.venv/bin/ruff check pipeline/ .claude/skills/ --select F 2>&1 | tail -3`
Expected: `All checks passed!`

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest pipeline/ 2>&1 | tail -5`
Expected: 1158 passed, 2 xfailed.

- [ ] **Step 3: Confirm parent repo has no leaked changes**

Run:
```bash
git status --short && echo '---' && git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status --short | head
```
Expected: only worktree shows changes; parent-repo output limited to the pre-existing `M tektonc-data-collection` / `?? …` items from `git status` at session start.

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin refactor/v2-step-1-issue-458-submodule-git-params
gh pr create --base refactor/v2-step-1 \
    --title "fix(assemble): populate benchmarkGit*/blisGit* PipelineRun params (#458)" \
    --body-file <PR body markdown>
```

PR body includes:
- `Closes #458.`
- Summary: dropped deferral picked up; framework submodule discovery moved from deleted prepare.py to assemble_run.py.
- Files changed.
- AC mapping with `[x]`/`[ ]` per item — flag the real-cluster gate as unverifiable from the harness (same posture as PRs #455-#457).
- Sweep notes.
- Verification (test counts, lint clean).
- Non-obvious choices worth reviewer attention:
  1. `.gitmodules` parsing (declarative) rather than `git remote get-url origin` (runtime) — chosen because it works even when the submodule dir is missing.
  2. Side-band warning attr (`missing_submodules`) matches the existing `skipped_algorithms` protocol — no behavior change for the `assemble()` return contract.
  3. Legacy `component` submodule branch of `_get_submodule_shas` is deliberately not ported — component image ref comes from translation, not git.

If `gh pr create` fails with a token error, retry with `unset GITHUB_TOKEN GH_TOKEN`.

---

## Self-review

### Spec coverage against AC

| AC item | Task | Notes |
|---------|------|-------|
| `sim2real assemble` populates the four params from `.gitmodules` + framework submodule HEADs | Task 2 | End-to-end integration test asserts populated values. |
| Graceful degradation with `"unknown"` + operator warning | Task 2 | `test_missing_submodule_populates_side_band_and_uses_unknown` covers both the value fallback and the warning surfacing. |
| Tests: both present / one missing / both missing / URL parsing | Task 1 | `TestDiscoverFrameworkSubmodules` has 5 tests covering all four scenarios + an extra-submodule-ignored guard. |
| Real-cluster gate (BYO demo produces `per_request_lifecycle_metrics.json`) | (deferred) | Not automatable from the harness. Called out in PR body — manual gate before merging. |
| `pipeline/README.md` documents the submodule requirement | Task 3 | Bullet appended to the `## Assemble a run` section's `**Inputs read:**` block. |

### Placeholder scan

- No "TBD", "TODO", "implement later".
- Every code block is a complete diff or complete function body.
- Test bodies show actual assertions.

### Type consistency

- `discover_framework_submodules(repo_root: Path) -> tuple[dict[str, str], dict[str, str], list[str]]` — signature used consistently across Task 1 tests, Task 2 wiring, and Task 2 tests.
- Side-band attr name `missing_submodules` used consistently (matches the existing `skipped_algorithms` pattern).
- `_FRAMEWORK_SUBMODULE_NAMES` used consistently.

### Open items (surface to user before implementation)

None — the deferral pattern is textbook (helper + call site + side-band warning + tests), the AC's real-cluster gate is a known manual step, and there are no scope ambiguities.
