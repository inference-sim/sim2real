# step-2 PR 5: `sim2real build` + shared build library + docs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `sim2real build` command, extract shared build primitives from `deploy.py:_cmd_build` into `pipeline/lib/build.py`, add the incomplete-translation prereq to `sim2real assemble`, and update the docs. Closes step-2's demo path (issue #469).

**Architecture:** New `pipeline/lib/build.py` module hosts three genuinely-shared primitives — `dispatch_buildkit_build` (wraps the `pipeline/scripts/build-epp.sh` invocation), `probe_image_digest` (skopeo inspect → digest or None), and `atomic_write_json` (tempfile + os.replace). `deploy.py:_cmd_build` is refactored to route its buildkit invocation and metadata write through the library — no behavior change. The new `sim2real build` command composes those primitives with the translation-scoped machinery: registry probe → build (unless probe hit + no `--force-rebuild`) → post-build probe for digest → atomic write of `image_ref`/`image_digest` back into `translations/<hash>/translation_output.json`. `sim2real assemble` gains a fail-fast check that every declared algorithm's `image_ref` is non-null before writing anything.

**Tech Stack:** Python 3.10+, pytest, PyYAML, subprocess (skopeo + kubectl via build-epp.sh), atomic-write via `tempfile.mkstemp` + `os.replace`.

## Global Constraints

- **CI-visible test paths.** `.github/workflows/test.yml` enumerates test files by explicit path. Any new test file must be listed there — CI does not glob.
- **Ruff lint (F-only).** All new code must pass `ruff check pipeline/ --select F` (pyflakes unused-import / undefined-name / redefined-name).
- **Path discipline.** The worktree root is `.claude/worktrees/issue-469-sim2real-build/`. Every edit uses that prefix. After every commit run `git status` in the worktree AND `git -C <parent> status` to confirm no leaks into the parent repo.
- **No workspace-artifact fixes.** `workspace/` is generated. Any bug fix belongs in the generating stage, not the artifact.
- **Field naming.** `sim2real build` reads `registry` and `repo_name` from `<workspace>/setup_config.json` (produced by `setup.py`), matching `deploy.py:_cmd_build`'s existing behavior. The design doc's reference to `transfer.yaml:epp_image.build.{hub,name}` is aspirational — that field does not exist in the current v3 manifest (`pipeline/lib/manifest.py`). Route via `setup_config.json` for now; a future task can migrate both `deploy.py` and `sim2real build` together if the field is added.
- **Image-ref shape.** `<registry>/<repo_name>:<translation_hash[:12]>-<algo>`. Sourced per-invocation from `setup_config.json:registry`, `setup_config.json:repo_name`, resolved `translation_hash`, and the algorithm name.
- **skopeo probe fail-safe.** Any probe failure of any kind (missing binary, network, auth, timeout) is treated as "image absent → build." The command NEVER refuses to build because a probe failed.
- **Post-build inspect softens.** If the second (post-build) probe fails, the build is considered successful, `image_ref` is recorded, and `image_digest` is written as `null` with a warning. The image was pushed — probe just couldn't record the digest.
- **Atomic writes only.** Every write to `translation_output.json` must go through `atomic_write_json` (tempfile in the same directory + `os.replace`). Never overwrite in place.
- **Deploy tests unchanged.** `pipeline/tests/test_deploy_build.py` must pass unchanged after the `_cmd_build` refactor. This is the parity guarantee.

---

## File Structure

### New files
- `pipeline/lib/build.py` — new module. Hosts `compose_image_ref`, `check_skopeo`, `probe_image_digest`, `dispatch_buildkit_build`, `atomic_write_json`. ~120 lines. Pure library — no CLI, no argparse.
- `pipeline/tests/test_build.py` — tests for `pipeline/lib/build.py` primitives AND for the `sim2real build` command. Split into two `TestCase` classes: `TestBuildLibrary` and `TestSim2realBuildCommand`. ~500 lines.

### Modified files
- `pipeline/sim2real.py` — add `_cmd_build` (~120 lines), extend argparse with `build` subcommand (~30 lines), extend `_cmd_assemble` with incomplete-translation prereq (~15 lines), hoist `_atomic_write_json` to import from `pipeline.lib.build` (~5-line delta).
- `pipeline/deploy.py` — refactor `_cmd_build` to call `build.dispatch_buildkit_build` (~10-line change) and `build.atomic_write_json` for `_write_build_metadata` (~5-line change). No new features. Existing tests must pass unchanged.
- `pipeline/tests/test_assemble_run.py` OR `pipeline/tests/test_sim2real.py` — new tests for the incomplete-translation `assemble` prereq. Prefer `test_sim2real.py` because the check lives in `_cmd_assemble`.
- `pipeline/README.md` — new "sim2real build" section under "sim2real.py"; ensure the assemble section (line 209) matches; add end-to-end example under "End-of-step-1 BYO demo" for the skill-driven path.
- `CLAUDE.md` — ensure the `pipeline/lib/build.py` module and the `sim2real build` command appear in their respective tables.
- `.github/workflows/test.yml` — add `pipeline/tests/test_build.py` to the explicit test-path list.

### Untouched
- `pipeline/lib/translation_ref.py` — resolver already exists, no changes needed.
- `pipeline/lib/slicer.py` — `translation_hash_with_sources` already exists, no changes.
- `pipeline/scripts/build-epp.sh` — NOT modified in this PR. `sim2real build` passes `--run-dir <translation_dir>` as a valid-directory placeholder (the script only reads run-metadata from `--run-dir` when `--image-ref` and `--source-dir` are absent; those are always provided).

---

## Task Right-Sizing

Seven tasks, each independently reviewable and testable. Task boundaries drawn where a reviewer could reject one without affecting another:

1. **Library primitives (`build.py`, unit-tested with mocked subprocess)** — foundational; every subsequent task depends on it.
2. **`_cmd_build` refactor in deploy.py** — parity guarantee. Reviewer can gate on `test_deploy_build.py` passing unchanged.
3. **`sim2real build` argparse + dispatch scaffolding + prereq checks** — CLI surface visible.
4. **`sim2real build` per-algorithm probe → build → write loop** — the core new behavior.
5. **Assemble prereq: null-image_ref fail-early** — small, isolated behavior change.
6. **Docs + CI listing** — text-only; low risk.
7. **Sweep + verify** — final gate.

---

## Interfaces (contracts between tasks)

Every symbol referenced later is defined here so tasks can be read out of order.

### `pipeline/lib/build.py`

```python
class BuildError(Exception):
    """Raised for build-time failures that should exit the CLI with code 2."""


def compose_image_ref(registry: str, repo: str, tag: str) -> str:
    """Return ``<registry>/<repo>:<tag>``. All three must be non-empty."""


def check_skopeo() -> None:
    """Raise ``BuildError`` with a platform-appropriate install hint if
    ``skopeo`` is not on PATH. Returns None on success."""


def probe_image_digest(image_ref: str, *, timeout: float = 30.0) -> str | None:
    """Return ``sha256:HEX`` from ``skopeo inspect --raw docker://<ref>``,
    or ``None`` on any failure (network, auth, timeout, missing tag,
    missing binary, invalid JSON). Never raises. Callers use ``None`` as
    the fail-safe signal to (re)build."""


def dispatch_buildkit_build(
    *,
    image_ref: str,
    build_id: str,
    namespace: str,
    source_dir: "Path",
    run_dir: "Path",
    repo_root: "Path",
) -> int:
    """Invoke ``pipeline/scripts/build-epp.sh`` with the given flags. Returns
    the exit code (0 = success). Never raises; caller inspects return code
    and can call skopeo again for digest recording."""


def atomic_write_json(path: "Path", data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a tempfile in the same
    directory + ``os.replace``. POSIX-atomic. Creates parent dirs. Raises
    ``OSError`` on filesystem faults (caller decides how to surface)."""
```

### `pipeline/sim2real.py`

```python
def _cmd_build(args) -> int:
    """Resolve --translation, prereqs, iterate algorithms, probe → build →
    write per-algo. Returns 0 on all-success (including all-skip), 2 on
    any prereq failure or build failure."""


# _cmd_assemble gains this check BEFORE calling assemble_run.assemble_run:
#   For every algo declared in transfer.yaml:algorithms whose name is
#   also in translation_output.json:algorithms, verify image_ref is not
#   None. If any are None → exit 2 with the design's error string.
```

### Argparse extension in `build_parser()`

```python
b = sub.add_parser("build", help="Build EPP images for a translation")
b.add_argument("--translation", required=True, metavar="REF",
               help="alias, hash prefix, or full translation hash")
b.add_argument("--force-rebuild", action="store_true",
               help="Rebuild and push even if the registry already has the tag")
b.add_argument("--skip-build", action="store_true",
               help="Skip all probe + build activity (assemble will fail if image_ref is null)")
```

---

## Task 1: `pipeline/lib/build.py` primitives (TDD)

**Files:**
- Create: `pipeline/lib/build.py`
- Create: `pipeline/tests/test_build.py` (initial scaffolding + `TestBuildLibrary` class)

