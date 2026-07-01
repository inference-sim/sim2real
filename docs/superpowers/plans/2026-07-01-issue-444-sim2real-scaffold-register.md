# Issue #444 Implementation Plan — sim2real.py scaffold + `translation register`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first command of the new `pipeline/sim2real.py` CLI — `translation register` — which imports a pre-built EPP image and treatment overlay as a registered translation under `workspace/translations/<hash>/`.

**Architecture:** New top-level entry point `pipeline/sim2real.py` follows `pipeline/cluster.py`'s argparse-subcommand shape (two-level subcommands: `translation register`). Path helpers extend `pipeline/lib/layout.py`. All register logic is private to `sim2real.py` (small first PR, no lib module needed yet). Hash uses canonical JSON of `{image_digest_or_ref, config_sha256, algorithm_name}` for boundary-shift safety while staying deterministic. Digest is extracted from the image ref when it contains `@sha256:...`; otherwise `image_digest` is recorded as `null` (design's offline case).

**Tech Stack:** Python 3.10+, `argparse`, `hashlib.sha256`, `json` canonical-form, `pytest` (following `pipeline/tests/test_cluster_py.py` conventions).

## Global Constraints

- Base branch: `refactor/v2-step-1`. All PR forks from this branch after step-0 was merged in via the preflight merge commit.
- No changes to `prepare.py`, `deploy.py`, `run.py`, or legacy `assemble.py` in this PR — those are addressed by later step-1 children (#445–#449).
- Schemas carry `version: 1` per design.
- Algorithm name regex: `[a-z0-9-]+`. Empty or non-matching → error.
- Idempotent register: same three inputs → same hash → warn "already registered, no-op", exit 0.
- Hash-collision case (existing translation dir with same hash but different algorithm name recorded) → error.
- Offline registry (no `@sha256:` in image ref) → warn, `image_digest: null`, hash still computed over `image_ref`.
- `--registered-hash` (if given) → assert equal to computed hash or error before any writes.
- CI on `refactor/v2-*` branches is not wired up; add the new test file to the workflow anyway (for eventual main merge). Note in PR body.

---

## File Structure

Files created / modified in this PR:

- `pipeline/lib/layout.py` — **modify**. Add four path helpers.
- `pipeline/sim2real.py` — **create**. Argparse dispatch + `translation register` subcommand with private helpers.
- `pipeline/tests/test_layout.py` — **modify**. Add tests for the four new helpers.
- `pipeline/tests/test_sim2real.py` — **create**. Unit tests for helpers + argparse-driven integration tests.
- `.github/workflows/test.yml` — **modify**. Add `pipeline/tests/test_sim2real.py` to the explicit path list.
- `pipeline/README.md` — **modify**. Add "Register a translation" section.

---

## Task 1: Extend layout.py with translation path helpers

**Files:**
- Modify: `pipeline/lib/layout.py` (after existing `translations_dir()` on line ~91)
- Test: `pipeline/tests/test_layout.py`

**Interfaces:**
- Consumes: `translations_dir()` (already defined at layout.py:91)
- Produces:
  - `translation_dir(translation_hash: str) -> Path`
  - `translation_output_path(translation_hash: str) -> Path`
  - `registered_path(translation_hash: str) -> Path`
  - `generated_config_path(translation_hash: str, algorithm_name: str) -> Path`

- [ ] **Step 1: Write failing tests for the four helpers**

Append to `pipeline/tests/test_layout.py`:

```python
class TestTranslationPaths:
    def test_translation_dir(self, tmp_path, monkeypatch):
        layout._EXPERIMENT_ROOT = tmp_path
        assert layout.translation_dir("abc123") == tmp_path / "workspace" / "translations" / "abc123"

    def test_translation_output_path(self, tmp_path, monkeypatch):
        layout._EXPERIMENT_ROOT = tmp_path
        assert layout.translation_output_path("abc123") == \
            tmp_path / "workspace" / "translations" / "abc123" / "translation_output.json"

    def test_registered_path(self, tmp_path, monkeypatch):
        layout._EXPERIMENT_ROOT = tmp_path
        assert layout.registered_path("abc123") == \
            tmp_path / "workspace" / "translations" / "abc123" / "registered.json"

    def test_generated_config_path(self, tmp_path, monkeypatch):
        layout._EXPERIMENT_ROOT = tmp_path
        assert layout.generated_config_path("abc123", "softreflective") == \
            tmp_path / "workspace" / "translations" / "abc123" / "generated" / "softreflective" / "softreflective_config.yaml"
```

- [ ] **Step 2: Run to confirm failure**

```
python -m pytest pipeline/tests/test_layout.py::TestTranslationPaths -v
```

Expected: 4× FAIL with `AttributeError: module 'pipeline.lib.layout' has no attribute 'translation_dir'` (or similar).

- [ ] **Step 3: Implement the four helpers**

Append to `pipeline/lib/layout.py`:

```python
def translation_dir(translation_hash: str) -> Path:
    """``workspace/translations/<hash>/``"""
    return translations_dir() / translation_hash


def translation_output_path(translation_hash: str) -> Path:
    """``workspace/translations/<hash>/translation_output.json``"""
    return translation_dir(translation_hash) / "translation_output.json"


def registered_path(translation_hash: str) -> Path:
    """``workspace/translations/<hash>/registered.json`` (BYO-only)."""
    return translation_dir(translation_hash) / "registered.json"


def generated_config_path(translation_hash: str, algorithm_name: str) -> Path:
    """``workspace/translations/<hash>/generated/<algo>/<algo>_config.yaml``"""
    return translation_dir(translation_hash) / "generated" / algorithm_name / f"{algorithm_name}_config.yaml"
```

- [ ] **Step 4: Confirm tests pass**

```
python -m pytest pipeline/tests/test_layout.py::TestTranslationPaths -v
```

Expected: 4× PASS.

- [ ] **Step 5: Commit**

```
git add pipeline/lib/layout.py pipeline/tests/test_layout.py
git commit -m "feat(layout): add translation path helpers"
```

---

## Task 2: sim2real.py — algorithm-name validator (TDD)

**Files:**
- Create: `pipeline/sim2real.py` (initial skeleton + this helper)
- Create: `pipeline/tests/test_sim2real.py`

**Interfaces:**
- Produces: `_validate_algorithm_name(name: str) -> str` — returns the name on success, raises `argparse.ArgumentTypeError` otherwise (so argparse gives a clean error).

- [ ] **Step 1: Write failing tests**

Create `pipeline/tests/test_sim2real.py`:

```python
"""Tests for pipeline/sim2real.py — sim2real CLI top-level entry."""

from __future__ import annotations

import argparse

import pytest

from pipeline import sim2real
from pipeline.lib import layout


@pytest.fixture(autouse=True)
def _isolated_experiment_root(tmp_path, monkeypatch):
    layout._EXPERIMENT_ROOT = tmp_path
    yield
    layout._EXPERIMENT_ROOT = None


class TestValidateAlgorithmName:
    def test_accepts_lowercase_letters(self):
        assert sim2real._validate_algorithm_name("softreflective") == "softreflective"

    def test_accepts_hyphens_and_digits(self):
        assert sim2real._validate_algorithm_name("algo-v2-final") == "algo-v2-final"

    def test_rejects_uppercase(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("SoftReflective")

    def test_rejects_underscore(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("soft_reflective")

    def test_rejects_empty(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("")

    def test_rejects_whitespace(self):
        with pytest.raises(argparse.ArgumentTypeError):
            sim2real._validate_algorithm_name("soft reflective")
```

- [ ] **Step 2: Run to confirm failure**

```
python -m pytest pipeline/tests/test_sim2real.py::TestValidateAlgorithmName -v
```

Expected: 6× FAIL with `ModuleNotFoundError: No module named 'pipeline.sim2real'`.

- [ ] **Step 3: Create sim2real.py skeleton + validator**

Create `pipeline/sim2real.py`:

```python
#!/usr/bin/env python3
"""sim2real top-level CLI.

Subcommands land incrementally across the step-1 epic. This file is
created by PR 1 with only ``translation register``. Subsequent PRs add
``assemble``, ``use``, ``list runs``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Ensure repo root is on sys.path when run as a script (python pipeline/sim2real.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.lib import layout  # noqa: E402 — must follow sys.path guard


_ALGORITHM_NAME_RE = re.compile(r"^[a-z0-9-]+$")


def _validate_algorithm_name(name: str) -> str:
    """Return ``name`` if it matches ``[a-z0-9-]+``; raise otherwise.

    Used as an argparse ``type=`` so validation surfaces as a clean CLI
    error rather than a stacktrace deep in register logic.
    """
    if not name or not _ALGORITHM_NAME_RE.match(name):
        raise argparse.ArgumentTypeError(
            f"algorithm name must match [a-z0-9-]+ (got {name!r})"
        )
    return name
```

- [ ] **Step 4: Confirm tests pass**

```
python -m pytest pipeline/tests/test_sim2real.py::TestValidateAlgorithmName -v
```

Expected: 6× PASS.

- [ ] **Step 5: Commit**

```
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(sim2real): skeleton + algorithm-name validator"
```

---

## Task 3: Digest extraction + translation hash (TDD)

**Files:**
- Modify: `pipeline/sim2real.py`
- Modify: `pipeline/tests/test_sim2real.py`

**Interfaces:**
- Produces:
  - `_extract_digest_from_ref(image_ref: str) -> str | None` — returns `"sha256:..."` if ref contains `@sha256:`, else `None`.
  - `_compute_translation_hash(image_digest_or_ref: str, config_bytes: bytes, algorithm_name: str) -> str` — canonical-JSON SHA-256 hex.

**Hash formula (from design):**
```
translation_hash = sha256(canonical_json({
    algorithm_name,
    config_sha256: sha256(config_bytes).hex(),
    image_digest_or_ref,
}))
```
Canonical JSON = sorted keys, no whitespace, UTF-8. `config_sha256` (not raw bytes) is embedded so the outer JSON is well-formed and length-bounded. This preserves the design's "hash over these three inputs" intent while eliminating boundary-shift collision risk from raw concatenation.

- [ ] **Step 1: Write failing tests for digest extraction**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestExtractDigest:
    def test_extracts_digest_when_present(self):
        ref = "ghcr.io/foo/bar@sha256:aabbccdd" + "0" * 56
        assert sim2real._extract_digest_from_ref(ref) == "sha256:aabbccdd" + "0" * 56

    def test_returns_none_when_tag_only(self):
        assert sim2real._extract_digest_from_ref("ghcr.io/foo/bar:v1.0") is None

    def test_returns_none_when_no_tag_no_digest(self):
        assert sim2real._extract_digest_from_ref("ghcr.io/foo/bar") is None
```

- [ ] **Step 2: Run — expect FAIL**

```
python -m pytest pipeline/tests/test_sim2real.py::TestExtractDigest -v
```

Expected: 3× FAIL with `AttributeError`.

- [ ] **Step 3: Implement `_extract_digest_from_ref`**

Add to `pipeline/sim2real.py`:

```python
def _extract_digest_from_ref(image_ref: str) -> str | None:
    """Return the ``sha256:...`` fragment from an image ref, or None.

    Recognizes only the pinned form ``registry/repo@sha256:HEX`` (64 hex chars).
    Tag-only refs (``registry/repo:v1``) return None; the caller records
    ``image_digest: null`` per design's offline case.
    """
    idx = image_ref.rfind("@sha256:")
    if idx < 0:
        return None
    digest = image_ref[idx + 1:]
    if not digest.startswith("sha256:"):
        return None
    hex_part = digest[len("sha256:"):]
    if len(hex_part) != 64 or not all(c in "0123456789abcdef" for c in hex_part):
        return None
    return digest
```

- [ ] **Step 4: Confirm tests pass**

```
python -m pytest pipeline/tests/test_sim2real.py::TestExtractDigest -v
```

Expected: 3× PASS.

- [ ] **Step 5: Write failing tests for hash computation**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestComputeTranslationHash:
    def test_is_deterministic(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"config: 1\n", "algo")
        h2 = sim2real._compute_translation_hash("sha256:aa", b"config: 1\n", "algo")
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex length

    def test_changes_with_algorithm_name(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"c", "a")
        h2 = sim2real._compute_translation_hash("sha256:aa", b"c", "b")
        assert h1 != h2

    def test_changes_with_config_content(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"a", "algo")
        h2 = sim2real._compute_translation_hash("sha256:aa", b"b", "algo")
        assert h1 != h2

    def test_changes_with_image_ref(self):
        h1 = sim2real._compute_translation_hash("sha256:aa", b"c", "algo")
        h2 = sim2real._compute_translation_hash("sha256:bb", b"c", "algo")
        assert h1 != h2

    def test_offline_ref_produces_stable_hash(self):
        # Design: offline case substitutes image_ref for image_digest.
        # Same ref string → same hash across "offline sessions".
        h1 = sim2real._compute_translation_hash("ghcr.io/foo:v1", b"c", "algo")
        h2 = sim2real._compute_translation_hash("ghcr.io/foo:v1", b"c", "algo")
        assert h1 == h2
```

- [ ] **Step 6: Run — expect FAIL**

```
python -m pytest pipeline/tests/test_sim2real.py::TestComputeTranslationHash -v
```

Expected: 5× FAIL with `AttributeError`.

- [ ] **Step 7: Implement `_compute_translation_hash`**

Add to `pipeline/sim2real.py`:

```python
import hashlib
import json


def _compute_translation_hash(
    image_digest_or_ref: str,
    config_bytes: bytes,
    algorithm_name: str,
) -> str:
    """SHA-256 hex over canonical JSON of the three BYO inputs.

    Design: ``translation_hash = sha256(image_digest_or_ref || config || name)``.
    Implementation embeds ``sha256(config)`` in a canonical JSON envelope
    (sorted keys, no whitespace) to prevent boundary-shift collisions
    from raw concatenation while preserving determinism. Mirrors
    ``pipeline/lib/slicer.translation_hash``'s canonical-JSON approach.
    """
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    canonical = json.dumps(
        {
            "algorithm_name": algorithm_name,
            "config_sha256": config_sha,
            "image_digest_or_ref": image_digest_or_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Move the `import hashlib` and `import json` up to the top-of-file import block.

- [ ] **Step 8: Confirm tests pass**

```
python -m pytest pipeline/tests/test_sim2real.py -v
```

Expected: all PASS (14 tests so far: 6 validator + 3 digest + 5 hash).

- [ ] **Step 9: Commit**

```
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(sim2real): digest extraction + translation-hash computation"
```

---

## Task 4: `register` action + filesystem writes (TDD)

**Files:**
- Modify: `pipeline/sim2real.py`
- Modify: `pipeline/tests/test_sim2real.py`

**Interfaces:**
- Produces:
  - `_build_translation_output(algorithm_name, image_ref, translation_hash, created_at) -> dict` — the JSON structure written to `translation_output.json`.
  - `_build_registered(image_ref, image_digest, registered_at) -> dict` — the JSON structure written to `registered.json`.
  - `_register_translation(algorithm_name, image_ref, config_path, baseline_config_path, registered_hash, now_iso) -> tuple[str, str]` — returns `(translation_hash, status)` where status is `"created" | "idempotent"`; raises `ValueError` on collision, `RuntimeError` on `--registered-hash` mismatch.

- [ ] **Step 1: Write failing tests for the schema builders**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestBuildSchemas:
    def test_translation_output_schema(self):
        out = sim2real._build_translation_output(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            translation_hash="a" * 64,
            created_at="2026-07-01T14:00:00Z",
        )
        assert out == {
            "version": 1,
            "translation_hash": "a" * 64,
            "source": "byo",
            "algorithms": [{"name": "softreflective"}],
            "image_ref": "ghcr.io/foo:v1",
            "created_at": "2026-07-01T14:00:00Z",
        }

    def test_registered_schema_with_digest(self):
        reg = sim2real._build_registered(
            image_ref="ghcr.io/foo@sha256:" + "b" * 64,
            image_digest="sha256:" + "b" * 64,
            registered_at="2026-07-01T14:00:00Z",
        )
        assert reg == {
            "version": 1,
            "image_ref": "ghcr.io/foo@sha256:" + "b" * 64,
            "image_digest": "sha256:" + "b" * 64,
            "source": "byo",
            "registered_at": "2026-07-01T14:00:00Z",
        }

    def test_registered_schema_offline(self):
        reg = sim2real._build_registered(
            image_ref="ghcr.io/foo:v1",
            image_digest=None,
            registered_at="2026-07-01T14:00:00Z",
        )
        assert reg["image_digest"] is None
        assert reg["image_ref"] == "ghcr.io/foo:v1"
```

- [ ] **Step 2: Implement schema builders and confirm PASS**

Add to `pipeline/sim2real.py`:

```python
def _build_translation_output(
    algorithm_name: str,
    image_ref: str,
    translation_hash: str,
    created_at: str,
) -> dict:
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": "byo",
        "algorithms": [{"name": algorithm_name}],
        "image_ref": image_ref,
        "created_at": created_at,
    }


def _build_registered(
    image_ref: str,
    image_digest: str | None,
    registered_at: str,
) -> dict:
    return {
        "version": 1,
        "image_ref": image_ref,
        "image_digest": image_digest,
        "source": "byo",
        "registered_at": registered_at,
    }
```

Run:
```
python -m pytest pipeline/tests/test_sim2real.py::TestBuildSchemas -v
```

Expected: 3× PASS.

- [ ] **Step 3: Write failing tests for `_register_translation`**

Append to `pipeline/tests/test_sim2real.py`:

```python
import json


class TestRegisterTranslation:
    def _write_overlay(self, tmp_path, content=b"scorer: mine\n"):
        p = tmp_path / "treatment.yaml"
        p.write_bytes(content)
        return p

    def test_creates_translation_dir_and_files(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, status = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        assert status == "created"
        assert len(thash) == 64
        assert layout.translation_output_path(thash).exists()
        assert layout.registered_path(thash).exists()
        assert layout.generated_config_path(thash, "softreflective").exists()

    def test_translation_output_contents(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        out = json.loads(layout.translation_output_path(thash).read_text())
        assert out["source"] == "byo"
        assert out["algorithms"] == [{"name": "softreflective"}]
        assert out["translation_hash"] == thash
        assert out["image_ref"] == "ghcr.io/foo:v1"
        assert out["version"] == 1

    def test_registered_records_digest_when_present(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        ref = "ghcr.io/foo@sha256:" + "a" * 64
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref=ref,
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        reg = json.loads(layout.registered_path(thash).read_text())
        assert reg["image_digest"] == "sha256:" + "a" * 64

    def test_registered_null_digest_when_tag_only(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        reg = json.loads(layout.registered_path(thash).read_text())
        assert reg["image_digest"] is None

    def test_writes_treatment_overlay(self, tmp_path):
        cfg = self._write_overlay(tmp_path, content=b"scorer: custom\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        assert layout.generated_config_path(thash, "softreflective").read_bytes() == b"scorer: custom\n"

    def test_writes_baseline_config_when_provided(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        baseline = tmp_path / "baseline.yaml"
        baseline.write_bytes(b"baseline: config\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=baseline,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        gen_baseline = layout.translation_dir(thash) / "generated" / "baseline_config.yaml"
        assert gen_baseline.read_bytes() == b"baseline: config\n"

    def test_idempotent_second_call_same_inputs(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        args = dict(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        h1, s1 = sim2real._register_translation(**args)
        h2, s2 = sim2real._register_translation(**args)
        assert h1 == h2
        assert s1 == "created"
        assert s2 == "idempotent"

    def test_hash_collision_different_algorithm_errors(self, tmp_path, monkeypatch):
        # Simulate: existing translation dir with same hash but different algo name.
        cfg = self._write_overlay(tmp_path)
        thash, _ = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-01T14:00:00Z",
        )
        # Corrupt the existing translation_output.json to name a different algo.
        out_path = layout.translation_output_path(thash)
        out = json.loads(out_path.read_text())
        out["algorithms"] = [{"name": "otheralgo"}]
        out_path.write_text(json.dumps(out))
        with pytest.raises(ValueError, match="algorithm name mismatch"):
            sim2real._register_translation(
                algorithm_name="softreflective",
                image_ref="ghcr.io/foo:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-01T14:00:00Z",
            )

    def test_registered_hash_mismatch_errors(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        with pytest.raises(RuntimeError, match="registered-hash mismatch"):
            sim2real._register_translation(
                algorithm_name="softreflective",
                image_ref="ghcr.io/foo:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash="deadbeef" * 8,
                now_iso="2026-07-01T14:00:00Z",
            )

    def test_registered_hash_match_succeeds(self, tmp_path):
        cfg = self._write_overlay(tmp_path)
        expected = sim2real._compute_translation_hash(
            "ghcr.io/foo:v1", cfg.read_bytes(), "softreflective"
        )
        thash, status = sim2real._register_translation(
            algorithm_name="softreflective",
            image_ref="ghcr.io/foo:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=expected,
            now_iso="2026-07-01T14:00:00Z",
        )
        assert thash == expected
        assert status == "created"
```

- [ ] **Step 4: Run — expect FAIL**

```
python -m pytest pipeline/tests/test_sim2real.py::TestRegisterTranslation -v
```

Expected: 10× FAIL with `AttributeError: module 'pipeline.sim2real' has no attribute '_register_translation'`.

- [ ] **Step 5: Implement `_register_translation`**

Add to `pipeline/sim2real.py`:

```python
def _register_translation(
    *,
    algorithm_name: str,
    image_ref: str,
    config_path: Path,
    baseline_config_path: Path | None,
    registered_hash: str | None,
    now_iso: str,
) -> tuple[str, str]:
    """Register a BYO translation on disk.

    Returns ``(translation_hash, status)`` where status is either
    ``"created"`` (fresh registration) or ``"idempotent"`` (matching
    translation already existed; no writes performed).

    Raises:
        RuntimeError: ``--registered-hash`` given and does not match computed.
        ValueError: existing translation dir has the same hash but records
            a different algorithm name (corrupted state or collision).
    """
    config_bytes = config_path.read_bytes()
    image_digest = _extract_digest_from_ref(image_ref)
    digest_or_ref = image_digest if image_digest is not None else image_ref
    thash = _compute_translation_hash(digest_or_ref, config_bytes, algorithm_name)

    if registered_hash is not None and registered_hash != thash:
        raise RuntimeError(
            f"--registered-hash mismatch: expected {registered_hash}, got {thash}"
        )

    out_path = layout.translation_output_path(thash)
    if out_path.exists():
        existing = json.loads(out_path.read_text())
        existing_algos = [a.get("name") for a in existing.get("algorithms", [])]
        if algorithm_name not in existing_algos:
            raise ValueError(
                f"algorithm name mismatch: translation {thash} records "
                f"{existing_algos}, refusing to register {algorithm_name!r}"
            )
        return thash, "idempotent"

    tdir = layout.translation_dir(thash)
    (tdir / "generated" / algorithm_name).mkdir(parents=True, exist_ok=True)

    out = _build_translation_output(algorithm_name, image_ref, thash, now_iso)
    out_path.write_text(json.dumps(out, indent=2) + "\n")

    reg = _build_registered(image_ref, image_digest, now_iso)
    layout.registered_path(thash).write_text(json.dumps(reg, indent=2) + "\n")

    layout.generated_config_path(thash, algorithm_name).write_bytes(config_bytes)

    if baseline_config_path is not None:
        (tdir / "generated" / "baseline_config.yaml").write_bytes(
            baseline_config_path.read_bytes()
        )

    return thash, "created"
```

- [ ] **Step 6: Confirm tests pass**

```
python -m pytest pipeline/tests/test_sim2real.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(sim2real): _register_translation + schema builders"
```

---

## Task 5: Argparse dispatch + main() (TDD via CLI)

**Files:**
- Modify: `pipeline/sim2real.py`
- Modify: `pipeline/tests/test_sim2real.py`

**Interfaces:**
- Produces: `build_parser() -> argparse.ArgumentParser`, `main() -> int`.
- The CLI shape: `pipeline/sim2real.py [--experiment-root PATH] translation register --algorithm N --image REF --config PATH [--baseline-config PATH] [--registered-hash HASH]`.

- [ ] **Step 1: Write failing test for parser structure**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestBuildParser:
    def test_parses_translation_register(self):
        parser = sim2real.build_parser()
        args = parser.parse_args([
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", "/tmp/treatment.yaml",
        ])
        assert args.command == "translation"
        assert args.subcommand == "register"
        assert args.algorithm == "softreflective"
        assert args.image == "ghcr.io/foo:v1"
        assert args.config == "/tmp/treatment.yaml"
        assert args.baseline_config is None
        assert args.registered_hash is None

    def test_rejects_bad_algorithm_name(self):
        parser = sim2real.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "translation", "register",
                "--algorithm", "Bad_Name",
                "--image", "ghcr.io/foo:v1",
                "--config", "/tmp/treatment.yaml",
            ])
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `build_parser()` and `main()`**

