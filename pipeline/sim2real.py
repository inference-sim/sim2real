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
