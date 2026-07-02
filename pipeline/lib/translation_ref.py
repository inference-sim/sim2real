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