Append to `pipeline/sim2real.py`:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/sim2real.py",
        description="sim2real top-level CLI.",
    )
    parser.add_argument(
        "--experiment-root",
        metavar="PATH",
        default=None,
        help="Experiment root (default: current working directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    translation = sub.add_parser("translation", help="Manage translations")
    tsub = translation.add_subparsers(dest="subcommand", required=True)

    reg = tsub.add_parser("register", help="Register a BYO (pre-built) translation")
    reg.add_argument("--algorithm", required=True, type=_validate_algorithm_name,
                     help="Algorithm name (a-z, 0-9, hyphens)")
    reg.add_argument("--image", required=True, metavar="REF",
                     help="EPP image reference (e.g. ghcr.io/foo/bar:v1 or ...@sha256:...)")
    reg.add_argument("--config", required=True, metavar="PATH",
                     help="Path to the treatment overlay YAML")
    reg.add_argument("--baseline-config", metavar="PATH", default=None,
                     help="Optional path to a baseline overlay YAML")
    reg.add_argument("--registered-hash", metavar="HASH", default=None,
                     help="Assert the computed translation hash equals this value")

    return parser


def _cmd_translation_register(args) -> int:
    from datetime import datetime, timezone

    config_path = Path(args.config)
    baseline_config_path = Path(args.baseline_config) if args.baseline_config else None

    if not config_path.exists():
        print(f"error: --config file not found: {config_path}", file=sys.stderr)
        return 2

    # Fail-fast YAML validation. Malformed overlay → error before any writes.
    try:
        import yaml
        yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as e:
        print(f"error: --config is not valid YAML: {e}", file=sys.stderr)
        return 2

    if baseline_config_path is not None:
        if not baseline_config_path.exists():
            print(f"error: --baseline-config file not found: {baseline_config_path}", file=sys.stderr)
            return 2
        try:
            yaml.safe_load(baseline_config_path.read_text())
        except yaml.YAMLError as e:
            print(f"error: --baseline-config is not valid YAML: {e}", file=sys.stderr)
            return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        thash, status = _register_translation(
            algorithm_name=args.algorithm,
            image_ref=args.image,
            config_path=config_path,
            baseline_config_path=baseline_config_path,
            registered_hash=args.registered_hash,
            now_iso=now_iso,
        )
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if status == "idempotent":
        print(f"warning: translation {thash} already registered, no-op", file=sys.stderr)
    else:
        # If digest was not extractable, we recorded null — call it out.
        digest = _extract_digest_from_ref(args.image)
        if digest is None:
            print(
                f"warning: image_digest recorded as null "
                f"(no @sha256: in --image); hash falls back to using image_ref",
                file=sys.stderr,
            )
        print(f"registered translation {thash}")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    layout.set_experiment_root(args.experiment_root)
    if args.command == "translation" and args.subcommand == "register":
        return _cmd_translation_register(args)
    # argparse's required=True on subparsers means this line is unreachable
    # in practice; kept for defensive parity with cluster.py.
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

Also add `import yaml` to the top of the file (or leave the local `import yaml` inside `_cmd_translation_register` — it's the same as several existing modules that do local imports for optional deps).

- [ ] **Step 4: Confirm parser tests pass**

```
python -m pytest pipeline/tests/test_sim2real.py::TestBuildParser -v
```

Expected: 2× PASS.

- [ ] **Step 5: Write end-to-end main() test — happy path**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestMainEndToEnd:
    def test_happy_path(self, tmp_path, capsys, monkeypatch):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "registered translation" in captured.out
        # Warn about null digest since image ref is tag-only.
        assert "image_digest recorded as null" in captured.err

    def test_idempotent_second_run(self, tmp_path):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        argv = [
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ]
        assert sim2real.main(argv) == 0
        assert sim2real.main(argv) == 0  # idempotent, still exit 0

    def test_malformed_config_errors_no_writes(self, tmp_path, capsys):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("scorer: [unclosed\n")  # invalid YAML
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
        ])
        assert rc == 2
        # No translation dir should have been created.
        assert not layout.translations_dir().exists() or \
               not any(layout.translations_dir().iterdir())

    def test_registered_hash_mismatch_exits_2(self, tmp_path):
        cfg = tmp_path / "treatment.yaml"
        cfg.write_text("scorer: mine\n")
        rc = sim2real.main([
            "--experiment-root", str(tmp_path),
            "translation", "register",
            "--algorithm", "softreflective",
            "--image", "ghcr.io/foo:v1",
            "--config", str(cfg),
            "--registered-hash", "deadbeef" * 8,
        ])
        assert rc == 2