**Interfaces:**
- Produces: `compose_image_ref`, `check_skopeo`, `probe_image_digest`, `atomic_write_json`, `BuildError` (see Interfaces section above)
- Consumes: nothing (leaf module)

- [ ] **Step 1: Create the test file scaffold**

```python
# pipeline/tests/test_build.py
"""Tests for pipeline/lib/build.py — shared build primitives, and the
sim2real build command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.lib import build, layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestComposeImageRef:
    def test_composes_registry_repo_tag(self):
        assert build.compose_image_ref(
            "quay.io/user", "sched", "abc123-softref"
        ) == "quay.io/user/sched:abc123-softref"

    def test_rejects_empty_registry(self):
        with pytest.raises(build.BuildError, match="registry"):
            build.compose_image_ref("", "repo", "tag")

    def test_rejects_empty_repo(self):
        with pytest.raises(build.BuildError, match="repo"):
            build.compose_image_ref("reg", "", "tag")

    def test_rejects_empty_tag(self):
        with pytest.raises(build.BuildError, match="tag"):
            build.compose_image_ref("reg", "repo", "")
```

Run: `python -m pytest pipeline/tests/test_build.py::TestComposeImageRef -v`
Expected: FAIL — `pipeline/lib/build.py` does not exist.

- [ ] **Step 2: Run tests to confirm they fail**

Run: `python -m pytest pipeline/tests/test_build.py -v`
Expected: `ModuleNotFoundError: No module named 'pipeline.lib.build'`

- [ ] **Step 3: Create the module with `compose_image_ref` + `BuildError`**

```python
# pipeline/lib/build.py
"""Shared build primitives for sim2real.

Consumers:
  - ``pipeline/sim2real.py:_cmd_build`` (step-2, translation-scoped)
  - ``pipeline/deploy.py:_cmd_build`` (step-1, run-scoped) — routes its
    buildkit invocation through this module.

Every primitive is failure-tolerant in the "fail-safe → rebuild" direction:
skopeo probe returns ``None`` on any error, dispatch returns the exit
code without raising, atomic_write only raises on filesystem faults.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class BuildError(Exception):
    """Raised for build-time failures that should exit the CLI with code 2."""


def compose_image_ref(registry: str, repo: str, tag: str) -> str:
    """Return ``<registry>/<repo>:<tag>``. All three must be non-empty."""
    if not registry:
        raise BuildError("registry must not be empty")
    if not repo:
        raise BuildError("repo must not be empty")
    if not tag:
        raise BuildError("tag must not be empty")
    return f"{registry}/{repo}:{tag}"
```

- [ ] **Step 4: Confirm `TestComposeImageRef` passes**

Run: `python -m pytest pipeline/tests/test_build.py::TestComposeImageRef -v`
Expected: 4 passed.

- [ ] **Step 5: Add `TestCheckSkopeo` tests**

```python
class TestCheckSkopeo:
    def test_success_when_skopeo_on_path(self):
        with patch("shutil.which", return_value="/usr/local/bin/skopeo"):
            build.check_skopeo()  # returns None; must not raise

    def test_raises_with_install_hint_when_missing(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(build.BuildError, match="skopeo not found"):
                build.check_skopeo()

    def test_error_includes_install_commands(self):
        with patch("shutil.which", return_value=None):
            with pytest.raises(build.BuildError) as excinfo:
                build.check_skopeo()
            msg = str(excinfo.value)
            assert "brew install skopeo" in msg
            assert "apt install skopeo" in msg
            assert "dnf install skopeo" in msg
```

Run: `python -m pytest pipeline/tests/test_build.py::TestCheckSkopeo -v`
Expected: FAIL — `check_skopeo` not defined.

- [ ] **Step 6: Implement `check_skopeo`**

Append to `pipeline/lib/build.py`:

```python
import shutil


def check_skopeo() -> None:
    """Raise ``BuildError`` with a platform-appropriate install hint if
    ``skopeo`` is not on PATH."""
    if shutil.which("skopeo") is None:
        raise BuildError(
            "skopeo not found on PATH — required for registry probe. Install: "
            "brew install skopeo, apt install skopeo, or dnf install skopeo"
        )
```

- [ ] **Step 7: Confirm `TestCheckSkopeo` passes**

Run: `python -m pytest pipeline/tests/test_build.py::TestCheckSkopeo -v`
Expected: 3 passed.

- [ ] **Step 8: Add `TestProbeImageDigest` tests**

```python
class TestProbeImageDigest:
    def _mock_run(self, stdout: str = "", stderr: str = "",
                  returncode: int = 0, raise_exc: Exception | None = None):
        """Return a patch context that stubs subprocess.run."""
        def fake_run(*args, **kwargs):
            if raise_exc is not None:
                raise raise_exc
            return subprocess.CompletedProcess(
                args=args[0], returncode=returncode,
                stdout=stdout, stderr=stderr,
            )
        return patch("pipeline.lib.build.subprocess.run", side_effect=fake_run)

    def test_returns_digest_on_success(self):
        payload = json.dumps({
            "Digest": "sha256:abcdef0123456789" + "0" * 48
        })
        with self._mock_run(stdout=payload, returncode=0):
            digest = build.probe_image_digest("quay.io/u/r:t")
        assert digest == "sha256:abcdef0123456789" + "0" * 48

    def test_returns_none_on_nonzero_exit(self):
        with self._mock_run(stdout="", stderr="manifest unknown", returncode=1):
            assert build.probe_image_digest("quay.io/u/r:missing") is None

    def test_returns_none_on_invalid_json(self):
        with self._mock_run(stdout="not json{{{", returncode=0):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_returns_none_on_json_without_digest(self):
        with self._mock_run(stdout=json.dumps({"other": "value"}), returncode=0):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_returns_none_on_timeout(self):
        with self._mock_run(raise_exc=subprocess.TimeoutExpired(cmd=["skopeo"], timeout=30)):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_returns_none_on_file_not_found(self):
        with self._mock_run(raise_exc=FileNotFoundError()):
            assert build.probe_image_digest("quay.io/u/r:t") is None

    def test_calls_skopeo_inspect_with_docker_scheme(self):
        with patch(
            "pipeline.lib.build.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps({"Digest": "sha256:" + "0" * 64}),
                stderr="",
            ),
        ) as mock_run:
            build.probe_image_digest("quay.io/u/r:t")
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "skopeo"
            assert call_args[1] == "inspect"
            assert "docker://quay.io/u/r:t" in call_args
```

Run: `python -m pytest pipeline/tests/test_build.py::TestProbeImageDigest -v`
Expected: FAIL — `probe_image_digest` not defined.

- [ ] **Step 9: Implement `probe_image_digest`**

Append to `pipeline/lib/build.py`:

```python
def probe_image_digest(image_ref: str, *, timeout: float = 30.0) -> str | None:
    """Return the image digest via ``skopeo inspect`` or ``None`` on any failure.

    Never raises. Every non-happy path (network, auth, timeout, invalid
    JSON, missing tag, missing binary) returns ``None`` — the caller uses
    that as the fail-safe signal to (re)build.
    """
    try:
        result = subprocess.run(
            ["skopeo", "inspect", f"docker://{image_ref}"],
            capture_output=True, text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    digest = parsed.get("Digest")
    if not isinstance(digest, str) or not digest:
        return None
    return digest
```

- [ ] **Step 10: Confirm `TestProbeImageDigest` passes**

Run: `python -m pytest pipeline/tests/test_build.py::TestProbeImageDigest -v`
Expected: 7 passed.

- [ ] **Step 11: Add `TestAtomicWriteJson` tests**

```python
class TestAtomicWriteJson:
    def test_writes_pretty_json(self, tmp_path):
        target = tmp_path / "out.json"
        build.atomic_write_json(target, {"a": 1, "b": [2, 3]})
        loaded = json.loads(target.read_text())
        assert loaded == {"a": 1, "b": [2, 3]}
        text = target.read_text()
        assert "  " in text  # indented, not compact

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "c.json"
        build.atomic_write_json(target, {"x": 1})
        assert target.exists()
        assert json.loads(target.read_text()) == {"x": 1}

    def test_overwrites_existing_atomically(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text('{"old": true}')
        build.atomic_write_json(target, {"new": True})
        assert json.loads(target.read_text()) == {"new": True}

    def test_cleans_up_tempfile_on_write_failure(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text('{"placeholder": true}')

        # Force os.replace to fail so we exercise the cleanup path.
        with patch("pipeline.lib.build.os.replace", side_effect=OSError("boom")):
            with pytest.raises(OSError, match="boom"):
                build.atomic_write_json(target, {"new": True})
        # No stray .tmp-*.json siblings should remain.
        siblings = [p.name for p in tmp_path.iterdir() if p.name != "out.json"]
        assert siblings == [], f"leaked tempfile(s): {siblings}"
```

Run: `python -m pytest pipeline/tests/test_build.py::TestAtomicWriteJson -v`
Expected: FAIL — `atomic_write_json` not defined.

- [ ] **Step 12: Implement `atomic_write_json`**

Append to `pipeline/lib/build.py`:

```python
def atomic_write_json(path: "Path", data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a tempfile + os.replace.

    POSIX-atomic on same-filesystem writes. Creates parent dirs. Raises
    ``OSError`` on filesystem faults; callers surface the error and exit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

- [ ] **Step 13: Confirm `TestAtomicWriteJson` passes**

Run: `python -m pytest pipeline/tests/test_build.py::TestAtomicWriteJson -v`
Expected: 4 passed.

- [ ] **Step 14: Run all pipeline/lib tests to confirm no regressions**

Run: `python -m pytest pipeline/tests/ -v -x --ignore=pipeline/tests/test_deploy_remote.py`
Expected: all pass (some tests may be skipped for env reasons — none should FAIL).

- [ ] **Step 15: Commit**

```bash
git add pipeline/lib/build.py pipeline/tests/test_build.py
git commit -m "feat(build): add pipeline/lib/build.py — image-ref, skopeo probe, atomic write

Shared primitives extracted for step-2's sim2real build command; deploy.py's
_cmd_build will route through the same module in a follow-up task. Refs #469."
```

---

## Task 2: `dispatch_buildkit_build` primitive (TDD)

**Files:**
- Modify: `pipeline/lib/build.py`
- Modify: `pipeline/tests/test_build.py`

**Interfaces:**
- Produces: `dispatch_buildkit_build(image_ref, build_id, namespace, source_dir, run_dir, repo_root) -> int`
- Consumes: `subprocess.run` (external — `pipeline/scripts/build-epp.sh`)

- [ ] **Step 1: Add `TestDispatchBuildkitBuild` tests**

Append to `pipeline/tests/test_build.py`:

```python
class TestDispatchBuildkitBuild:
    def _patch_run(self, returncode: int = 0):
        return patch(
            "pipeline.lib.build.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=returncode, stdout="", stderr="",
            ),
        )

    def test_invokes_build_script_with_all_flags(self, tmp_path):
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        repo_root = tmp_path / "repo"
        (repo_root / "pipeline" / "scripts").mkdir(parents=True)
        (repo_root / "pipeline" / "scripts" / "build-epp.sh").write_text("#!/bin/bash\n")

        with self._patch_run(returncode=0) as mock_run:
            rc = build.dispatch_buildkit_build(
                image_ref="reg/repo:tag-algo",
                build_id="build-xyz",
                namespace="sim2real-slot1",
                source_dir=source_dir,
                run_dir=run_dir,
                repo_root=repo_root,
            )
        assert rc == 0
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "bash"
        assert str(repo_root / "pipeline" / "scripts" / "build-epp.sh") in cmd
        assert "--image-ref" in cmd
        assert "reg/repo:tag-algo" in cmd
        assert "--namespace" in cmd
        assert "sim2real-slot1" in cmd
        assert "--source-dir" in cmd
        assert str(source_dir) in cmd
        assert "--run-dir" in cmd
        assert str(run_dir) in cmd
        assert "--run-name" in cmd
        assert "build-xyz" in cmd

    def test_returns_nonzero_on_script_failure(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "run").mkdir()
        (tmp_path / "repo" / "pipeline" / "scripts").mkdir(parents=True)
        (tmp_path / "repo" / "pipeline" / "scripts" / "build-epp.sh").touch()
        with self._patch_run(returncode=42):
            rc = build.dispatch_buildkit_build(
                image_ref="r/r:t", build_id="b", namespace="ns",
                source_dir=tmp_path / "src", run_dir=tmp_path / "run",
                repo_root=tmp_path / "repo",
            )
        assert rc == 42

    def test_raises_when_build_script_missing(self, tmp_path):
        with pytest.raises(build.BuildError, match="build-epp.sh"):
            build.dispatch_buildkit_build(
                image_ref="r/r:t", build_id="b", namespace="ns",
                source_dir=tmp_path, run_dir=tmp_path,
                repo_root=tmp_path,
            )

    def test_cwd_is_repo_root(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "run").mkdir()
        (tmp_path / "pipeline" / "scripts").mkdir(parents=True)
        (tmp_path / "pipeline" / "scripts" / "build-epp.sh").touch()
        with self._patch_run(returncode=0) as mock_run:
            build.dispatch_buildkit_build(
                image_ref="r/r:t", build_id="b", namespace="ns",
                source_dir=tmp_path / "src", run_dir=tmp_path / "run",
                repo_root=tmp_path,
            )
        assert mock_run.call_args.kwargs.get("cwd") == tmp_path
```

Run: `python -m pytest pipeline/tests/test_build.py::TestDispatchBuildkitBuild -v`
Expected: FAIL — `dispatch_buildkit_build` not defined.

- [ ] **Step 2: Implement `dispatch_buildkit_build`**

Append to `pipeline/lib/build.py`:

```python
def dispatch_buildkit_build(
    *,
    image_ref: str,
    build_id: str,
    namespace: str,
    source_dir: "Path",
    run_dir: "Path",
    repo_root: "Path",
) -> int:
    """Invoke ``pipeline/scripts/build-epp.sh`` and return its exit code.

    Passes every arg the script requires. The script does the actual
    buildkit-pod submit, source-copy PVC upload, and registry-secret
    check. Never raises on non-zero exit — the caller inspects the
    return code (and may retry, log, or record a null-digest result).

    Raises ``BuildError`` only when ``build-epp.sh`` itself is missing.
    """
    build_script = repo_root / "pipeline" / "scripts" / "build-epp.sh"
    if not build_script.exists():
        raise BuildError(f"build-epp.sh not found at {build_script}")
    result = subprocess.run(
        [
            "bash", str(build_script),
            "--run-dir", str(run_dir),
            "--run-name", build_id,
            "--namespace", namespace,
            "--image-ref", image_ref,
            "--source-dir", str(source_dir),
        ],
        check=False,
        cwd=repo_root,
    )
    return result.returncode
```

- [ ] **Step 3: Confirm `TestDispatchBuildkitBuild` passes**

Run: `python -m pytest pipeline/tests/test_build.py::TestDispatchBuildkitBuild -v`
Expected: 4 passed.

- [ ] **Step 4: Run the full `test_build.py` file to confirm the module is coherent**

Run: `python -m pytest pipeline/tests/test_build.py -v`
Expected: 18 passed (4 compose + 3 skopeo + 7 probe + 4 atomic-write + 4 dispatch).

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/build.py pipeline/tests/test_build.py
git commit -m "feat(build): add dispatch_buildkit_build primitive

Wraps pipeline/scripts/build-epp.sh invocation with named args. Callers
(deploy.py:_cmd_build, sim2real build in later tasks) inspect the
returned exit code. Refs #469."
```

---

## Task 3: Refactor `deploy.py:_cmd_build` to route through `pipeline/lib/build.py`

**Files:**
- Modify: `pipeline/deploy.py` (lines 236-254 for `_write_build_metadata`, lines 403-413 for the buildkit invocation)

**Interfaces:**
- Consumes: `pipeline.lib.build.dispatch_buildkit_build`, `pipeline.lib.build.atomic_write_json`
- Produces: no new symbols. Behavior-preserving refactor.

**Rationale:** The parity guarantee (acceptance criterion 1: "no behavior change; existing `deploy.py` tests pass unchanged") is enforced by `pipeline/tests/test_deploy_build.py`. This task should not touch any test file.

- [ ] **Step 1: Read the current `_cmd_build` inline subprocess call**

Verify that lines 403-413 of `pipeline/deploy.py` are:
```python
result = run(
    ["bash", str(build_script),
     "--run-dir", str(run_dir),
     "--run-name", run_name,
     "--namespace", namespace,
     "--image-ref", ref,
     "--source-dir", str(source_dir)],
    check=False,
    cwd=REPO_ROOT,
)
```

- [ ] **Step 2: Replace the inline call with the library call**

In `pipeline/deploy.py:_cmd_build`, near the top of the function (after the existing imports), add:

```python
from pipeline.lib import build
```

Actually the file's existing imports are at the module level (line 52). Add `build` to the existing import so lib imports stay grouped:

Change line 52 from:
```python
from pipeline.lib import cluster_ops, layout
```
to:
```python
from pipeline.lib import build, cluster_ops, layout
```

Then replace the inline subprocess block (approximately lines 403-413) with:

```python
rc = build.dispatch_buildkit_build(
    image_ref=ref,
    build_id=run_name,
    namespace=namespace,
    source_dir=source_dir,
    run_dir=run_dir,
    repo_root=REPO_ROOT,
)
```

And update the immediate `if result.returncode != 0:` check (approximately line 437) to reference `rc`:

```python
if rc != 0:
    err(f"Image build failed for {ref} — see output above")
    sys.exit(1)
```

- [ ] **Step 3: Refactor `_write_build_metadata` to use `atomic_write_json`**

Change the body of `_write_build_metadata` (lines 236-254) to write atomically. Current implementation:
```python
meta_path.write_text(json.dumps(meta, indent=2))
```

Replace with:
```python
build.atomic_write_json(meta_path, meta)
```

Retain all the existing early-return behavior (missing file, unparseable JSON).

