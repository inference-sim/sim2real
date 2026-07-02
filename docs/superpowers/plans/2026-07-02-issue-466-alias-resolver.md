# Alias Resolver + Schema Plumbing + `list translations` — Implementation Plan (issue #466)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship step-2 PR 2 — a shared alias/prefix/hash resolver, schema migration of `translation_output.json` to per-algorithm `image_ref`/`image_digest`, and the `sim2real list translations` command.

**Architecture:** New module `pipeline/lib/translation_ref.py` owns (a) the validation regex used by every producer of alias/algorithm names, (b) an on-read normalizer/shim that presents both the legacy step-1 shape and the new step-2 shape as the same in-memory dict, and (c) the resolver that turns name/prefix/hash refs into full hashes. `sim2real translation register` is extended in place to write `alias` + per-algo `image_ref`/`image_digest` (new schema) and to enforce alias uniqueness (with `--force` atomic reassignment). `sim2real assemble --translation` routes through the resolver and gains an "incomplete-translation" check. `sim2real list translations` reads via the shim and prints a five-column table.

**Tech Stack:** Python 3.11+, argparse, pathlib, pytest. No new third-party deps.

## Global Constraints

- Base branch: `refactor/v2-step-2`. All commits on `refactor/v2-step-2-issue-466-alias-resolver`.
- Worktree root: `.claude/worktrees/issue-466-alias-resolver/` — every path in this plan is relative to that root.
- Design contract: `docs/epics/step-2/design.md#pr-2--alias-resolver--schema-plumbing--list-translations` (see also §Alias resolver, §`sim2real translation register`, §Alias and algorithm-name validation rules, §`sim2real assemble`, §`sim2real list translations`).
- Validation regex (verbatim): `^[A-Za-z0-9][A-Za-z0-9._-]*$`, max 128 chars, reject `.` / `..` / empty. (Leading `-` is naturally rejected by the regex's first-char class.)
- On-read shim is location-fixed to `pipeline/lib/translation_ref.py` per design line 170.
- Register CLI stays single-algorithm in this PR (multi-algo BYO deferred per design §Out of scope).
- BYO register CLI already validates `--algorithm` via `[a-z0-9-]+`. New shared validator is a **superset** (adds uppercase, dot, underscore). Two existing tests that assert those reject need to flip to accept.
- File-op path discipline: every absolute path passed to Read/Edit/Write/Bash must contain `.claude/worktrees/issue-466-alias-resolver/`. `git status` in both worktree and parent repo after each edit batch.

---

## File Structure

**New file:**
- `pipeline/lib/translation_ref.py` — validation regex, on-read shim, iterator, `resolve_translation_ref`. Pure-logic module; imports only `layout`, `json`, `re`, `pathlib`, `logging`.

**Modified files:**
- `pipeline/sim2real.py` —
  - Replace `_validate_algorithm_name` internals to delegate to `translation_ref.validate_name`.
  - Expand `_build_translation_output` to new schema (adds `alias`, moves `image_ref`/`image_digest` into `algorithms[0]`, adds `source_path`/`source_sha256`/`config_path` per-algo).
  - Extend `_register_translation` for alias-collision refusal + `--force` atomic reassignment.
  - Add `--force` to the `register` argparse block.
  - Add `list translations` subparser under the existing `list` command.
  - Add `_cmd_list_translations` handler.
  - Update `_cmd_assemble` to route `--translation` through the resolver (accept ref, pass resolved hash to `assemble_run`). Update the `--translation` help text (HASH → REF).
- `pipeline/lib/assemble_run.py` — read `algorithms[i].image_ref` via the shim; per-algo image lookup; add "incomplete-translation" error.

**New tests:**
- `pipeline/tests/test_translation_ref.py` — validator, shim, iterator, resolver.

**Modified tests:**
- `pipeline/tests/test_sim2real.py` — update the two now-obsolete "rejects uppercase/underscore" tests; add register-alias-collision + `--force` + list-translations tests.
- `pipeline/tests/test_assemble_run.py` — update `translation_output.json` fixtures to new schema; add "legacy shape still resolvable via shim" and "incomplete-translation errors" cases.

---

## Task 1: Validation helper + on-read shim + iterator (foundational)

**Files:**
- Create: `pipeline/lib/translation_ref.py`
- Test: `pipeline/tests/test_translation_ref.py`

**Interfaces:**
- Consumes: `pipeline.lib.layout` (for `translations_dir()` default).
- Produces (used by tasks 2, 3, 4, 5, 6):
  - `NAME_MAX_LEN: int = 128`
  - `NAME_PATTERN: re.Pattern` — compiled `^[A-Za-z0-9][A-Za-z0-9._-]*$`
  - `class ValidationError(ValueError)` — raised by `validate_name` on rejection.
  - `def validate_name(name: str) -> str` — returns `name` on success; raises `ValidationError` otherwise.
  - `def is_full_hash(ref: str) -> bool` — True iff exactly 64 lowercase hex chars.
  - `def read_translation_output(path: Path) -> dict` — reads JSON, normalizes legacy top-level `image_ref`/`image_digest` down into every `algorithms[i]`, ensures `alias` key exists (value may be `None`). Does NOT rewrite the file on disk.
  - `def iter_translations(translations_dir: Path | None = None) -> Iterator[tuple[str, dict]]` — yields `(hash, normalized_output)` for every `<translations_dir>/<hash>/translation_output.json`. Malformed files are logged as warnings via the `logging` module and skipped (not raised).
  - `def find_by_alias(alias: str, translations_dir: Path | None = None) -> str | None` — returns the hash whose `alias` == `alias`, else `None`. Uses `iter_translations`.
  - `def resolve_translation_ref(ref: str, translations_dir: Path | None = None) -> str` — full resolver per design line 371.
  - `class ResolveError(ValueError)` — raised by `resolve_translation_ref` for all lookup failures (not found, ambiguous, malformed).

**Notes:**
- Malformed metadata during resolver scans MUST NOT raise from the resolver. Log at `WARNING` level via `logging.getLogger(__name__)` and skip that translation. Consumers that want stricter behavior can call `read_translation_output` directly.
- Resolver-order (per design):
  1. Validate `ref` shape via `validate_name`. On failure, wrap into `ResolveError` (message: `"invalid translation ref: <original error>"`).
  2. If `is_full_hash(ref)` and `<translations_dir>/<ref>/` exists → return `ref`.
  3. Else scan via `iter_translations`. If any alias equals `ref` exactly → return that hash.
  4. Else compute prefix matches: hashes that start with `ref` (min prefix 4 chars). Unique → return; ambiguous → `ResolveError` listing candidates; none → `ResolveError` "no such translation".
- The prefix minimum applies only after alias lookup fails — an operator with a short 3-char alias `foo` is a valid resolve; a 3-char prefix `abc` is not.
- `find_by_alias` returns the FIRST match; the invariant "only one translation carries any given alias" is enforced by register's `--force` atomic reassignment, not by the getter.

- [ ] **Step 1: Write the failing test — validate_name accepts basic shapes**

Add `pipeline/tests/test_translation_ref.py`:

```python
"""Tests for pipeline/lib/translation_ref.py — validation + shim + resolver."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pipeline.lib import translation_ref
from pipeline.lib.translation_ref import (
    ResolveError,
    ValidationError,
    find_by_alias,
    is_full_hash,
    iter_translations,
    read_translation_output,
    resolve_translation_ref,
    validate_name,
)


class TestValidateName:
    def test_accepts_simple_alphanumeric(self):
        assert validate_name("softreflective") == "softreflective"

    def test_accepts_uppercase(self):
        assert validate_name("SoftReflective") == "SoftReflective"

    def test_accepts_dot_underscore_hyphen(self):
        assert validate_name("algo_v2.final-1") == "algo_v2.final-1"

    def test_accepts_leading_digit(self):
        assert validate_name("1algo") == "1algo"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_name("")

    def test_rejects_leading_hyphen(self):
        with pytest.raises(ValidationError):
            validate_name("-algo")

    def test_rejects_leading_dot(self):
        with pytest.raises(ValidationError):
            validate_name(".algo")

    def test_rejects_single_dot(self):
        with pytest.raises(ValidationError):
            validate_name(".")

    def test_rejects_double_dot(self):
        with pytest.raises(ValidationError):
            validate_name("..")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_name("../foo")

    def test_rejects_slash(self):
        with pytest.raises(ValidationError):
            validate_name("foo/bar")

    def test_rejects_whitespace(self):
        with pytest.raises(ValidationError):
            validate_name("soft reflective")

    def test_rejects_oversized(self):
        with pytest.raises(ValidationError):
            validate_name("a" * 129)

    def test_accepts_max_length(self):
        assert validate_name("a" * 128) == "a" * 128
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestValidateName -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.lib.translation_ref'`

- [ ] **Step 3: Create `pipeline/lib/translation_ref.py` with validator only**

```python
"""Shared alias/hash resolver + validation + on-read shim for translations.

Consumers: `sim2real translation register` (validates --algorithm, writes
alias + normalized schema), `sim2real assemble --translation` (routes the
CLI ref through `resolve_translation_ref`), `sim2real list translations`
(iterates via `iter_translations` in newest-first order), and, later,
step-2 `sim2real translate` / `sim2real build`.

The on-read shim (`read_translation_output`) normalizes step-1 BYO
translations (top-level ``image_ref``/``image_digest`` at the object
root) into the step-2 per-algorithm shape (fields duplicated onto every
``algorithms[i]``). Legacy files are never rewritten on disk.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path

from pipeline.lib import layout


_log = logging.getLogger(__name__)


NAME_MAX_LEN = 128
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RESERVED_NAMES = frozenset({".", ".."})
_MIN_PREFIX_LEN = 4


class ValidationError(ValueError):
    """Raised when an alias or algorithm name fails validation."""


class ResolveError(ValueError):
    """Raised when `resolve_translation_ref` cannot resolve a ref."""


def validate_name(name: str) -> str:
    """Return ``name`` if it matches the shared validation rules.

    Rules (design §Alias and algorithm-name validation rules):
      - matches ``^[A-Za-z0-9][A-Za-z0-9._-]*$``
      - not empty
      - not ``.`` or ``..``
      - length ≤ 128
    """
    if not name:
        raise ValidationError("name must not be empty")
    if name in _RESERVED_NAMES:
        raise ValidationError(f"reserved name not allowed: {name!r}")
    if len(name) > NAME_MAX_LEN:
        raise ValidationError(
            f"name too long: {len(name)} > {NAME_MAX_LEN} chars"
        )
    if not NAME_PATTERN.match(name):
        raise ValidationError(
            f"name must match {NAME_PATTERN.pattern} (got {name!r})"
        )
    return name


def is_full_hash(ref: str) -> bool:
    """True iff ``ref`` is exactly 64 lowercase hex chars."""
    if len(ref) != 64:
        return False
    return all(c in "0123456789abcdef" for c in ref)
```

- [ ] **Step 4: Run test to verify TestValidateName passes**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestValidateName -v`
Expected: PASS on all 13 cases

- [ ] **Step 5: Write failing tests for `read_translation_output` (shim)**

Append to `pipeline/tests/test_translation_ref.py`:

```python
class TestReadTranslationOutput:
    def test_new_schema_pass_through(self, tmp_path):
        payload = {
            "version": 1,
            "translation_hash": "a" * 64,
            "source": "skill",
            "alias": "softreflective-v1",
            "algorithms": [
                {"name": "sr", "source_path": "algorithms/sr.py",
                 "source_sha256": "e3b0", "config_path": None,
                 "image_ref": "quay.io/x:tag", "image_digest": "sha256:aa"},
            ],
            "created_at": "2026-07-02T14:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = read_translation_output(p)
        assert data["alias"] == "softreflective-v1"
        assert data["algorithms"][0]["image_ref"] == "quay.io/x:tag"
        assert data["algorithms"][0]["image_digest"] == "sha256:aa"

    def test_legacy_top_level_image_ref_normalized(self, tmp_path):
        payload = {
            "version": 1,
            "translation_hash": "b" * 64,
            "source": "byo",
            "algorithms": [{"name": "legacy"}],
            "image_ref": "ghcr.io/legacy:v1",
            "image_digest": "sha256:bb",
            "created_at": "2026-06-01T10:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = read_translation_output(p)
        assert data["alias"] is None
        # Top-level image_ref/digest lifted into every algorithms[i].
        assert data["algorithms"][0]["image_ref"] == "ghcr.io/legacy:v1"
        assert data["algorithms"][0]["image_digest"] == "sha256:bb"

    def test_legacy_shape_does_not_overwrite_per_algo(self, tmp_path):
        # If a file had *both* (odd, but defensive), per-algo wins.
        payload = {
            "version": 1,
            "translation_hash": "c" * 64,
            "source": "byo",
            "alias": None,
            "algorithms": [{"name": "x", "image_ref": "specific:tag"}],
            "image_ref": "top:tag",
            "created_at": "2026-06-01T10:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = read_translation_output(p)
        assert data["algorithms"][0]["image_ref"] == "specific:tag"

    def test_missing_alias_defaults_to_none(self, tmp_path):
        payload = {
            "version": 1,
            "translation_hash": "d" * 64,
            "source": "skill",
            "algorithms": [{"name": "x"}],
            "created_at": "2026-07-02T14:00:00Z",
        }
        p = tmp_path / "translation_output.json"
        p.write_text(json.dumps(payload))
        data = read_translation_output(p)
        assert data["alias"] is None
```

- [ ] **Step 6: Run those tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestReadTranslationOutput -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'read_translation_output'`

- [ ] **Step 7: Implement `read_translation_output`**

Append to `pipeline/lib/translation_ref.py`:

```python
def read_translation_output(path: Path) -> dict:
    """Read a ``translation_output.json`` and normalize legacy top-level fields.

    Rules:
      - ``alias`` key is guaranteed to exist (may be ``None``).
      - For each ``algorithms[i]``, ``image_ref`` and ``image_digest`` are
        guaranteed to exist. If missing, the value from the object-root
        ``image_ref``/``image_digest`` (legacy step-1 shape) is copied in.
        Per-algo values, when already present, take precedence.

    Never rewrites the file. Callers that want to persist the new shape
    call ``sim2real translation register`` (which emits the new shape by
    construction).
    """
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(
            f"translation_output.json is not a JSON object: {path}"
        )
    data.setdefault("alias", None)
    top_ref = data.get("image_ref")
    top_digest = data.get("image_digest")
    for algo in data.get("algorithms", []) or []:
        if not isinstance(algo, dict):
            continue
        if "image_ref" not in algo:
            algo["image_ref"] = top_ref
        if "image_digest" not in algo:
            algo["image_digest"] = top_digest
    return data
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestReadTranslationOutput -v`
Expected: PASS on all 4 cases

- [ ] **Step 9: Write failing tests for `iter_translations` + `find_by_alias`**

Append to `pipeline/tests/test_translation_ref.py`:

```python
def _write_translation(base: Path, thash: str, payload: dict) -> None:
    tdir = base / thash
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "translation_output.json").write_text(json.dumps(payload))


class TestIterTranslations:
    def test_yields_all_translations(self, tmp_path):
        base = tmp_path / "translations"
        _write_translation(base, "a" * 64, {
            "version": 1, "translation_hash": "a" * 64, "source": "skill",
            "alias": "algo-a", "algorithms": [{"name": "a"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        _write_translation(base, "b" * 64, {
            "version": 1, "translation_hash": "b" * 64, "source": "byo",
            "alias": "algo-b", "algorithms": [{"name": "b"}],
            "created_at": "2026-07-02T00:00:00Z",
        })
        results = dict(iter_translations(base))
        assert set(results.keys()) == {"a" * 64, "b" * 64}
        assert results["a" * 64]["alias"] == "algo-a"

    def test_missing_directory_yields_empty(self, tmp_path):
        base = tmp_path / "nonexistent"
        assert list(iter_translations(base)) == []

    def test_malformed_json_logged_and_skipped(self, tmp_path, caplog):
        base = tmp_path / "translations"
        good = "a" * 64
        bad = "b" * 64
        _write_translation(base, good, {
            "version": 1, "translation_hash": good, "source": "byo",
            "alias": None, "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        (base / bad).mkdir(parents=True)
        (base / bad / "translation_output.json").write_text("{not json")
        caplog.set_level(logging.WARNING, logger=translation_ref.__name__)
        results = dict(iter_translations(base))
        assert good in results
        assert bad not in results
        assert any("malformed" in rec.message.lower() or bad in rec.message
                   for rec in caplog.records)

    def test_missing_translation_output_file_skipped(self, tmp_path):
        base = tmp_path / "translations"
        (base / ("c" * 64)).mkdir(parents=True)
        # No translation_output.json in that dir.
        assert list(iter_translations(base)) == []

    def test_dir_name_not_full_hash_skipped(self, tmp_path):
        # Prevents surprises from stray files/directories.
        base = tmp_path / "translations"
        _write_translation(base, "not-a-hash", {
            "version": 1, "translation_hash": "not-a-hash", "source": "byo",
            "alias": None, "algorithms": [], "created_at": "x",
        })
        assert list(iter_translations(base)) == []


class TestFindByAlias:
    def test_finds_matching_alias(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        _write_translation(base, h, {
            "version": 1, "translation_hash": h, "source": "skill",
            "alias": "my-alias", "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        assert find_by_alias("my-alias", base) == h

    def test_returns_none_when_no_match(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        _write_translation(base, h, {
            "version": 1, "translation_hash": h, "source": "skill",
            "alias": "other", "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        assert find_by_alias("my-alias", base) is None

    def test_skips_null_aliases(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        _write_translation(base, h, {
            "version": 1, "translation_hash": h, "source": "byo",
            "alias": None, "algorithms": [{"name": "x"}],
            "created_at": "2026-07-01T00:00:00Z",
        })
        assert find_by_alias("", base) is None
        # Also: an alias that is the string "None" doesn't match null.
        assert find_by_alias("None", base) is None
```

- [ ] **Step 10: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestIterTranslations pipeline/tests/test_translation_ref.py::TestFindByAlias -v`
Expected: FAIL — attributes not defined

- [ ] **Step 11: Implement `iter_translations` + `find_by_alias`**

Append to `pipeline/lib/translation_ref.py`:

```python
def iter_translations(
    translations_dir: Path | None = None,
) -> Iterator[tuple[str, dict]]:
    """Yield ``(hash, normalized_translation_output)`` for every translation.

    Directories whose name is not a 64-hex full hash are skipped
    (defensive: keeps operator-side stray files/dirs from breaking the
    resolver). Files that fail to parse are logged as warnings via the
    module logger and skipped — the resolver must never raise on
    unrelated malformed data.
    """
    base = translations_dir if translations_dir is not None else layout.translations_dir()
    if not base.is_dir():
        return
    for child in base.iterdir():
        if not child.is_dir():
            continue
        thash = child.name
        if not is_full_hash(thash):
            continue
        tout_path = child / "translation_output.json"
        if not tout_path.exists():
            continue
        try:
            yield thash, read_translation_output(tout_path)
        except (OSError, ValueError) as exc:
            _log.warning(
                "skipping malformed translation_output.json at %s: %s",
                tout_path, exc,
            )
            continue


def find_by_alias(
    alias: str, translations_dir: Path | None = None
) -> str | None:
    """Return the hash whose ``alias`` == ``alias``, else ``None``.

    Skips translations with ``alias == None``. Register enforces alias
    uniqueness across writes, so at most one match is expected.
    """
    if not alias:
        return None
    for thash, data in iter_translations(translations_dir):
        if data.get("alias") == alias:
            return thash
    return None
```

- [ ] **Step 12: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py -v`
Expected: PASS on all cases so far

- [ ] **Step 13: Write failing tests for `resolve_translation_ref`**

Append to `pipeline/tests/test_translation_ref.py`:

```python
class TestResolveTranslationRef:
    def _seed(self, base: Path, hashes_and_aliases: list[tuple[str, str | None]]):
        for thash, alias in hashes_and_aliases:
            _write_translation(base, thash, {
                "version": 1, "translation_hash": thash, "source": "skill",
                "alias": alias, "algorithms": [{"name": "algo"}],
                "created_at": "2026-07-01T00:00:00Z",
            })

    def test_resolves_alias_exact(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        self._seed(base, [(h, "my-alias")])
        assert resolve_translation_ref("my-alias", base) == h

    def test_resolves_full_hash(self, tmp_path):
        base = tmp_path / "translations"
        h = "a" * 64
        self._seed(base, [(h, "my-alias")])
        assert resolve_translation_ref(h, base) == h

    def test_resolves_unique_prefix(self, tmp_path):
        base = tmp_path / "translations"
        h = "abcdef" + "0" * 58
        self._seed(base, [(h, None), ("f" * 64, None)])
        assert resolve_translation_ref("abcd", base) == h

    def test_prefix_too_short(self, tmp_path):
        base = tmp_path / "translations"
        h = "abcdef" + "0" * 58
        self._seed(base, [(h, None)])
        with pytest.raises(ResolveError):
            resolve_translation_ref("abc", base)

    def test_prefix_ambiguous_lists_candidates(self, tmp_path):
        base = tmp_path / "translations"
        h1 = "abcd" + "1" * 60
        h2 = "abcd" + "2" * 60
        self._seed(base, [(h1, None), (h2, None)])
        with pytest.raises(ResolveError) as exc:
            resolve_translation_ref("abcd", base)
        assert h1 in str(exc.value)
        assert h2 in str(exc.value)

    def test_full_hash_not_present_errors(self, tmp_path):
        base = tmp_path / "translations"
        # Empty dir.
        base.mkdir()
        h = "a" * 64
        with pytest.raises(ResolveError):
            resolve_translation_ref(h, base)

    def test_no_match_error_mentions_list_command(self, tmp_path):
        base = tmp_path / "translations"
        self._seed(base, [("a" * 64, "other-alias")])
        with pytest.raises(ResolveError) as exc:
            resolve_translation_ref("nomatch", base)
        assert "list translations" in str(exc.value)

    def test_alias_wins_over_prefix(self, tmp_path):
        # An alias named "abcd" wins over a prefix match of "abcd*".
        base = tmp_path / "translations"
        h1 = "1" * 64
        h2 = "abcd" + "0" * 60
        self._seed(base, [(h1, "abcd"), (h2, None)])
        assert resolve_translation_ref("abcd", base) == h1

    def test_invalid_ref_rejected_before_scan(self, tmp_path):
        base = tmp_path / "translations"
        # Ref with slash — regex fails; scan never happens.
        with pytest.raises(ResolveError):
            resolve_translation_ref("foo/bar", base)

    def test_empty_translations_dir_error(self, tmp_path):
        base = tmp_path / "translations"
        # base does not exist at all.
        with pytest.raises(ResolveError):
            resolve_translation_ref("anything", base)
```

- [ ] **Step 14: Run those tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestResolveTranslationRef -v`
Expected: FAIL — resolver not implemented

- [ ] **Step 15: Implement `resolve_translation_ref`**

Append to `pipeline/lib/translation_ref.py`:

```python
def resolve_translation_ref(
    ref: str, translations_dir: Path | None = None
) -> str:
    """Resolve a name/prefix/hash ``ref`` to a full translation hash.

    Precedence:
      1. Reject refs that fail ``validate_name``.
      2. If ``ref`` is a full 64-char hex hash and the directory exists →
         return ``ref`` unchanged.
      3. Otherwise scan translations; if any ``alias`` equals ``ref``
         exactly → return that hash.
      4. Else compute hash-prefix matches (min prefix ``_MIN_PREFIX_LEN``).
         Unique → return; ambiguous → error with candidates; none →
         error mentioning ``sim2real list translations``.
    """
    try:
        validate_name(ref)
    except ValidationError as exc:
        raise ResolveError(f"invalid translation ref: {exc}") from exc

    base = translations_dir if translations_dir is not None else layout.translations_dir()

    # Step 2: full-hash short-circuit — but only if the directory is present.
    if is_full_hash(ref):
        if (base / ref).is_dir() and (base / ref / "translation_output.json").exists():
            return ref
        raise ResolveError(
            f"no such translation hash: {ref}; "
            "run 'sim2real list translations' to see available"
        )

    # Steps 3-4: scan.
    all_entries = list(iter_translations(base))
    if not all_entries:
        raise ResolveError(
            f"no translations in {base}; "
            "run 'sim2real translation register' or 'sim2real translate' first"
        )
    for thash, data in all_entries:
        if data.get("alias") == ref:
            return thash

    if len(ref) < _MIN_PREFIX_LEN:
        raise ResolveError(
            f"'{ref}' is too short for a prefix match "
            f"(min {_MIN_PREFIX_LEN} chars) and does not match any alias; "
            "run 'sim2real list translations' to see available"
        )
    prefix_hits = [thash for thash, _ in all_entries if thash.startswith(ref)]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    if len(prefix_hits) > 1:
        raise ResolveError(
            f"prefix '{ref}' matches {len(prefix_hits)} translations: "
            + ", ".join(sorted(prefix_hits))
        )
    raise ResolveError(
        f"no such translation '{ref}'; "
        "run 'sim2real list translations' to see available"
    )
```

- [ ] **Step 16: Run all translation_ref tests to verify green**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py -v`
Expected: PASS on all cases

- [ ] **Step 17: Commit**

```bash
cd .claude/worktrees/issue-466-alias-resolver
git add pipeline/lib/translation_ref.py pipeline/tests/test_translation_ref.py
git commit -m "translation_ref: validator + on-read shim + resolver

Adds pipeline/lib/translation_ref.py: shared validation regex
(^[A-Za-z0-9][A-Za-z0-9._-]*$, max 128, reserved/leading rules),
read_translation_output shim that normalizes step-1 legacy top-level
image_ref/image_digest into per-algo fields, iter_translations
(malformed files logged + skipped), find_by_alias, and
resolve_translation_ref (validate -> full-hash short-circuit -> alias
exact -> unique prefix). All errors surface as ResolveError, never
propagate JSON/OSError from a stray file.

Fresh module. Step-2 PR 2 groundwork; no existing consumers yet."
```

---

## Task 2: Extend `sim2real translation register` (new schema + alias collision)

**Files:**
- Modify: `pipeline/sim2real.py`
- Test: `pipeline/tests/test_sim2real.py`

**Interfaces:**
- Consumes (from task 1): `translation_ref.validate_name`, `translation_ref.find_by_alias`, `translation_ref.read_translation_output`.
- Produces (used by task 3, 4, 5, 6):
  - `_build_translation_output(...) -> dict` — new signature: `_build_translation_output(*, algorithm_name, image_ref, image_digest, config_path, translation_hash, source, alias, created_at)`. Returns the full step-2 shape with per-algo image_ref/image_digest inside `algorithms[0]` and no top-level `image_ref`.
  - `_register_translation(..., force: bool, ...) -> tuple[str, str]` — new `force` kwarg. On alias collision with `force=False`, raises `RuntimeError` with message starting `alias 'X' already assigned to translation <hash>; pass --force to reassign`. On collision with `force=True`, atomically clears the alias from the previous translation and writes the new one.
  - `register` argparse block gains `--force` (`action="store_true"`).

**Notes:**
- `_validate_algorithm_name` in sim2real.py now delegates to `translation_ref.validate_name`, catching `ValidationError` and re-raising as `argparse.ArgumentTypeError` for a clean CLI diagnostic.
- The step-1 config-schema was `{"version":1, translation_hash, source, algorithms:[{name}], image_ref, created_at}`. New shape (single-algo BYO): `{"version":1, translation_hash, source, alias, algorithms:[{name, source_path=None, source_sha256=None, config_path, image_ref, image_digest}], created_at}`.
- `_register_translation`'s partial-write check keeps working: we still write `registered.json` (BYO artifact from step-1, unchanged) and the config overlay under `generated/<algo>/`. The idempotent-return path (existing translation) still works — the file will already be in the new shape after this PR ships, and the shim handles legacy inputs at read time.
- Atomic reassignment: (1) read the previous translation's `translation_output.json`, (2) write a new version with `alias: null` via `os.replace` to the same path, (3) write the new translation's `translation_output.json` (also via `os.replace` — Python's `Path.write_text` does not do atomic-replace; use `tempfile` + `os.replace`). If step 2 succeeds and step 3 fails, the old alias is cleared but the new one is not set — operator can re-run the register command; no data lost. Both files are single-file atomic replaces on POSIX.

- [ ] **Step 1: Write failing test — new schema shape for `_build_translation_output`**

Add to `pipeline/tests/test_sim2real.py` (find a spot near the existing `TestBuildTranslationOutput` / register tests; if not present, add a new class):

```python
class TestBuildTranslationOutputV2:
    def test_new_schema_shape(self):
        out = sim2real._build_translation_output(
            algorithm_name="softreflective",
            image_ref="ghcr.io/x/sr:v1",
            image_digest="sha256:aa",
            config_path="generated/softreflective/softreflective_config.yaml",
            translation_hash="a" * 64,
            source="byo",
            alias="softreflective",
            created_at="2026-07-02T14:00:00Z",
        )
        # Top-level image_ref removed; now per-algo.
        assert "image_ref" not in out
        assert "image_digest" not in out
        assert out["alias"] == "softreflective"
        assert out["source"] == "byo"
        assert out["version"] == 1
        assert out["translation_hash"] == "a" * 64
        assert out["created_at"] == "2026-07-02T14:00:00Z"
        assert len(out["algorithms"]) == 1
        algo = out["algorithms"][0]
        assert algo["name"] == "softreflective"
        assert algo["image_ref"] == "ghcr.io/x/sr:v1"
        assert algo["image_digest"] == "sha256:aa"
        assert algo["config_path"] == \
            "generated/softreflective/softreflective_config.yaml"
        assert algo["source_path"] is None
        assert algo["source_sha256"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestBuildTranslationOutputV2 -v`
Expected: FAIL — TypeError (extra keyword args)

- [ ] **Step 3: Rewrite `_build_translation_output` to the new signature/shape**

Replace the current definition in `pipeline/sim2real.py` (lines 92-105):

```python
def _build_translation_output(
    *,
    algorithm_name: str,
    image_ref: str,
    image_digest: str | None,
    config_path: str,
    translation_hash: str,
    source: str,
    alias: str | None,
    created_at: str,
) -> dict:
    """Build the ``translation_output.json`` body for step-2 schema.

    Single-algorithm shape (BYO register in this PR). ``image_ref`` and
    ``image_digest`` live per-algo. ``source_path``/``source_sha256`` are
    ``None`` for BYO (populated by the skill-driven ``translate`` in PR 3).
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": source,
        "alias": alias,
        "algorithms": [
            {
                "name": algorithm_name,
                "source_path": None,
                "source_sha256": None,
                "config_path": config_path,
                "image_ref": image_ref,
                "image_digest": image_digest,
            }
        ],
        "created_at": created_at,
    }
```

- [ ] **Step 4: Run the test to verify the new schema builder passes**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestBuildTranslationOutputV2 -v`
Expected: PASS

- [ ] **Step 5: Update `_register_translation` to use the new builder + write alias**

Edit `_register_translation` in `pipeline/sim2real.py`. Changes:
- Add `force: bool = False` kwarg.
- After the partial-write / idempotency check, before the write, call `translation_ref.find_by_alias(algorithm_name, layout.translations_dir())`. If the returned hash exists and differs from `thash`:
  - If `not force`: raise `RuntimeError(f"alias {algorithm_name!r} already assigned to translation {other_hash}; pass --force to reassign")`
  - If `force`: atomically clear the alias on `other_hash` (see helper below).
- Call `_build_translation_output` with the new keyword signature, passing `source="byo"`, `alias=algorithm_name`, and `config_path=f"generated/{algorithm_name}/{algorithm_name}_config.yaml"`.
- Use `_atomic_write_json` (new helper) to write `translation_output.json`.

Add a helper near the top of `pipeline/sim2real.py` (after `_extract_digest_from_ref`):

```python
def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a tempfile + os.replace.

    POSIX-atomic on same-filesystem writes. Prevents readers from
    observing a half-written file during ``--force`` alias reassignment.
    """
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except Exception:
        # Cleanup on failure; ignore rmtree race with concurrent GC.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _clear_alias_on(other_hash: str) -> None:
    """Rewrite ``translations/<other_hash>/translation_output.json`` with ``alias=null``.

    Used by ``_register_translation`` under ``--force`` to keep aliases
    globally unique. If the target file cannot be read (missing, corrupt),
    raise — the collision-detection path already confirmed it exists via
    ``find_by_alias``; a read failure here indicates a race the operator
    should resolve manually.
    """
    from pipeline.lib import translation_ref
    other_path = layout.translation_output_path(other_hash)
    data = translation_ref.read_translation_output(other_path)
    data["alias"] = None
    _atomic_write_json(other_path, data)
```

Update `_register_translation` in place — replace lines 122-195 (function body) with the version below. Keep the same idempotency guarantees; new logic is only alias collision + build-call signature:

```python
def _register_translation(
    *,
    algorithm_name: str,
    image_ref: str,
    config_path: Path,
    baseline_config_path: Path | None,
    registered_hash: str | None,
    now_iso: str,
    force: bool = False,
) -> tuple[str, str]:
    """Register a BYO translation on disk.

    Returns ``(translation_hash, status)`` where status is either
    ``"created"`` (fresh registration) or ``"idempotent"`` (matching
    translation already existed; no writes performed).

    Raises:
        RuntimeError: ``--registered-hash`` given and does not match
            computed; OR alias collision on a different hash without
            ``force``.
        ValueError: existing translation dir has the same hash but records
            a different algorithm name (corrupted state or collision).
    """
    from pipeline.lib import translation_ref
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
        existing = translation_ref.read_translation_output(out_path)
        existing_algos = [a.get("name") for a in existing.get("algorithms", [])]
        if algorithm_name not in existing_algos:
            raise ValueError(
                f"algorithm name mismatch: translation {thash} records "
                f"{existing_algos}, refusing to register {algorithm_name!r}"
            )
        missing = []
        if not layout.registered_path(thash).exists():
            missing.append("registered.json")
        if not layout.generated_config_path(thash, algorithm_name).exists():
            missing.append(f"generated/{algorithm_name}/{algorithm_name}_config.yaml")
        if missing:
            raise RuntimeError(
                f"translation {thash} directory is incomplete (missing: "
                f"{', '.join(missing)}); remove {layout.translation_dir(thash)} "
                f"and re-run to recover"
            )
        return thash, "idempotent"

    # Alias collision — detect BEFORE creating any new files. Only a
    # different-hash collision matters; same-hash "collision" fell out
    # into the idempotent path above.
    other_hash = translation_ref.find_by_alias(
        algorithm_name, layout.translations_dir()
    )
    if other_hash is not None and other_hash != thash:
        if not force:
            raise RuntimeError(
                f"alias {algorithm_name!r} already assigned to translation "
                f"{other_hash}; pass --force to reassign"
            )
        _clear_alias_on(other_hash)

    tdir = layout.translation_dir(thash)
    (tdir / "generated" / algorithm_name).mkdir(parents=True, exist_ok=True)

    out = _build_translation_output(
        algorithm_name=algorithm_name,
        image_ref=image_ref,
        image_digest=image_digest,
        config_path=f"generated/{algorithm_name}/{algorithm_name}_config.yaml",
        translation_hash=thash,
        source="byo",
        alias=algorithm_name,
        created_at=now_iso,
    )
    _atomic_write_json(out_path, out)

    reg = _build_registered(image_ref, image_digest, now_iso)
    layout.registered_path(thash).write_text(json.dumps(reg, indent=2) + "\n")

    layout.generated_config_path(thash, algorithm_name).write_bytes(config_bytes)

    if baseline_config_path is not None:
        (tdir / "generated" / "baseline_config.yaml").write_bytes(
            baseline_config_path.read_bytes()
        )

    return thash, "created"
```

Also update `_validate_algorithm_name` (lines 30-43) to delegate to the shared helper:

```python
def _validate_algorithm_name(name: str) -> str:
    """Argparse ``type=`` wrapper around ``translation_ref.validate_name``.

    Widened in step-2 PR 2: accepts uppercase, dot, and underscore per
    the shared regex ``^[A-Za-z0-9][A-Za-z0-9._-]*$``. Kept as a thin
    wrapper so CLI errors surface as ``argparse.ArgumentTypeError``.
    """
    from pipeline.lib import translation_ref
    try:
        return translation_ref.validate_name(name)
    except translation_ref.ValidationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
```

Remove the now-unused `_ALGORITHM_NAME_RE = re.compile(r"^[a-z0-9-]+$")` module-level constant (line 30).

Also add `--force` to the register argparse block (`build_parser`, near the `--registered-hash` argument):

```python
    reg.add_argument(
        "--force",
        action="store_true",
        help="Reassign the alias (--algorithm) from a previous translation.",
    )
```

Wire `_cmd_translation_register` (lines 291-353) to pass `force=args.force` to `_register_translation`. Also, update the error message translation: the `RuntimeError` message "alias 'X' already assigned to translation Y" needs to flow through unchanged (already handled by the existing `except (RuntimeError, ValueError, OSError)` block that prints `error: {e}` and returns 2).

- [ ] **Step 6: Update existing sim2real tests that assumed the old schema/CLI**

In `pipeline/tests/test_sim2real.py`:

- `TestValidateAlgorithmName::test_rejects_uppercase` — rename to `test_accepts_uppercase` and change:
  ```python
  def test_accepts_uppercase(self):
      assert sim2real._validate_algorithm_name("SoftReflective") == "SoftReflective"
  ```
- `TestValidateAlgorithmName::test_rejects_underscore` — rename to `test_accepts_underscore` and change:
  ```python
  def test_accepts_underscore(self):
      assert sim2real._validate_algorithm_name("soft_reflective") == "soft_reflective"
  ```
- Add these new cases inside `TestValidateAlgorithmName`:
  ```python
  def test_rejects_leading_hyphen(self):
      with pytest.raises(argparse.ArgumentTypeError):
          sim2real._validate_algorithm_name("-algo")

  def test_rejects_dot(self):
      with pytest.raises(argparse.ArgumentTypeError):
          sim2real._validate_algorithm_name(".")

  def test_rejects_double_dot(self):
      with pytest.raises(argparse.ArgumentTypeError):
          sim2real._validate_algorithm_name("..")

  def test_rejects_oversized(self):
      with pytest.raises(argparse.ArgumentTypeError):
          sim2real._validate_algorithm_name("a" * 129)
  ```

Search for any test that asserts on the **old** `translation_output.json` shape (top-level `image_ref`) and update it to the new per-algo shape:

Run: `.venv/bin/python -c "import json; import pathlib; p = pathlib.Path('pipeline/tests/test_sim2real.py'); print(len([l for l in p.read_text().splitlines() if 'image_ref' in l]))"`

Look for these hits in `test_sim2real.py` and update:
- Any test that reads `translation_output.json` and expects `data["image_ref"]` at the top — change to `data["algorithms"][0]["image_ref"]`.
- Any test that constructs a synthetic `translation_output.json` with top-level `image_ref` should keep doing so ONLY for the legacy-shim regression tests. Otherwise, update to the new per-algo shape.

Concrete file-search command to execute during this step (inside the worktree):

```bash
.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v 2>&1 | grep -E "FAIL|ERROR" | head -40
```

Fix each failing test by mapping `image_ref` reads to `algorithms[0].image_ref`. Preserve the intent (registered translation reads the image); only the field location has moved.

- [ ] **Step 7: Add failing tests for alias-collision + `--force`**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestAliasCollision:
    def _seed_register(self, tmp_path, algo, image, config_yaml):
        cfg = tmp_path / f"{algo}.yaml"
        cfg.write_text(config_yaml)
        thash, status = sim2real._register_translation(
            algorithm_name=algo,
            image_ref=image,
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
        )
        return thash

    def test_same_alias_same_content_is_idempotent(self, tmp_path):
        h1 = self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        h2 = self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        assert h1 == h2

    def test_alias_collision_without_force_raises(self, tmp_path):
        self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        cfg = tmp_path / "different.yaml"
        cfg.write_text("a: 2\n")
        with pytest.raises(RuntimeError, match="already assigned"):
            sim2real._register_translation(
                algorithm_name="algo",
                image_ref="ghcr.io/x:v1",
                config_path=cfg,
                baseline_config_path=None,
                registered_hash=None,
                now_iso="2026-07-02T14:00:00Z",
            )

    def test_force_reassigns_alias_and_clears_previous(self, tmp_path):
        from pipeline.lib import translation_ref
        h_old = self._seed_register(tmp_path, "algo", "ghcr.io/x:v1", "a: 1\n")
        cfg = tmp_path / "different.yaml"
        cfg.write_text("a: 2\n")
        h_new, _status = sim2real._register_translation(
            algorithm_name="algo",
            image_ref="ghcr.io/x:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
            force=True,
        )
        assert h_new != h_old
        # New translation carries the alias.
        assert translation_ref.find_by_alias("algo") == h_new
        # Old translation's alias is null; it's still reachable by hash.
        old_data = translation_ref.read_translation_output(
            layout.translation_output_path(h_old)
        )
        assert old_data["alias"] is None
        assert layout.translation_dir(h_old).exists()
```

- [ ] **Step 8: Run the alias-collision tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestAliasCollision -v`
Expected: PASS on all cases

- [ ] **Step 9: Run the full test_sim2real to catch regressions from schema change**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v`
Expected: PASS on all cases. If any fail, they are almost always
tests that inspected the old top-level `image_ref` — port them to the
new per-algo shape (see Step 6 guidance).

- [ ] **Step 10: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "translation register: write alias + per-algo image metadata

_build_translation_output now emits the step-2 shape (alias + per-algo
image_ref/image_digest inside algorithms[0]) and drops top-level
image_ref/image_digest. _register_translation enforces alias
uniqueness across writes: same alias on the same hash is idempotent,
same alias on a different hash refuses unless --force. --force
atomically clears the alias on the previous translation and writes
the new one via tempfile + os.replace.

_validate_algorithm_name delegates to translation_ref.validate_name;
the shared regex widens what's accepted (uppercase, dot, underscore)
per design §Alias and algorithm-name validation rules. Existing
'rejects uppercase/underscore' tests flip to 'accepts'. New tests
cover leading -, ., .., oversized (>128 chars).

BYO register still writes registered.json unchanged."
```

---

## Task 3: Route `sim2real assemble --translation` through the resolver + incomplete-translation check

**Files:**
- Modify: `pipeline/sim2real.py` (`_cmd_assemble`)
- Modify: `pipeline/lib/assemble_run.py` (per-algo `image_ref` lookup via shim; incomplete-translation error)
- Test: `pipeline/tests/test_sim2real.py`, `pipeline/tests/test_assemble_run.py`

**Interfaces:**
- Consumes (from task 1): `translation_ref.resolve_translation_ref`, `translation_ref.read_translation_output`, `translation_ref.ResolveError`.
- Consumes (from task 2): `translation_ref.find_by_alias` (indirectly, no direct call).
- Produces (used by task 4): `_cmd_assemble` now accepts a ref (alias/prefix/full-hash) via `--translation`, resolves it to a full hash before calling `assemble_run`. `assemble_run` reads per-algo `image_ref` from `algorithms[i]` (with shim handling legacy top-level). Incomplete-translation surfaces via `AssembleError`.

**Notes:**
- The `--translation` metavar changes from `HASH` to `REF`; help text updates to "alias, hash prefix, or full hash".
- Legacy step-1 shape is normalized by the shim, so all reads route through `read_translation_output`.
- `image_tag` in `run_metadata.json` currently records the (single) top-level `image_ref`. Step-2 keeps a single-image-tag summary field there for backward-compat with the existing `run_metadata.json` schema and step-1 consumers; we set it to `algorithms[0].image_ref` (matches BYO single-algo, sufficient for skill-driven single-algo). Multi-algo image-tag summarization is a PR 3 concern.
- Incomplete-translation error text (verbatim from design line 366): `translation <alias-or-hash> not built for algorithms: <names> — run 'sim2real build --translation <alias-or-hash>' first`. Since assemble now sees the resolved full hash but the user typed a ref, echo the user's original ref in the message. Thread `translation_ref` (the string the user typed) through `assemble_run` as a new kwarg alongside `translation_hash`.

- [ ] **Step 1: Write failing test — assemble accepts alias**

Add to `pipeline/tests/test_sim2real.py`:

```python
class TestAssembleResolvesAlias:
    def test_assemble_accepts_alias(self, tmp_path, monkeypatch):
        # This is a smoke test — we mock assemble_run to just capture
        # the resolved hash. Full assemble behavior is exercised in
        # test_assemble_run.py.
        cfg = tmp_path / "algo.yaml"
        cfg.write_text("scenario: []\n")
        thash, _ = sim2real._register_translation(
            algorithm_name="my-algo",
            image_ref="ghcr.io/x:v1",
            config_path=cfg,
            baseline_config_path=None,
            registered_hash=None,
            now_iso="2026-07-02T14:00:00Z",
        )

        captured = {}
        def fake_assemble(*, translation_hash, translation_ref, cluster_id,
                          run_name, experiment_root, manifest_path,
                          force, now_iso):
            captured["hash"] = translation_hash
            captured["ref"] = translation_ref

        monkeypatch.setattr(
            sim2real._assemble_run_lib, "assemble_run", fake_assemble
        )
        # Also stub the manifest file so the pre-check passes.
        (tmp_path / "transfer.yaml").write_text("kind: sim2real-transfer\n")
        parser = sim2real.build_parser()
        args = parser.parse_args([
            "--experiment-root", str(tmp_path),
            "assemble",
            "--translation", "my-algo",
            "--cluster", "cX",
            "--run", "r1",
        ])
        sim2real.layout.set_experiment_root(str(tmp_path))
        # Mocking cluster_config lookup is out of scope here; the fake
        # replaces assemble_run entirely so cluster_config is never read.
        rc = sim2real._cmd_assemble(args)
        assert rc == 0
        assert captured["hash"] == thash
        assert captured["ref"] == "my-algo"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestAssembleResolvesAlias -v`
Expected: FAIL — `assemble_run` doesn't accept a `translation_ref` kwarg, or `_cmd_assemble` doesn't call resolver.

- [ ] **Step 3: Update `_cmd_assemble` to resolve the ref**

In `pipeline/sim2real.py`, edit `_cmd_assemble` (around lines 356-402). Insert a resolution step before the `assemble_run` call:

```python
def _cmd_assemble(args) -> int:
    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )
    manifest_path = exp_root / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = exp_root / "config" / "transfer.yaml"
    if not manifest_path.exists():
        print(
            f"error: transfer.yaml not found under {exp_root}",
            file=sys.stderr,
        )
        return 2

    from pipeline.lib import translation_ref
    try:
        translation_hash = translation_ref.resolve_translation_ref(args.translation)
    except translation_ref.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _assemble_run_lib.assemble_run(
            translation_hash=translation_hash,
            translation_ref=args.translation,
            cluster_id=args.cluster,
            run_name=args.run,
            experiment_root=exp_root,
            manifest_path=manifest_path,
            force=args.force,
            now_iso=now_iso,
        )
    except _assemble_run_lib.AssembleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # ... rest unchanged
```

Update the `--translation` argparse entry (in `build_parser`) — change `metavar="HASH"` to `metavar="REF"` and help text to `"alias, hash prefix, or full translation hash"`.

- [ ] **Step 4: Update `assemble_run` to accept `translation_ref` and per-algo image lookup**

Edit `pipeline/lib/assemble_run.py`:

1. Add `translation_ref: str` to the `assemble_run` signature (keyword-only). This is the user-facing ref used only in error messages; internal logic keeps using `translation_hash`. Update the docstring to note.

2. Replace the "load tout" block (around lines 493-505) with:

   ```python
   from pipeline.lib import translation_ref as _tref
   try:
       tout = _tref.read_translation_output(tout_path)
   except (json.JSONDecodeError, ValueError) as exc:
       raise AssembleError(
           f"translation_output.json is not valid JSON: {tout_path}: {exc}"
       ) from exc

   translated_algos = {
       a.get("name"): a for a in tout.get("algorithms", []) or []
   }
   translated_names = set(translated_algos.keys())
   ```

3. Remove the top-level `image_ref = tout.get("image_ref")` guard (lines 501-505).

4. Filter algorithms (existing logic, lines 507-510) stays.

5. Add the incomplete-translation check AFTER `filter_algorithms` (after line 510):

   ```python
   unbuilt = [
       a["name"] for a in kept_algos
       if not translated_algos[a["name"]].get("image_ref")
   ]
   if unbuilt:
       raise AssembleError(
           f"translation {translation_ref} not built for algorithms: "
           f"{', '.join(unbuilt)} — run 'sim2real build --translation "
           f"{translation_ref}' first"
       )
   ```

6. In the treatment loop (lines 546-564), replace `inject_image_tag(resolved, image_ref)` with a per-algo lookup:

   ```python
   for algo in kept_algos:
       algo_name = algo["name"]
       base_name = algo["defaults"]
       if base_name not in resolved_baselines:
           raise AssembleError(
               f"algorithm '{algo_name}' references unknown baseline "
               f"'{base_name}'; known: {sorted(resolved_baselines)}"
           )
       diffs_path = _resolve_scenario_path(
           exp_root, algo.get("scenario"), "treatment.yaml"
       )
       overlay_path = generated_root / algo_name / f"{algo_name}_config.yaml"
       resolved = resolve_treatment(
           baseline_resolved=resolved_baselines[base_name],
           diffs_path=diffs_path,
           overlay_path=overlay_path,
       )
       algo_image_ref = translated_algos[algo_name]["image_ref"]
       inject_image_tag(resolved, algo_image_ref)
       packages.append((algo_name, resolved))
   ```

7. Replace the `image_tag` value in `write_run_metadata` (around lines 617-622). Currently `"image_tag": image_ref` used the top-level. Use `algorithms[0].image_ref` as a summary:

   ```python
   run_meta_image_tag = (
       tout["algorithms"][0]["image_ref"]
       if tout.get("algorithms") else ""
   )
   ...
   write_run_metadata(
       run_dir,
       {
           "version": 1,
           "run_name": run_name,
           "translation_hash": translation_hash,
           "cluster_id": cluster_id,
           "params_hash": params_hash,
           "image_tag": run_meta_image_tag,
           "assembled_at": now_iso,
       },
   )
   ```

- [ ] **Step 5: Update the sim2real assemble test from Step 1 to pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestAssembleResolvesAlias -v`
Expected: PASS

- [ ] **Step 6: Update existing test_assemble_run.py fixtures to the new schema**

In `pipeline/tests/test_assemble_run.py`, any test that constructs a `translation_output.json` (search for `image_ref` under that file — the file mocks translations to seed tests) needs to switch from:

```json
{"version":1, "translation_hash":"...", "source":"byo",
 "algorithms":[{"name":"ac1"}], "image_ref":"quay.io/x:v1", "created_at":"..."}
```

to:

```json
{"version":1, "translation_hash":"...", "source":"byo", "alias":"ac1",
 "algorithms":[{"name":"ac1", "source_path":null, "source_sha256":null,
                "config_path":"generated/ac1/ac1_config.yaml",
                "image_ref":"quay.io/x:v1", "image_digest":"sha256:aa"}],
 "created_at":"..."}
```

Also, every call to `assemble_run.assemble_run(...)` needs to add `translation_ref="<hash>"` (or a helper alias) — the new required kwarg.

- [ ] **Step 7: Add a "legacy shape still works via shim" regression test**

Append to `pipeline/tests/test_assemble_run.py` (using existing fixtures where applicable):

```python
class TestLegacyShapeShim:
    def test_legacy_top_level_image_ref_still_resolvable(
        self, tmp_path, monkeypatch
    ):
        # Simulate a step-1 BYO translation_output.json on disk.
        from pipeline.lib import layout, translation_ref
        layout.set_experiment_root(tmp_path)
        thash = "a" * 64
        tdir = layout.translation_dir(thash)
        tdir.mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "byo",
            "algorithms": [{"name": "ac1"}],
            "image_ref": "quay.io/legacy:v1",
            "created_at": "2026-06-01T10:00:00Z",
        }))
        # Shim normalizes it: algorithms[0].image_ref is filled in.
        data = translation_ref.read_translation_output(
            tdir / "translation_output.json"
        )
        assert data["algorithms"][0]["image_ref"] == "quay.io/legacy:v1"
        assert data["alias"] is None
```

- [ ] **Step 8: Add an incomplete-translation test**

Append to `pipeline/tests/test_assemble_run.py`:

```python
class TestIncompleteTranslation:
    def test_missing_image_ref_raises_build_first(self, tmp_path):
        from pipeline.lib import assemble_run, layout
        layout.set_experiment_root(tmp_path)
        thash = "a" * 64
        tdir = layout.translation_dir(thash)
        tdir.mkdir(parents=True)
        (tdir / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": thash,
            "source": "skill",
            "alias": "not-built",
            "algorithms": [{
                "name": "ac1", "source_path": "a.py",
                "source_sha256": "…", "config_path": None,
                "image_ref": None, "image_digest": None,
            }],
            "created_at": "2026-07-02T14:00:00Z",
        }))
        manifest_path = tmp_path / "transfer.yaml"
        manifest_path.write_text(
            "kind: sim2real-transfer\n"
            "version: 3\n"
            "algorithms: [{name: ac1, defaults: b1}]\n"
            "baselines: [{name: b1, scenario: baseline.yaml}]\n"
            "workloads: []\n"
        )
        (tmp_path / "baseline.yaml").write_text("scenario: [{a: 1}]\n")
        # Cluster config seed.
        cluster_cfg_path = layout.cluster_config_path("cX")
        cluster_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cluster_cfg_path.write_text(json.dumps({
            "id": "cX", "namespaces": ["ns1"], "workspaces": {},
        }))
        with pytest.raises(assemble_run.AssembleError, match="not built for algorithms"):
            assemble_run.assemble_run(
                translation_hash=thash,
                translation_ref="not-built",
                cluster_id="cX",
                run_name="r1",
                experiment_root=tmp_path,
                manifest_path=manifest_path,
                force=False,
                now_iso="2026-07-02T14:00:00Z",
            )
```

- [ ] **Step 9: Run all tests**

Run: `.venv/bin/python -m pytest pipeline/tests/test_assemble_run.py pipeline/tests/test_sim2real.py -v 2>&1 | tail -60`
Expected: PASS on all cases. Any failure is a legacy-fixture that still uses top-level `image_ref` in synthetic `translation_output.json` — port it to the new shape.

- [ ] **Step 10: Commit**

```bash
git add pipeline/sim2real.py pipeline/lib/assemble_run.py pipeline/tests/test_sim2real.py pipeline/tests/test_assemble_run.py
git commit -m "assemble: route --translation through resolver; per-algo image_ref

_cmd_assemble resolves the CLI --translation via
translation_ref.resolve_translation_ref (aliases/prefixes/hashes all
accepted). assemble_run gains a translation_ref kwarg (echoed in
error messages) and switches from top-level tout.image_ref to
per-algo lookup in tout.algorithms[i].image_ref (with the shim
handling legacy step-1 top-level shape). An 'incomplete-translation'
error fires when the resolved algorithms list contains any algo with
null image_ref, printing the design-specified 'sim2real build
--translation <ref> first' message. run_metadata.json.image_tag
switches to algorithms[0].image_ref as a summary field."
```

---

## Task 4: `sim2real list translations` subcommand

**Files:**
- Modify: `pipeline/sim2real.py`
- Test: `pipeline/tests/test_sim2real.py`

**Interfaces:**
- Consumes (from task 1): `translation_ref.iter_translations`.
- Produces: `_cmd_list_translations(args) -> int` — prints a 5-column table `ALIAS / HASH / SOURCE / IMAGES / CREATED`.

**Notes:**
- Columns:
  - `ALIAS`: value of `alias` field, or `-` if `None` (legacy / cleared).
  - `HASH`: first 12 chars.
  - `SOURCE`: `skill` or `byo` (from `source` field). Default `?` on missing.
  - `IMAGES`: aggregated per-algo status. Let `built = len([a for a in algos if a.get("image_ref")])`, `total = len(algos)`. If `total == 0` → `-`. If `source == "byo"` → `N registered`. Else if `built == total` → `N built`. Else if `built == 0` → `N pending`. Else → `N/M built`.
  - `CREATED`: `YYYY-MM-DD HH:MM` (mirrors `_format_assembled`). Fall back to `?` on unparseable.
- Sort key: `created_at` newest-first; break ties by hash for determinism.
- Empty translations directory prints `no translations yet`.

- [ ] **Step 1: Write failing test — happy-path table output**

Append to `pipeline/tests/test_sim2real.py`:

```python
class TestListTranslations:
    def test_empty_prints_no_translations(self, capsys, tmp_path):
        # translations_dir absent.
        rc = sim2real._cmd_list_translations(
            sim2real.build_parser().parse_args(["list", "translations"])
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "no translations yet" in out

    def test_shows_alias_hash_source_images_created(self, capsys, tmp_path):
        from pipeline.lib import layout
        layout.set_experiment_root(tmp_path)
        base = layout.translations_dir()
        base.mkdir(parents=True)

        h1 = "a" * 64
        (base / h1).mkdir()
        (base / h1 / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": h1,
            "source": "skill",
            "alias": "softreflective-v1",
            "algorithms": [{"name": "sr", "image_ref": "quay.io/x:v1"}],
            "created_at": "2026-07-02T14:00:00Z",
        }))

        h2 = "b" * 64
        (base / h2).mkdir()
        (base / h2 / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": h2,
            "source": "skill",
            "alias": "compare-a-b",
            "algorithms": [
                {"name": "a", "image_ref": None},
                {"name": "b", "image_ref": None},
            ],
            "created_at": "2026-07-02T14:30:00Z",
        }))

        h3 = "c" * 64
        (base / h3).mkdir()
        (base / h3 / "translation_output.json").write_text(json.dumps({
            "version": 1,
            "translation_hash": h3,
            "source": "byo",
            "alias": None,
            "algorithms": [{"name": "legacy", "image_ref": "ghcr.io/y:v1"}],
            "created_at": "2026-07-01T10:00:00Z",
        }))

        rc = sim2real._cmd_list_translations(
            sim2real.build_parser().parse_args(["list", "translations"])
        )
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        # Header + 3 rows, newest first.
        assert "ALIAS" in lines[0]
        assert "HASH" in lines[0]
        assert "SOURCE" in lines[0]
        assert "IMAGES" in lines[0]
        assert "CREATED" in lines[0]
        # h2 is newest by created_at; h1 middle; h3 oldest.
        assert "compare-a-b" in lines[1]
        assert "softreflective-v1" in lines[2]
        assert "-" in lines[3].split()[0:2]  # ALIAS column shows "-"

        assert "2 pending" in out
        assert "1 built" in out
        assert "1 registered" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestListTranslations -v`
Expected: FAIL — `_cmd_list_translations` not defined; also argparse rejects `list translations`.

- [ ] **Step 3: Implement `_cmd_list_translations` + argparse wiring**

In `pipeline/sim2real.py`:

Add the subparser (edit the `list` block in `build_parser`, around lines 284-286):

```python
    lst = sub.add_parser("list", help="List workspace-scoped resources")
    lsub = lst.add_subparsers(dest="subcommand", required=True)
    lsub.add_parser("runs", help="List runs, newest first")
    lsub.add_parser("translations", help="List translations, newest first")
```

Add `_cmd_list_translations` (near `_cmd_list_runs`, around line 457):

```python
def _summarize_images(source: str, algos: list[dict]) -> str:
    """Return the IMAGES column value for one translation.

    - BYO source: 'N registered' — BYO images are pre-built at register.
    - Skill source, all algos have image_ref: 'N built'.
    - Skill source, all null: 'N pending'.
    - Skill source, mixed: 'N/M built'.
    - Empty algos list: '-'.
    """
    total = len(algos)
    if total == 0:
        return "-"
    built = sum(1 for a in algos if a.get("image_ref"))
    if source == "byo":
        return f"{total} registered"
    if built == total:
        return f"{total} built"
    if built == 0:
        return f"{total} pending"
    return f"{built}/{total} built"


def _cmd_list_translations(_args) -> int:
    from pipeline.lib import translation_ref
    base = layout.translations_dir()
    entries = list(translation_ref.iter_translations(base))
    if not entries:
        print("no translations yet")
        return 0

    # Newest-first by created_at; tie-break on hash for determinism.
    def sort_key(item):
        thash, data = item
        return (data.get("created_at") or "", thash)

    entries.sort(key=sort_key, reverse=True)

    fmt = "{alias:<20} {hash:<12} {source:<8} {images:<15} {created}"
    print(fmt.format(
        alias="ALIAS", hash="HASH", source="SOURCE",
        images="IMAGES", created="CREATED",
    ))
    for thash, data in entries:
        alias = data.get("alias") or "-"
        source = data.get("source") or "?"
        images = _summarize_images(source, data.get("algorithms") or [])
        created = _format_assembled(data.get("created_at") or "")
        print(fmt.format(
            alias=alias, hash=thash[:12], source=source,
            images=images, created=created,
        ))
    return 0
```

Wire the dispatch in `main` (line 516-517):

```python
    if args.command == "list" and args.subcommand == "runs":
        return _cmd_list_runs(args)
    if args.command == "list" and args.subcommand == "translations":
        return _cmd_list_translations(args)
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py::TestListTranslations -v`
Expected: PASS

- [ ] **Step 5: Run the full sim2real test suite**

Run: `.venv/bin/python -m pytest pipeline/tests/test_sim2real.py -v`
Expected: PASS across the board.

- [ ] **Step 6: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "list translations: enumerate workspace/translations/ with alias/status

New 'sim2real list translations' subcommand. Reads via
translation_ref.iter_translations (so legacy top-level image_ref
still counts as 'built'), prints ALIAS/HASH/SOURCE/IMAGES/CREATED
newest-first. IMAGES aggregates per-algo image_ref state:
'N built' when all algos have an image, 'N pending' when none,
'N/M built' when mixed. BYO source always renders 'N registered'.
Empty workspace prints 'no translations yet'."
```

---

## Task 5: Path-safety and regex regression tests (aggregation)

**Files:**
- Modify: `pipeline/tests/test_translation_ref.py`

**Interfaces:** — (no code changes; documents the full path-safety matrix per design line 537)

**Notes:**
- The regex tests already cover the individual rules from task 1. This task adds a parameterized "path-safety matrix" test that mirrors design line 537's list verbatim so it's obvious the rules are enforced end-to-end.

- [ ] **Step 1: Add parameterized test**

Append to `pipeline/tests/test_translation_ref.py`:

```python
class TestPathSafetyMatrix:
    @pytest.mark.parametrize("bad", [
        "../foo",
        "..",
        ".",
        "foo/bar",
        "",
        "a" * 129,
        "-leading-hyphen",
        "@#$%",           # all non-alphanumeric
        ".hidden",
        "foo\x00bar",     # embedded null
        "foo\nbar",       # embedded newline
    ])
    def test_rejects_dangerous_name(self, bad):
        with pytest.raises(ValidationError):
            validate_name(bad)

    @pytest.mark.parametrize("good", [
        "softreflective",
        "algo-v2",
        "Algo_v2",
        "1st",
        "a.b.c",
        "algo_v2.final-1",
        "a" * 128,
    ])
    def test_accepts_safe_name(self, good):
        assert validate_name(good) == good
```

- [ ] **Step 2: Run to verify pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translation_ref.py::TestPathSafetyMatrix -v`
Expected: PASS on all parameterized cases

- [ ] **Step 3: Commit**

```bash
git add pipeline/tests/test_translation_ref.py
git commit -m "translation_ref: parameterized path-safety matrix

Covers the full design line 537 list: path-traversal, ., .., slash,
empty, oversized, leading hyphen, all-non-alphanumeric, hidden-file
form, embedded null, embedded newline. Complements the granular
TestValidateName cases with a compact matrix for future readers."
```

---

## Task 6: Verification sweep + docs + PR

**Files:**
- Run tests
- Sweep docs, skills, README
- Commit sweep changes if any
- Push, open PR

- [ ] **Step 1: Full pipeline test suite green**

Run: `.venv/bin/python -m pytest pipeline/ -v 2>&1 | tail -60`
Expected: All tests pass. If any failure, fix it before proceeding.

- [ ] **Step 2: Lint**

Run: `.venv/bin/python -m ruff check pipeline/ --select F`
Expected: No output (success). Fix any warnings.

- [ ] **Step 3: Sweep for stale references**

Search for docs/README/skills that reference the old translation_output.json shape or the register CLI:

```bash
grep -rn "translation_output" pipeline/README.md docs/ .claude/skills/ CLAUDE.md 2>&1 | head
grep -rn "image_ref" pipeline/README.md docs/ .claude/skills/ CLAUDE.md 2>&1 | head -30
grep -rn "sim2real translation register" pipeline/README.md docs/ .claude/skills/ CLAUDE.md 2>&1 | head
grep -rn "sim2real assemble" pipeline/README.md docs/ .claude/skills/ CLAUDE.md 2>&1 | head
grep -rn "sim2real list translations" docs/ .claude/skills/ CLAUDE.md 2>&1 | head
```

For each hit, decide: stale (update it), still accurate, or unrelated. Common candidates:
- `pipeline/README.md` — probably describes the register command and translation_output.json shape. Update the shape example and mention `--force`.
- `.claude/skills/sim2real-*/SKILL.md` — check if any skill prompt references the shape.
- `docs/epics/step-2/design.md` — the source of truth; don't rewrite.
- `CLAUDE.md` in the worktree — update if it describes the alias UX or translation_output.json schema.

- [ ] **Step 4: Commit sweep changes (if any)**

```bash
git add <files>
git commit -m "docs: sweep for translation_output.json shape references

Updates docs/skills to reflect per-algo image_ref/image_digest, the
alias field, and the new 'sim2real list translations' subcommand."
```

If no files needed updating, skip this step and note it in the PR body.

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin refactor/v2-step-2-issue-466-alias-resolver
gh pr create --base refactor/v2-step-2 \
  --title "step-2 PR 2: alias resolver + schema plumbing + sim2real list translations" \
  --body-file /tmp/pr-body-466.md
```

PR body template (write to `/tmp/pr-body-466.md`):

```markdown
Closes #466.

## Summary

Implements step-2 PR 2:

- **`pipeline/lib/translation_ref.py`** — new module. Shared validation
  regex (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, max 128 chars, rejects `.`/`..`/
  leading `-`/empty), on-read shim that normalizes legacy step-1
  top-level `image_ref`/`image_digest` into per-algorithm fields,
  translations iterator (malformed files logged + skipped),
  `find_by_alias`, and `resolve_translation_ref` (name → prefix → hash
  precedence per design).
- **`sim2real translation register`** — writes the new step-2 schema:
  `alias` field (from `--algorithm`), per-algo `image_ref`/`image_digest`
  inside `algorithms[0]`, plus `source_path`/`source_sha256`/`config_path`
  per-algo. Refuses on alias collision unless `--force`. `--force`
  atomically clears the alias on the previous translation and writes the
  new one via tempfile + `os.replace`.
- **`sim2real assemble --translation`** — accepts alias, hash prefix, or
  full hash (metavar changed from `HASH` to `REF`). Errors with the
  design-specified "run `sim2real build --translation <ref>` first"
  message when any resolved algorithm has null `image_ref`.
- **`sim2real list translations`** — new subcommand. Prints
  `ALIAS / HASH / SOURCE / IMAGES / CREATED` newest-first. IMAGES
  summarizes per-algo image state (`N built`, `N pending`, `N/M built`,
  `N registered` for BYO).

## Schema migration

Legacy step-1 BYO `translation_output.json` files (top-level `image_ref`,
no `alias`) remain resolvable and buildable via the on-read shim — no
forced migration, no `version` bump. Users who re-register a legacy
translation with the same `--algorithm` name will hit the alias
uniqueness check (existing translations have no alias, so the fresh
register succeeds without `--force`; the old translation stays alias-less
until its owner re-registers).

## Reviewer attention

- `translation_ref.resolve_translation_ref` precedence: alias > prefix.
  A 4-char alias that also happens to be a hash prefix will resolve to
  the alias — that's the design's choice (line 384-388: aliases checked
  before prefix scan).
- `--force` atomicity: two separate single-file `os.replace` operations,
  not a cross-file transaction. If the clear-old succeeds and the new
  write fails, the old translation loses its alias but is still
  hash-reachable; operator re-runs `register`.
- `run_metadata.json.image_tag` now records `algorithms[0].image_ref` as
  a summary. For BYO single-algo (the shape shipping in this PR), this
  is identical to the step-1 behavior. Multi-algo skill-driven summary
  is a PR 3 concern.

## Sweep

I greped `pipeline/README.md`, `docs/`, `.claude/skills/`, and `CLAUDE.md`
for `translation_output`, `image_ref`, and the register/assemble CLI
strings. [List updates here, or "no doc updates needed".]

## Test plan

- [ ] `.venv/bin/python -m pytest pipeline/ -v` all green
- [ ] `.venv/bin/python -m ruff check pipeline/ --select F` clean
- [ ] Manual smoke: create a fake translation via `sim2real translation
      register --algorithm foo`, then `sim2real list translations`,
      then `sim2real translation register --algorithm foo --force` with
      a different config and confirm alias reassignment.
```

Then open the PR with the actual `--body-file`. Verify PR URL, print it.