```

- [ ] **Step 6: Run and confirm all tests pass**

```
python -m pytest pipeline/tests/test_sim2real.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "feat(sim2real): translation register CLI dispatch + main()"
```

---

## Task 6: CI + README updates

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `pipeline/README.md`

- [ ] **Step 1: Add `pipeline/tests/test_sim2real.py` to `.github/workflows/test.yml`**

Insert into the pytest command list (after `test_slicer.py`):

```yaml
      - name: Run tests
        run: |
          python -m pytest pipeline/ \
            pipeline/tests/test_layout.py \
            pipeline/tests/test_cluster_ops.py \
            pipeline/tests/test_cluster_py.py \
            pipeline/tests/test_slicer.py \
            pipeline/tests/test_sim2real.py \
            .claude/skills/sim2real-analyze/tests/ \
            .claude/skills/sim2real-bootstrap/tests/ \
            .claude/skills/sim2real-translate/tests/ \
            -v
```

- [ ] **Step 2: Add "Register a translation" section to `pipeline/README.md`**

Find an existing section header (e.g. `## Assembling`) and insert before it (or near where translation-related material sits). Content:

````markdown
### Register a translation (BYO)

`sim2real.py translation register` imports a pre-built EPP image and its
treatment overlay YAML as a registered translation. Downstream commands
(`assemble`, `deploy.py run`) then treat it identically to a
skill-produced translation.