**Note:** `atomic_write_json` writes a trailing newline while `write_text(json.dumps(..., indent=2))` did not. `test_deploy_build.py::TestWriteBuildMetadata` reads the file with `json.loads`, which ignores trailing whitespace — so the tests remain green. If any test asserts on raw bytes (search first before commit), leave `_write_build_metadata` using its inline write instead of hooking into `atomic_write_json`.

- [ ] **Step 4: Run the deploy build tests to confirm parity**

Run: `python -m pytest pipeline/tests/test_deploy_build.py -v`
Expected: All 11 tests pass unchanged.

- [ ] **Step 5: Run the full pipeline test suite to confirm no wider regression**

Run: `python -m pytest pipeline/tests/ -v -x --ignore=pipeline/tests/test_deploy_remote.py`
Expected: all pass.

- [ ] **Step 6: Verify no leaks into parent repo**

Run: `git status` (in worktree) — should show only `pipeline/deploy.py` modified.
Run: `git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status` — should show only pre-existing untracked files (nothing modified).

- [ ] **Step 7: Commit**

```bash
git add pipeline/deploy.py
git commit -m "refactor(deploy): route _cmd_build through pipeline/lib/build.py

No behavior change — dispatch_buildkit_build wraps the same build-epp.sh
invocation, atomic_write_json replaces the inline JSON write in
_write_build_metadata. Existing test_deploy_build.py passes unchanged.
Refs #469."
```

---

## Task 4: `sim2real build` argparse + prereq checks (TDD)

**Files:**
- Modify: `pipeline/sim2real.py` — add `build` subparser in `build_parser`, add `_cmd_build` skeleton, wire into `main`
- Modify: `pipeline/tests/test_build.py` — add `TestSim2realBuildParser` and `TestSim2realBuildPrereqs` classes

**Interfaces:**
- Produces: `sim2real build --translation REF [--force-rebuild] [--skip-build]` CLI surface; `_cmd_build(args) -> int`
- Consumes: `pipeline.lib.build.check_skopeo`, `pipeline.lib.translation_ref.resolve_translation_ref`, `pipeline.lib.translation_ref.read_translation_output`, `pipeline.lib.layout.setup_config_path`

- [ ] **Step 1: Add `TestSim2realBuildParser` tests**

Append to `pipeline/tests/test_build.py`:

```python
class TestSim2realBuildParser:
    def _parse(self, argv):
        from pipeline import sim2real
        return sim2real.build_parser().parse_args(argv)

    def test_translation_required(self):
        with pytest.raises(SystemExit):
            self._parse(["build"])

    def test_accepts_translation_alias(self):
        args = self._parse(["build", "--translation", "softreflective"])
        assert args.command == "build"
        assert args.translation == "softreflective"
        assert args.force_rebuild is False
        assert args.skip_build is False

    def test_force_rebuild_flag(self):
        args = self._parse(["build", "--translation", "abc", "--force-rebuild"])
        assert args.force_rebuild is True

    def test_skip_build_flag(self):
        args = self._parse(["build", "--translation", "abc", "--skip-build"])
        assert args.skip_build is True
```

Run: `python -m pytest pipeline/tests/test_build.py::TestSim2realBuildParser -v`
Expected: FAIL — `build` subcommand doesn't exist yet.

- [ ] **Step 2: Add the argparse block to `pipeline/sim2real.py`**

Insert into `build_parser()` after the `trans = sub.add_parser("translate", ...)` block (search for `mode = trans.add_mutually_exclusive_group()` — insert after its `mode.add_argument("--resume", ...)` line):

```python
b = sub.add_parser(
    "build",
    help="Build EPP images for each algorithm in a translation",
)
b.add_argument(
    "--translation",
    required=True,
    metavar="REF",
    help="alias, hash prefix, or full translation hash",
)
b.add_argument(
    "--force-rebuild",
    action="store_true",
    help="Rebuild and push even when the registry already has the tag",
)
b.add_argument(
    "--skip-build",
    action="store_true",
    help=(
        "Skip probe + build for every algorithm. Downstream 'sim2real "
        "assemble' will fail if any image_ref is still null."
    ),
)
```

- [ ] **Step 3: Confirm the parser tests pass**

Run: `python -m pytest pipeline/tests/test_build.py::TestSim2realBuildParser -v`
Expected: 4 passed.

- [ ] **Step 4: Add `TestSim2realBuildPrereqs` tests (fail-early paths)**

Append to `pipeline/tests/test_build.py`:

```python
import yaml
from pipeline import sim2real


def _make_workspace(tmp_path, *, registry="quay.io/user", repo_name="sched"):
    """Create a workspace/setup_config.json with registry/repo_name."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "setup_config.json").write_text(json.dumps({
        "registry": registry, "repo_name": repo_name,
    }))
    return ws


def _make_translation(tmp_path, *, thash="a" * 64, alias="softref",
                      algorithms=None, source="skill"):
    """Materialize a workspace/translations/<hash>/translation_output.json.

    Each algorithm dict is stored verbatim under algorithms[i]. Defaults
    to a single algo named 'softref' with image_ref=None.
    """
    ws = tmp_path / "workspace"
    tdir = ws / "translations" / thash
    (tdir / "generated" / "softref").mkdir(parents=True, exist_ok=True)
    if algorithms is None:
        algorithms = [{
            "name": "softref", "source_path": "algorithms/softref.py",
            "source_sha256": "0" * 64, "config_path": None,
            "image_ref": None, "image_digest": None,
        }]
    for algo in algorithms:
        gd = tdir / "generated" / algo["name"]
        gd.mkdir(parents=True, exist_ok=True)
        (gd / f"{algo['name']}_output.json").write_text(json.dumps({"stub": True}))
    tout = tdir / "translation_output.json"
    tout.write_text(json.dumps({
        "version": 1, "translation_hash": thash, "source": source,
        "alias": alias, "algorithms": algorithms,
        "created_at": "2026-07-02T00:00:00Z",
    }))
    return thash


class TestSim2realBuildPrereqs:
    def test_missing_skopeo_exits_2(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        _make_translation(tmp_path, alias="softref")
        with patch("shutil.which", return_value=None):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "skopeo not found" in capsys.readouterr().err

    def test_unknown_translation_exits_2(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        (tmp_path / "workspace" / "translations").mkdir(parents=True)
        with patch("shutil.which", return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "nope",
            ])
        assert rc == 2
        assert "no translations" in capsys.readouterr().err

    def test_missing_algo_output_exits_2(self, tmp_path, capsys):
        """Prereq: translation completeness — every algo needs
        generated/<algo>/<algo>_output.json on disk."""
        _make_workspace(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")
        # Delete the algo output file, keep translation_output.json.
        algo_out = (
            tmp_path / "workspace" / "translations" / thash
            / "generated" / "softref" / "softref_output.json"
        )
        algo_out.unlink()
        with patch("shutil.which", return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        err_out = capsys.readouterr().err
        assert "incomplete" in err_out
        assert "softref" in err_out

    def test_missing_registry_exits_2(self, tmp_path, capsys):
        _make_workspace(tmp_path, registry="", repo_name="")
        _make_translation(tmp_path, alias="softref")
        with patch("shutil.which", return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "registry" in capsys.readouterr().err.lower()

    def test_missing_setup_config_exits_2(self, tmp_path, capsys):
        """No workspace/setup_config.json at all → prereq error."""
        (tmp_path / "workspace").mkdir()
        (tmp_path / "workspace" / "translations").mkdir()
        _make_translation(tmp_path, alias="softref")
        with patch("shutil.which", return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "setup_config" in capsys.readouterr().err.lower() or \
               "registry" in capsys.readouterr().err.lower()
```

Run: `python -m pytest pipeline/tests/test_build.py::TestSim2realBuildPrereqs -v`
Expected: FAIL — `_cmd_build` doesn't exist yet.

- [ ] **Step 5: Add `_cmd_build` skeleton + prereq checks to `pipeline/sim2real.py`**

Insert after `_cmd_translate`:

