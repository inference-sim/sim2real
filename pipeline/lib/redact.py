"""YAML redaction for collected plan files.

Stubs out sensitive field values in Kubernetes objects whose `kind`
matches a denylist, so collected plan YAMLs do not carry credentials
into developer laptops or shared run dirs.

Behavior:
  - `data` and `stringData` values in matching docs are replaced with
    the literal string ``REDACTED``. Key names are preserved.
  - Multi-doc YAML files are processed per-document; non-matching docs
    pass through unchanged.
  - Files without any matching docs are not rewritten.
  - Unreadable / unparseable files are left untouched (warning logged).
  - Writes go through a sibling tmp file + atomic ``os.replace`` so a
    process crash mid-write cannot leave a half-redacted file on disk.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterable

import yaml

from pipeline.lib.log import warn

REDACTED = "REDACTED"

DEFAULT_REDACT_KINDS: frozenset[str] = frozenset({"Secret"})


def _stub_data_fields(doc: dict) -> bool:
    """Replace every value under data/stringData with REDACTED in-place.

    Returns True if any value was actually changed. Already-redacted
    values are left as-is and do not count as a change (so an
    already-redacted file produces a 0-count pass and skips rewrite).
    """
    changed = False
    for field in ("data", "stringData"):
        section = doc.get(field)
        if not isinstance(section, dict):
            continue
        for key, value in list(section.items()):
            if value != REDACTED:
                section[key] = REDACTED
                changed = True
    return changed


def _format_header(counts: Counter) -> str:
    parts = []
    for kind, n in sorted(counts.items()):
        suffix = "s" if n != 1 else ""
        parts.append(f"{n} {kind}{suffix} stubbed")
    return f"# REDACTED by sim2real collect: {', '.join(parts)}\n"


def redact_yaml_file(path: Path, kinds: Iterable[str] | None = None) -> int:
    """Redact data/stringData values for kind-matching docs in `path`.

    Returns the count of docs that were redacted. Returns 0 (without
    rewriting the file) for: files with no matching docs, files that
    aren't valid YAML, or files that can't be read.
    """
    redact_set = frozenset(kinds) if kinds is not None else DEFAULT_REDACT_KINDS

    try:
        text = path.read_text()
    except OSError as e:
        warn(f"redact: could not read {path}: {e}")
        return 0

    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        warn(f"redact: skipping unparseable YAML {path.name}: {e}")
        return 0

    counts: Counter = Counter()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        if kind in redact_set and _stub_data_fields(doc):
            counts[kind] += 1

    total = sum(counts.values())
    if total == 0:
        return 0

    body = yaml.safe_dump_all(docs, sort_keys=False, default_flow_style=False)
    output = _format_header(counts) + body

    tmp = path.with_suffix(path.suffix + ".redact.tmp")
    try:
        tmp.write_text(output)
        tmp.replace(path)
    except OSError as e:
        warn(f"redact: write failed for {path}: {e}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return 0

    return total


def redact_yaml_tree(root: Path, kinds: Iterable[str] | None = None) -> int:
    """Run ``redact_yaml_file`` over every ``*.yaml`` / ``*.yml`` under root.

    Recursive. Non-yaml files are ignored. A missing root directory is a
    silent no-op (returns 0). Returns the total count of docs redacted
    across all files.
    """
    if not root.is_dir():
        return 0
    total = 0
    for path in sorted(root.rglob("*.yaml")):
        total += redact_yaml_file(path, kinds=kinds)
    for path in sorted(root.rglob("*.yml")):
        total += redact_yaml_file(path, kinds=kinds)
    return total