```bash
python pipeline/sim2real.py translation register \
    --algorithm softreflective \
    --image ghcr.io/kalantar-msb/sr-router:some-tag \
    --config path/to/treatment-overlay.yaml \
    [--baseline-config path/to/baseline-overlay.yaml] \
    [--registered-hash <expected-sha256-hex>]
```

Writes to `workspace/translations/<hash>/`:

- `translation_output.json` — algorithm index + provenance.
- `registered.json` — image ref + digest (BYO-only audit trail).
- `generated/<algorithm>/<algorithm>_config.yaml` — the treatment overlay.
- `generated/baseline_config.yaml` — if `--baseline-config` is given.

`translation_hash` is deterministic: same inputs produce the same hash,
so re-registering the same triple is idempotent (warn, exit 0).

If the `--image` reference contains a digest (`registry/repo@sha256:...`),
that digest is recorded in `registered.json`. Tag-only refs record
`image_digest: null` with a warning.
````

- [ ] **Step 3: Commit**

```
git add .github/workflows/test.yml pipeline/README.md
git commit -m "docs(sim2real): register-translation section + CI wiring"
```

---

## Task 7: Verify + stale-reference sweep + push + PR

- [ ] **Step 1: Full test run**

```
python -m pytest pipeline/ -v
```

Expected: all PASS (existing tests untouched; new sim2real tests all pass).