```python
def _cmd_build(args) -> int:
    """Build EPP images for every algorithm in a translation.

    Prerequisites (checked in order):
      1. skopeo on PATH (unless --skip-build).
      2. Translation ref resolves via translation_ref.resolve_translation_ref.
      3. Every algorithm has generated/<algo>/<algo>_output.json on disk.
      4. workspace/setup_config.json has non-empty registry and repo_name.
    """
    from pipeline.lib import build, translation_ref

    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )
    layout.set_experiment_root(str(exp_root))

    if not args.skip_build:
        try:
            build.check_skopeo()
        except build.BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        translation_hash = translation_ref.resolve_translation_ref(args.translation)
    except translation_ref.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    tout_path = layout.translation_output_path(translation_hash)
    try:
        tout = translation_ref.read_translation_output(tout_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    algorithms = tout.get("algorithms") or []
    if not algorithms:
        print(
            f"error: translation {translation_hash} has no algorithms recorded",
            file=sys.stderr,
        )
        return 2

    # Completeness check: every algo must have generated/<algo>/<algo>_output.json.
    tdir = layout.translation_dir(translation_hash)
    missing = [
        a["name"] for a in algorithms
        if not (tdir / "generated" / a["name"] / f"{a['name']}_output.json").exists()
    ]
    if missing:
        print(
            f"error: translation {translation_hash} incomplete — "
            f"missing outputs for: {', '.join(missing)}; "
            f"run '/sim2real-translate' then 'sim2real translate --resume'",
            file=sys.stderr,
        )
        return 2

    # Registry / repo prereq — read from setup_config.json (mirrors deploy.py).
    setup_cfg_path = layout.setup_config_path()
    if not setup_cfg_path.exists():
        print(
            "error: workspace/setup_config.json not found — run setup.py first",
            file=sys.stderr,
        )
        return 2
    try:
        setup_cfg = json.loads(setup_cfg_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    registry = setup_cfg.get("registry") or ""
    repo_name = setup_cfg.get("repo_name") or ""
    if not registry or not repo_name:
        print(
            "error: workspace/setup_config.json is missing 'registry' or "
            "'repo_name' — re-run setup.py with --registry",
            file=sys.stderr,
        )
        return 2

    # Task 5 fills in the per-algo probe/build/write loop. For now, exit 0
    # with a placeholder log so the argparse layer is exercisable.
    print(
        f"error: build loop not implemented; prereqs passed for "
        f"{translation_hash} ({len(algorithms)} algorithms)",
        file=sys.stderr,
    )
    return 2
```

Then wire into `main()`. Find the block:
```python
    if args.command == "translate":
        return _cmd_translate(args)
    ...
    return 1
```

And add a `build` branch:
```python
    if args.command == "build":
        return _cmd_build(args)
```

- [ ] **Step 6: Confirm all prereq tests pass**

Run: `python -m pytest pipeline/tests/test_build.py::TestSim2realBuildPrereqs -v`
Expected: 5 passed. Every prereq failure exits 2 with a specific error message; the "success" path currently exits 2 with the placeholder message.

- [ ] **Step 7: Run the full test file**

Run: `python -m pytest pipeline/tests/test_build.py -v`
Expected: all currently-defined tests pass.

- [ ] **Step 8: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_build.py
git commit -m "feat(sim2real): add 'build' subcommand skeleton + prereq checks

Argparse block, translation-ref resolution, completeness check, and
setup_config.json validation. Per-algorithm build loop lands in the
next commit. Refs #469."
```

---

## Task 5: `sim2real build` per-algorithm probe → build → write loop (TDD)

**Files:**
- Modify: `pipeline/sim2real.py` — replace the placeholder tail of `_cmd_build` with the real loop
- Modify: `pipeline/tests/test_build.py` — add `TestSim2realBuildLoop` class

**Interfaces:**
- Consumes: everything from `pipeline.lib.build`
- Produces: mutations to `translations/<hash>/translation_output.json` — sets `algorithms[i].image_ref` and `algorithms[i].image_digest`

### Behavior contract (per-algorithm)

For each algorithm A:

1. Compute `image_ref = "<registry>/<repo>:<translation_hash[:12]>-<A.name>"` via `build.compose_image_ref`.
2. If `args.skip_build` — skip everything. Do NOT touch `translation_output.json`. Log `skipped (--skip-build) for <A.name>`.
3. If `A.image_ref` is already set to `image_ref` AND `A.image_digest` is non-null AND `--force-rebuild` is NOT set — skip (idempotency). Log `already-built for <A.name>: <digest>`.
4. If `--force-rebuild` NOT set AND `probe_image_digest(image_ref)` returns a digest — treat as "already in registry", write it back to `A.image_ref`/`A.image_digest` (atomic), log `probe hit for <A.name>: <digest>`, and continue.
5. Otherwise: call `build.dispatch_buildkit_build(...)` with the composed image_ref. If exit code != 0, log the failure and return 2.
6. After a successful build: call `probe_image_digest(image_ref)` a second time to record the pushed digest. If probe returns None, log `built <image_ref>; digest not recorded (probe failed)`, and write `image_digest: null`. If probe returns a digest, log `built <image_ref>: <digest>`.
7. Atomic-write the mutated `translation_output.json` back to disk (per-algo write — write after every algo, so a mid-run failure leaves prior algos recorded).

Return 0 after all algorithms complete; 2 on any single build failure (loop stops at the first failure).

**Namespace:** use `cluster_config.namespaces[0]` (the primary slot namespace) from `workspace/clusters/<cluster_id>/cluster_config.json`. Load via `pipeline.lib.cluster_ops.read_cluster_config`. Cluster id: use `layout.list_cluster_ids()` — must return exactly one; if zero, error; if >1, error. Follows the "single cluster per workspace" step-0 assumption.

**Source dir:** `<experiment_root>/<repo_name>`. Deploy.py uses the same expression.

**Build ID:** `f"sim2real-build-{translation_hash[:8]}-{algo_name}"` — passed as `--run-name` to build-epp.sh, used for pod name / PVC path uniqueness. Different from `translation_hash[:12]` (which is the image tag prefix).

- [ ] **Step 1: Add `TestSim2realBuildLoop` tests**

Append to `pipeline/tests/test_build.py`:

```python
class TestSim2realBuildLoop:
    """Tests the per-algorithm probe → build → write loop.

    Each test:
      - stubs out shutil.which so check_skopeo passes
      - stubs pipeline.lib.build.probe_image_digest and .dispatch_buildkit_build
      - materializes a workspace with translations/<hash>/ and a cluster_config.json
      - asserts on returncode, on the mutated translation_output.json, and on
        which subprocess calls were made
    """

    def _make_cluster_config(self, tmp_path, cluster_id="test-cluster",
                             namespaces=("sim2real-slot1",)):
        cdir = tmp_path / "workspace" / "clusters" / cluster_id
        cdir.mkdir(parents=True)
        (cdir / "cluster_config.json").write_text(json.dumps({
            "cluster_id": cluster_id,
            "namespaces": list(namespaces),
            "is_openshift": False,
            "storage_class": "",
            "secret_names": {"hf_token": "hf-token"},
            "workspaces": [],
        }))

    def _read_translation_output(self, tmp_path, thash):
        return json.loads(
            (tmp_path / "workspace" / "translations" / thash
             / "translation_output.json").read_text()
        )

    def test_probe_hit_skips_build(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   return_value="sha256:" + "d" * 64) as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        mock_probe.assert_called()
        mock_build.assert_not_called()  # probe hit → no build

        data = self._read_translation_output(tmp_path, thash)
        algo = data["algorithms"][0]
        assert algo["image_ref"] == f"quay.io/user/sched:{thash[:12]}-softref"
        assert algo["image_digest"] == "sha256:" + "d" * 64

    def test_probe_miss_triggers_build(self, tmp_path):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        probe_returns = [None, "sha256:" + "b" * 64]  # miss, then post-build hit
        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns) as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        assert mock_probe.call_count == 2  # pre + post
        mock_build.assert_called_once()

        data = self._read_translation_output(tmp_path, thash)
        algo = data["algorithms"][0]
        assert algo["image_ref"] == f"quay.io/user/sched:{thash[:12]}-softref"
        assert algo["image_digest"] == "sha256:" + "b" * 64

    def test_force_rebuild_ignores_probe_hit(self, tmp_path):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   return_value="sha256:" + "c" * 64), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref", "--force-rebuild",
            ])
        assert rc == 0
        mock_build.assert_called_once()  # forced despite probe hit

    def test_skip_build_bypasses_everything(self, tmp_path):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        with patch("pipeline.lib.build.probe_image_digest") as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as mock_build, \
             patch("pipeline.lib.build.check_skopeo") as mock_check:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref", "--skip-build",
            ])
        assert rc == 0
        mock_check.assert_not_called()
        mock_probe.assert_not_called()
        mock_build.assert_not_called()

        # translation_output.json unchanged
        data = self._read_translation_output(tmp_path, thash)
        assert data["algorithms"][0]["image_ref"] is None

    def test_probe_auth_failure_treated_as_miss(self, tmp_path):
        """probe_image_digest returns None on any failure — including auth."""
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        probe_returns = [None, "sha256:" + "1" * 64]  # auth-fail miss, then post-build
        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0) as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        mock_build.assert_called_once()  # miss → build

    def test_post_build_probe_failure_records_null_digest(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        probe_returns = [None, None]  # miss then post-build failure
        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=0):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        data = self._read_translation_output(tmp_path, thash)
        algo = data["algorithms"][0]
        assert algo["image_ref"] == f"quay.io/user/sched:{thash[:12]}-softref"
        assert algo["image_digest"] is None
        out = capsys.readouterr().out
        assert "digest not recorded" in out or "digest not recorded" in \
            capsys.readouterr().err

    def test_build_failure_returns_2(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref")

        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   return_value=None), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   return_value=1):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        assert "build failed" in capsys.readouterr().err.lower()

    def test_per_algo_writes_are_atomic_and_incremental(self, tmp_path):
        """Two-algo translation: first algo succeeds, second fails.

        After the run, the first algo's image_ref/image_digest are recorded
        and persisted; the second's are still None."""
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = _make_translation(tmp_path, alias="softref", algorithms=[
            {"name": "algo1", "source_path": "a1.py", "source_sha256": "0"*64,
             "config_path": None, "image_ref": None, "image_digest": None},
            {"name": "algo2", "source_path": "a2.py", "source_sha256": "1"*64,
             "config_path": None, "image_ref": None, "image_digest": None},
        ])

        # probe: miss algo1, post-build hit for algo1, miss algo2
        probe_returns = [None, "sha256:" + "1" * 64, None]
        # dispatch: succeed for algo1 (rc=0), fail for algo2 (rc=1)
        dispatch_returns = [0, 1]

        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest",
                   side_effect=probe_returns), \
             patch("pipeline.lib.build.dispatch_buildkit_build",
                   side_effect=dispatch_returns):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 2
        data = self._read_translation_output(tmp_path, thash)
        algo1 = next(a for a in data["algorithms"] if a["name"] == "algo1")
        algo2 = next(a for a in data["algorithms"] if a["name"] == "algo2")
        assert algo1["image_ref"] == f"quay.io/user/sched:{thash[:12]}-algo1"
        assert algo1["image_digest"] == "sha256:" + "1" * 64
        assert algo2["image_ref"] is None
        assert algo2["image_digest"] is None

    def test_idempotent_when_image_ref_and_digest_already_recorded(self, tmp_path):
        """A translation with a known image_ref+digest doesn't probe or build."""
        _make_workspace(tmp_path)
        self._make_cluster_config(tmp_path)
        thash = "a" * 64
        # Pre-set image_ref and image_digest to their expected values.
        thash = _make_translation(tmp_path, thash=thash, alias="softref", algorithms=[
            {"name": "softref", "source_path": "s.py", "source_sha256": "0"*64,
             "config_path": None,
             "image_ref": f"quay.io/user/sched:{thash[:12]}-softref",
             "image_digest": "sha256:" + "e" * 64},
        ])

        with patch("shutil.which", return_value="/usr/bin/skopeo"), \
             patch("pipeline.lib.build.probe_image_digest") as mock_probe, \
             patch("pipeline.lib.build.dispatch_buildkit_build") as mock_build:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "softref",
            ])
        assert rc == 0
        mock_probe.assert_not_called()
        mock_build.assert_not_called()
