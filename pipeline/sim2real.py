#!/usr/bin/env python3
"""sim2real top-level CLI.

Subcommands land incrementally across the step-1 epic. This file is
created by PR 1 with only ``translation register``. Subsequent PRs add
``assemble``, ``use``, ``list runs``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