- [ ] **Step 2: Lint**

```
ruff check pipeline/ --select F
```

Expected: no errors.

- [ ] **Step 3: Sweep for stale references**

Grep across `**/*.md`, `docs/`, `.claude/skills/`, `README*` for symbols this PR introduces or changes. New symbols: `sim2real.py`, `translation register`, `translation_output.json` (already referenced by legacy prepare.py — those references stay accurate for now; sim2real.py is the new writer for BYO translations under `workspace/translations/`), `registered.json` (new; expect no prior references).

```bash
grep -rn "translation register\|registered.json\|translations/<hash>" \
    docs/ .claude/skills/ pipeline/README.md CLAUDE.md \
    | grep -v "docs/epics/step-1/design.md" | head
```

Log findings in the PR body.

- [ ] **Step 4: Confirm worktree-only changes; parent repo clean**

```bash
git status                                  # from worktree
git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
```

Second command should show unchanged parent-repo state (or, at most, the epic worktree's own diff — not files this PR should have touched).

- [ ] **Step 5: Push branch**

```bash
git push -u origin refactor/v2-step-1-issue-444-sim2real-scaffold-register
```

- [ ] **Step 6: Create PR against refactor/v2-step-1**

```bash
unset GITHUB_TOKEN GH_TOKEN 2>/dev/null; gh pr create \
    --base refactor/v2-step-1 \
    --title "step-1 PR 1: sim2real.py scaffold + translation register (#444)" \
    --body-file /tmp/pr-444-body.md
```

PR body should call out: (a) base branch is `refactor/v2-step-1` not `main`; (b) CI trigger on `refactor/v2-*` isn't wired — this PR only validates locally; (c) closes #444.

---

## Self-Review

**Spec coverage** — acceptance criteria from #444 mapped to task tests:

| Acceptance criterion | Where covered |
|---|---|
| Creates `workspace/translations/<hash>/` with the 3 files | Task 4 Step 3 tests (`test_creates_translation_dir_and_files`, `test_translation_output_contents`) |
| Hash is deterministic | Task 3 Step 5 tests (`test_is_deterministic`) |
| Idempotent same-inputs | Task 4 Step 3 (`test_idempotent_second_call_same_inputs`) + Task 5 Step 5 (`test_idempotent_second_run`) |
| Algorithm-name collision within translation errors | Task 4 Step 3 (`test_hash_collision_different_algorithm_errors`) |
| Malformed treatment overlay errors before writes | Task 5 Step 5 (`test_malformed_config_errors_no_writes`) |
| Offline / unreachable registry → warns, `image_digest: null` | Task 4 Step 3 (`test_registered_null_digest_when_tag_only`) + Task 5 Step 5 (`test_happy_path`'s stderr check) |
| Algorithm name validated against `[a-z0-9-]+` | Task 2 Step 1 (`TestValidateAlgorithmName`) |
| `--registered-hash` matches computed or errors | Task 4 Step 3 (`test_registered_hash_mismatch_errors`, `test_registered_hash_match_succeeds`) + Task 5 Step 5 (`test_registered_hash_mismatch_exits_2`) |
| Unit tests cover the listed items | Above table |
| `.github/workflows/test.yml` includes new tests | Task 6 Step 1 |
| `pipeline/README.md` "Register a translation" section | Task 6 Step 2 |

**Placeholder scan:** none found — every step has concrete code or exact commands.

**Type consistency:**
- `_register_translation` returns `tuple[str, str]` throughout.
- `image_digest` is `str | None`.
- `registered_hash` param is `str | None`.
- `now_iso` is a string.
- Path helpers all return `Path`.

Ready to execute.