```

Run: `python -m pytest pipeline/tests/test_build.py::TestSim2realBuildLoop -v`
Expected: FAIL — build loop not implemented (placeholder returns 2).

- [ ] **Step 2: Implement the per-algorithm loop in `_cmd_build`**

Replace the placeholder tail of `_cmd_build` in `pipeline/sim2real.py` (from the `# Task 5 fills in ...` comment through the end of the function) with:

```python
    # Cluster resolution — matches step-0 "single cluster per workspace".
    from pipeline.lib import cluster_ops
    cluster_ids = layout.list_cluster_ids()
    if not cluster_ids:
        print(
            "error: no cluster provisioned; run "
            "'cluster.py provision <cluster_id>' first",
            file=sys.stderr,
        )
        return 2
    if len(cluster_ids) > 1:
        print(
            f"error: multiple clusters found ({cluster_ids}); "
            "sim2real assumes a single cluster per workspace",
            file=sys.stderr,
        )
        return 2
    try:
        cluster_config = cluster_ops.read_cluster_config(cluster_ids[0])
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    namespaces = cluster_config.get("namespaces") or []
    if not namespaces:
        print(
            "error: cluster_config.json has no namespaces; "
            "re-run 'cluster.py provision --namespaces NS1,...'",
            file=sys.stderr,
        )
        return 2
    build_namespace = namespaces[0]
    source_dir = exp_root / repo_name

    tag_prefix = translation_hash[:12]
    any_failure = False
    for algo in algorithms:
        algo_name = algo["name"]
        try:
            image_ref = build.compose_image_ref(
                registry, repo_name, f"{tag_prefix}-{algo_name}"
            )
        except build.BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        # Idempotency — already recorded with the composed ref and a digest?
        if (algo.get("image_ref") == image_ref
                and algo.get("image_digest")
                and not args.force_rebuild
                and not args.skip_build):
            print(f"already built: {image_ref} ({algo['image_digest']})")
            continue

        if args.skip_build:
            print(f"skipped (--skip-build) for {algo_name}")
            continue

        # Pre-build probe (skip if --force-rebuild).
        if not args.force_rebuild:
            digest = build.probe_image_digest(image_ref)
            if digest is not None:
                algo["image_ref"] = image_ref
                algo["image_digest"] = digest
                build.atomic_write_json(tout_path, tout)
                print(f"probe hit: {image_ref} ({digest})")
                continue

        # Build via buildkit.
        build_id = f"sim2real-build-{translation_hash[:8]}-{algo_name}"
        rc = build.dispatch_buildkit_build(
            image_ref=image_ref,
            build_id=build_id,
            namespace=build_namespace,
            source_dir=source_dir,
            run_dir=tdir,
            repo_root=_REPO_ROOT,
        )
        if rc != 0:
            print(
                f"error: build failed for {algo_name} (image_ref={image_ref})",
                file=sys.stderr,
            )
            any_failure = True
            break

        # Post-build probe.
        digest = build.probe_image_digest(image_ref)
        algo["image_ref"] = image_ref
        algo["image_digest"] = digest
        build.atomic_write_json(tout_path, tout)
        if digest is None:
            print(
                f"built {image_ref}; digest not recorded (probe failed)"
            )
        else:
            print(f"built {image_ref} ({digest})")

    return 2 if any_failure else 0
```

- [ ] **Step 3: Also update the initial imports in `_cmd_build`**

Ensure the `from pipeline.lib import build, translation_ref` line already includes `build` (added in Task 4 Step 5). This step just verifies — no code change needed if already correct.

- [ ] **Step 4: Confirm all loop tests pass**

Run: `python -m pytest pipeline/tests/test_build.py::TestSim2realBuildLoop -v`
Expected: 9 passed.

- [ ] **Step 5: Run the full test_build.py file to confirm everything is coherent**

Run: `python -m pytest pipeline/tests/test_build.py -v`
Expected: all tests pass.

- [ ] **Step 6: Run the full pipeline test suite**

Run: `python -m pytest pipeline/tests/ -v -x --ignore=pipeline/tests/test_deploy_remote.py`
Expected: all pass (no regressions).

- [ ] **Step 7: Path leak check**

Run: `git status` (worktree — should show only `pipeline/sim2real.py` and `pipeline/tests/test_build.py` modified).
Run: `git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status` — pre-existing untracked only.

- [ ] **Step 8: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_build.py
git commit -m "feat(sim2real): implement 'build' per-algorithm probe/build/write loop

Registry probe → skip on hit unless --force-rebuild → dispatch buildkit
→ post-build probe for digest → atomic write per algo. Loop stops at
first build failure so partial state is preserved. Refs #469."
```

---

## Task 6: `sim2real assemble` incomplete-translation prereq (TDD)

**Files:**
- Modify: `pipeline/sim2real.py` — extend `_cmd_assemble` with the pre-write check
- Modify: `pipeline/tests/test_sim2real.py` — add tests

**Interfaces:**
- Consumes: `pipeline.lib.translation_ref.read_translation_output` (already used by resolve path)
- Produces: no new symbols; new failure mode in `_cmd_assemble`.

**Behavior:** After resolving the translation ref (existing code) and before calling `_assemble_run_lib.assemble_run`, read `translations/<hash>/translation_output.json` and check every declared algorithm has a non-null `image_ref`. If any are null, exit 2 with the design's error string:

```
translation <alias-or-hash> not built for algorithms: <names> — run 'sim2real build --translation <alias-or-hash>' first
```

Note: `read_translation_output` guarantees `image_ref` key exists on every `algorithms[i]` (fills from legacy top-level when absent) — so `.get("image_ref")` is the exact check. `None` (or missing top-level for legacy without any image_ref) triggers the error.

- [ ] **Step 1: Add tests for the new check**

Append to `pipeline/tests/test_sim2real.py` (or create a new class at the bottom):

```python
class TestAssembleIncompleteTranslationCheck:
    """--translation with null image_ref on any algorithm → exit 2."""

    def _minimal_assemble_setup(self, tmp_path, image_ref=None):
        """Materialize the minimum inputs for _cmd_assemble to reach the check.

        Writes:
          - workspace/setup_config.json (registry/repo_name)
          - workspace/clusters/c/cluster_config.json
          - workspace/translations/<hash>/translation_output.json
          - <exp_root>/transfer.yaml
        Returns translation_hash.
        """
        ws = tmp_path / "workspace"
        (ws / "clusters" / "c").mkdir(parents=True)
        (ws / "clusters" / "c" / "cluster_config.json").write_text(json.dumps({
            "cluster_id": "c",
            "namespaces": ["sim2real-slot1"],
            "is_openshift": False,
            "storage_class": "",
            "secret_names": {"hf_token": "hf-token"},
            "workspaces": [],
        }))
        thash = "a" * 64
        tdir = ws / "translations" / thash
        (tdir / "generated" / "softref").mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1, "translation_hash": thash, "source": "skill",
            "alias": "softref-alias",
            "algorithms": [{
                "name": "softref", "source_path": "algorithms/softref.py",
                "source_sha256": "0" * 64, "config_path": None,
                "image_ref": image_ref, "image_digest": None,
            }],
            "created_at": "2026-07-02T00:00:00Z",
        }))
        # A minimal-but-valid transfer.yaml.
        (tmp_path / "algorithms").mkdir()
        (tmp_path / "algorithms" / "softref.py").write_text("# stub\n")
        (tmp_path / "transfer.yaml").write_text(yaml.safe_dump({
            "kind": "sim2real-transfer", "version": 3,
            "scenario": "softref-alias",
            "baselines": [{"name": "base", "scenario": "baselines/base.yaml"}],
            "algorithms": [
                {"name": "softref", "source": "algorithms/softref.py",
                 "defaults": "base"}
            ],
            "component": {"repo": "example.com/x/y", "kind": "scorer"},
            "context": {"text": "", "files": []},
        }))
        return thash

    def test_null_image_ref_fails_early_with_actionable_error(
        self, tmp_path, capsys,
    ):
        self._minimal_assemble_setup(tmp_path, image_ref=None)
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "assemble", "--translation", "softref-alias",
            "--cluster", "c", "--run", "trial-1",
        ])
        assert rc == 2
        err_out = capsys.readouterr().err
        assert "not built for algorithms" in err_out
        assert "softref" in err_out
        assert "sim2real build --translation" in err_out
        # No writes to runs/ happened.
        assert not (tmp_path / "workspace" / "runs").exists()

    def test_non_null_image_ref_passes_check(self, tmp_path):
        """When image_ref is set, the check passes and assemble proceeds
        into the (mocked) assemble_run.

        Point of this test: verifying the check does NOT short-circuit
        when the ref is set. We stub the underlying assemble_run to keep
        the test focused on the check itself."""
        self._minimal_assemble_setup(
            tmp_path, image_ref="reg/repo:hash-softref"
        )
        with patch.object(
            sim2real._assemble_run_lib, "assemble_run"
        ) as mock_assemble:
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "assemble", "--translation", "softref-alias",
                "--cluster", "c", "--run", "trial-1",
            ])
        # If the check errored we would exit 2; passing check means we
        # reach assemble_run, which is mocked so it returns None → rc=0.
        assert rc == 0
        mock_assemble.assert_called_once()
```

The imports at the top of `test_sim2real.py` already include `pytest`, `yaml`, `json`, `sim2real`. Add `from unittest.mock import patch` if not already present.

Run: `python -m pytest pipeline/tests/test_sim2real.py::TestAssembleIncompleteTranslationCheck -v`
Expected: FAIL — the check is not in `_cmd_assemble` yet.

- [ ] **Step 2: Add the check to `_cmd_assemble`**

In `pipeline/sim2real.py:_cmd_assemble`, after the existing `translation_hash = translation_ref.resolve_translation_ref(args.translation)` line and before `_assemble_run_lib.assemble_run(...)`:

```python
# Fail-early: every algorithm declared in transfer.yaml that is also
# recorded in translation_output.json must have a non-null image_ref
# (design §Commands §sim2real assemble). Skill-driven translations
# start with image_ref: null; the operator must run 'sim2real build'
# before 'sim2real assemble'.
tout_path = layout.translation_output_path(translation_hash)
try:
    tout = translation_ref.read_translation_output(tout_path)
except (OSError, ValueError) as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 2

# Load the manifest just enough to know which algorithms are declared;
# the full validation happens inside assemble_run itself.
try:
    _manifest_data = _manifest.load_manifest(manifest_path)
except _manifest.ManifestError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 2

declared_names = {a["name"] for a in _manifest_data.get("algorithms") or []}
recorded_by_name = {
    a["name"]: a for a in (tout.get("algorithms") or [])
    if isinstance(a, dict) and a.get("name")
}
unbuilt = [
    name for name in sorted(declared_names & recorded_by_name.keys())
    if recorded_by_name[name].get("image_ref") is None
]
if unbuilt:
    print(
        f"error: translation {args.translation} not built for algorithms: "
        f"{', '.join(unbuilt)} — run 'sim2real build --translation "
        f"{args.translation}' first",
        file=sys.stderr,
    )
    return 2
```

Add the imports at the top of `_cmd_assemble` (near the existing `from pipeline.lib import translation_ref`):

```python
from pipeline.lib import manifest as _manifest
```

- [ ] **Step 3: Confirm the new tests pass**

Run: `python -m pytest pipeline/tests/test_sim2real.py::TestAssembleIncompleteTranslationCheck -v`
Expected: 2 passed.

- [ ] **Step 4: Run the full `test_sim2real.py` to confirm no regressions**

Run: `python -m pytest pipeline/tests/test_sim2real.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full pipeline test suite**

Run: `python -m pytest pipeline/tests/ -v -x --ignore=pipeline/tests/test_deploy_remote.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(assemble): fail early when translation has null image_ref

Every algorithm declared in transfer.yaml that is also recorded in
translation_output.json must have a non-null image_ref. Prevents
'sim2real assemble' silently succeeding on a not-yet-built skill-driven
translation. Refs #469."
```

---

## Task 7: Docs (README, CLAUDE.md) and CI listing

**Files:**
- Modify: `pipeline/README.md`
- Modify: `CLAUDE.md`
- Modify: `.github/workflows/test.yml`

**Interfaces:**
- No code changes. Text-only.

- [ ] **Step 1: Add `sim2real build` section to `pipeline/README.md`**

Insert a new `## Build translation images` section between "Assemble a run" (ends ~line 220) and "deploy.py" (~line 223):

```markdown
## Build translation images

Once a skill-driven translation is checkpointed (`sim2real translate` +
`/sim2real-translate` skill run), `sim2real build` compiles the per-algorithm
Go plugin sources into container images and pushes them to your registry.
Each algorithm gets its own image tagged
`<translation_hash[:12]>-<algorithm_name>`.

```bash
python pipeline/sim2real.py build \
    --translation REF \
    [--force-rebuild] \
    [--skip-build] \
    [--experiment-root PATH]
```

| Flag | Required | Notes |
|------|----------|-------|
| `--translation REF` | yes | alias, hash prefix, or full 64-char hash (resolver from PR 2) |
| `--force-rebuild` | no | Rebuild and re-push every algorithm even if the registry already has the tag. |
| `--skip-build` | no | Skip all probe + build activity. Downstream `sim2real assemble` will fail if any `image_ref` is still null. Useful when you know the images already exist and want to bypass the probe. |

**Prerequisites (fail-early):**

- `skopeo` on PATH (`brew install skopeo` / `apt install skopeo` / `dnf install skopeo`).
- Translation completeness: every algorithm declared in `transfer.yaml` must have `translations/<hash>/generated/<algo>/<algo>_output.json` on disk (i.e. `/sim2real-translate` was run).
- `workspace/setup_config.json` has non-empty `registry` and `repo_name` (populated by `setup.py`).
- A single provisioned cluster (`workspace/clusters/<id>/cluster_config.json`) with a `namespaces` slot — buildkit runs in `namespaces[0]`.
- `<experiment-root>/<repo_name>/` exists and contains the component source.

**Behavior per algorithm:**

1. Compose `image_ref = <registry>/<repo>:<translation_hash[:12]>-<algo>` from `setup_config.json` fields and the resolved hash.
2. **Idempotency short-circuit**: if the algorithm's recorded `image_ref` already equals the composed value AND `image_digest` is non-null AND `--force-rebuild` is not set, skip. Prints `already built: <ref> (<digest>)`.
3. **Pre-build registry probe**: `skopeo inspect docker://<ref>`. Any success returns the digest; the digest is written back to `translation_output.json` and the build is skipped. Any failure (network, auth, missing tag, timeout) is treated as "absent → build" (fail-safe).
4. **Buildkit dispatch**: submits an in-cluster `moby/buildkit:latest` pod that reads component source from a PVC and pushes to the target registry via `registry-secret` (provisioned by `cluster.py provision`). Follows the same `pipeline/scripts/build-epp.sh` code path `deploy.py:_cmd_build` has always used.
5. **Post-build digest inspect**: `skopeo inspect` runs a second time to record the pushed digest. On success, `image_digest` is set. On failure, the build is still considered successful (the image was pushed); `image_digest` is recorded as `null` and a warning is printed. Digest can be back-filled by a later `sim2real build --force-rebuild`.
6. **Atomic writeback**: `translations/<hash>/translation_output.json:algorithms[i].image_ref` and `image_digest` are written after every algorithm via tempfile-and-rename. A mid-run failure preserves prior algorithms' recorded state.

Return code: `0` on all-success (including all-idempotent). `2` on any prereq failure or the first build failure (loop stops there — subsequent algorithms are not attempted).

**Common failure modes:**

| Symptom | Cause | Fix |
|---------|-------|-----|
| `skopeo not found on PATH` | Local prereq missing | Install skopeo per the hint in the error message. |
| `translation <hash> incomplete — missing outputs for: X` | `/sim2real-translate` skill has not been run for algorithm X | Run `/sim2real-translate` at the Claude prompt, then re-run `sim2real translate --resume` to verify, then `sim2real build`. |
| `workspace/setup_config.json is missing 'registry' or 'repo_name'` | `setup.py` was not run, or was run without `--registry` | `python pipeline/setup.py --registry quay.io/username --repo-name <repo>` |
| `build failed for <algo> (image_ref=...)` | Buildkit pod exited non-zero — usually a Go compile error, a missing PVC, or a bad `registry-secret` | Inspect `kubectl logs epp-build-sim2real-build-<hash8>-<algo> -n <namespace>` for the compiler output. |
| `translation <ref> not built for algorithms: X` when running `sim2real assemble` | You ran `assemble` before `build` | Run `sim2real build --translation <ref>` first. |

---
```

Also, update the existing "End-of-step-1 BYO demo" section to include a companion "Step-2 skill-driven demo" subsection. Look for `### Success criterion` near line 448; insert a new subsection `## End-of-step-2 skill-driven demo` after the BYO demo, showing:

1. `cluster.py provision` (one-time)
2. `setup.py`
3. `sim2real translate` (initial run — writes checkpoint)
4. `/sim2real-translate` skill (fills in `generated/<algo>/<algo>_output.json`)
5. `sim2real translate --resume` (validates)
6. `sim2real build --translation <alias>`
7. `sim2real assemble --translation <alias> --cluster <id> --run <name>`
8. `deploy.py run` + `deploy.py collect`
9. Same success criterion (per-workload per-algorithm `per_request_lifecycle_metrics.json`)

Keep the copy tight — this is a step-by-step, not a tutorial. About 50 lines.

- [ ] **Step 2: Update `CLAUDE.md`**

Ensure the `pipeline/lib/` table has an entry for `build.py`:

```markdown
| `build.py` | Shared build primitives (image-ref, skopeo probe, atomic write, buildkit dispatch); consumed by `sim2real build` and `deploy.py:_cmd_build` |
```

Ensure the "Workspace Artifacts" table row for `translations/<hash>/translation_output.json` mentions `sim2real build` as a writer:

```markdown
| `translations/<hash>/translation_output.json` (step-2 shape: top-level `alias`; per-algo `image_ref`/`image_digest`/`config_path`/`source_path`/`source_sha256` inside `algorithms[i]`. Step-1 legacy files with top-level `image_ref` remain readable via `translation_ref.read_translation_output`) | `sim2real translation register` (BYO); `sim2real translate` (skill; writes null image fields); `sim2real build` (fills image_ref/image_digest per algo) | `sim2real assemble`, `sim2real list translations`, `deploy.py` |
```

Ensure the "Transfer Pipeline" prose mentions the step-2 flow:

```markdown
Step-2 adds the skill-driven flow that step-1 stubbed:

```
cluster.py provision  (one-time)
                   ↓
setup.py → sim2real translate → /sim2real-translate skill → sim2real translate --resume → sim2real build → sim2real assemble → deploy.py
```

The BYO flow (`sim2real translation register` in place of the four-step translate+skill+build sequence) remains supported.
```

- [ ] **Step 3: Add `test_build.py` to CI**

Open `.github/workflows/test.yml`. Locate the pytest command block that lists individual test files. Add `pipeline/tests/test_build.py \` in alphabetical position (right after `test_assemble_run.py`, before or after existing entries as ordering allows).

Also ensure `pipeline/tests/test_translation_ref.py` and `pipeline/tests/test_translate.py` are already listed (they should be from PRs 2 and 3). Add them if missing.

- [ ] **Step 4: Run ruff to catch any lint issues introduced**

Run: `ruff check pipeline/ --select F`
Expected: clean (no F errors).

If installed, also: `ruff check pipeline/ .claude/skills/ --select F` (matches CI).

- [ ] **Step 5: Run the full pipeline test suite one final time**

Run: `python -m pytest pipeline/tests/ -v -x --ignore=pipeline/tests/test_deploy_remote.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pipeline/README.md CLAUDE.md .github/workflows/test.yml
git commit -m "docs: document sim2real build; wire test_build.py into CI

- pipeline/README.md: new 'Build translation images' section + skill-driven
  demo subsection.
- CLAUDE.md: pipeline/lib/build.py table entry; translation_output.json
  writer list updated to include sim2real build.
- .github/workflows/test.yml: enumerate pipeline/tests/test_build.py.

Refs #469."
```

---

## Task 8: Sweep + verify

**Files:**
- No code changes (this is a verification / discovery task).

- [ ] **Step 1: Sweep for stale references**

Search for references to symbols and paths modified in this PR:

```bash
grep -rn "prepare.py" .claude/skills/ docs/ pipeline/README.md CLAUDE.md 2>&1 | head -20
grep -rn "_cmd_build" pipeline/ 2>&1 | head -20
grep -rn "epp_image.build" pipeline/ docs/ 2>&1 | head -20
grep -rn "sim2real build" pipeline/ docs/ CLAUDE.md 2>&1 | head -20
```

For each hit, decide: stale (update), still accurate (leave), or unrelated. Note any updates in the commit message.

- [ ] **Step 2: Final ruff pass**

```bash
ruff check pipeline/ --select F
```

Expected: clean.

- [ ] **Step 3: Final pytest pass**

```bash
python -m pytest pipeline/tests/ -v --ignore=pipeline/tests/test_deploy_remote.py
```

Expected: all pass.

- [ ] **Step 4: Verify path discipline one more time**

```bash
git status
git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
```

Expected: worktree shows all the intended changes (`pipeline/sim2real.py`, `pipeline/deploy.py`, `pipeline/lib/build.py`, `pipeline/tests/test_build.py`, `pipeline/tests/test_sim2real.py`, `pipeline/README.md`, `CLAUDE.md`, `.github/workflows/test.yml`, `docs/superpowers/plans/2026-07-02-issue-469-sim2real-build.md`). Parent shows only pre-existing untracked state.

- [ ] **Step 5: If sweep found real staleness, commit fixes**

If step 1 surfaced stale references worth fixing, apply and commit:

```bash
git add <files>
git commit -m "docs: sweep for stale references after sim2real build (#469)"
```

Otherwise, skip.

---

## Self-Review

1. **Spec coverage.** Every acceptance-criterion checkbox in issue #469:
   - `pipeline/lib/build.py` extracts shared primitives — Task 1 (primitives), Task 2 (dispatch), Task 3 (deploy.py routes through). ✓
   - `sim2real build --translation NAME|PREFIX|HASH [--force-rebuild] [--skip-build]` — Task 4 argparse, Task 5 loop. ✓
   - Prerequisites checked at command entry (skopeo, completeness, `epp_image.build.{hub,name}` mapped to `setup_config.json`) — Task 4 step 5. ✓
   - Image-ref construction — Task 5. ✓
   - Registry probe via skopeo, success + no `--force-rebuild` → skip, any failure → treat-as-miss → build — Task 5. ✓
   - Post-build second `skopeo inspect` → digest; failure → `image_ref` + `image_digest: null` + warning — Task 5. ✓
   - `algorithms[i].image_ref` and `image_digest` via atomic tempfile+rename — Task 1 (primitive), Task 5 (usage). ✓
   - `sim2real assemble` incomplete-translation prereq — Task 6. ✓
   - `pipeline/README.md` updates (translate flow, build behavior, alias resolution UX, failure modes, e2e example) — Task 7. ✓
   - `CLAUDE.md` updates (alias UX + schema fields) — Task 7. ✓
   - Real-cluster demo gate — manual, explicitly noted in the issue as a non-CI item. Skip in this plan.
   - Tests: probe-hit skip, probe-miss triggers build, `--force-rebuild` overrides, `--skip-build` bypasses, probe-auth-failure treated as miss, missing-skopeo fails cleanly, post-build inspect failure records null digest + warning, per-algo image_ref/image_digest written back correctly, incomplete-translation exits with actionable error, assemble fails early on null image_ref — Tasks 1, 4, 5, 6. ✓

2. **Placeholder scan.** No "TODO", "TBD", "handle edge cases" left; every code step shows the code; every test step shows the test.

3. **Type consistency.**
   - `dispatch_buildkit_build(image_ref=str, build_id=str, namespace=str, source_dir=Path, run_dir=Path, repo_root=Path) -> int` — consistent between Task 2 interface, Task 3 caller in deploy.py, and Task 5 caller in sim2real.py.
   - `probe_image_digest(image_ref) -> str | None` — consistent between Task 1 and Task 5's callers.
   - `atomic_write_json(path, data)` — consistent between Task 1, Task 3 (via `_write_build_metadata`), and Task 5.
   - `translation_output.json:algorithms[i]` schema matches what `read_translation_output` produces (fields: name, source_path, source_sha256, config_path, image_ref, image_digest).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-02-issue-469-sim2real-build.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session, batched with checkpoints.

Which approach?
