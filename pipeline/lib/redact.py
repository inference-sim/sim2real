"""YAML redaction for collected plan files.

Stubs out sensitive field values so collected plan YAMLs do not carry
credentials into developer laptops or shared run dirs. Two independent
mechanisms run over every file (defense in depth):

  1. **Kind-based** — for Kubernetes objects whose `kind` matches a
     denylist (default: ``Secret``), every value under `data` /
     `stringData` is replaced with ``REDACTED``.
  2. **Key-name-based** — for *any* document regardless of `kind`, a
     recursive walk replaces values whose key names a credential
     (``token``, ``password``, ``apiKey`` … — see ``SENSITIVE_KEYS``).
     This catches credentials inlined as ordinary config fields, e.g.
     an llm-d-benchmark harness plan's ``huggingface.token`` (issue
     #819), which is neither a ``Secret`` nor under `data`/`stringData`.

Behavior:
  - Matching values are replaced with the literal string ``REDACTED``.
    Key names are always preserved.
  - Reference/name fields that merely point at a secret (``secretName``,
    ``tokenKey``, ``contextSecretName``) are NOT credentials and are left
    intact — key matching is exact (case-insensitive), not substring.
  - Multi-doc YAML files are processed per-document. A document is
    counted once if either mechanism changed it; documents with no
    `kind` are labelled ``document`` in the summary header.
  - Files with no changes are not rewritten.
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

# Key names (matched case-insensitively, exact — not substring) whose
# values are credentials and must be stubbed wherever they appear, in any
# document. Stored lowercased; compared against ``key.lower()``.
#
# Deliberately excluded — these reference or name a secret but are not
# themselves secret: ``secretName``, ``tokenKey``, ``contextSecretName``.
SENSITIVE_KEYS: frozenset[str] = frozenset({
    "token",
    "tokenbase64",
    "password",
    "apikey",
    "api_key",
    "accesskey",
    "secretaccesskey",
    "authorization",
    "bearertoken",
})


def _stub_sensitive_keys(node: object) -> bool:
    """Recursively stub values under sensitive keys, in-place.

    Walks nested dicts and lists. A value is replaced with ``REDACTED``
    when its key is in ``SENSITIVE_KEYS`` (case-insensitive exact match);
    the sensitive value is stubbed wholesale and not descended into.
    Recursion continues through every other container value.

    Already-redacted values are left as-is (so a re-run over a scrubbed
    file reports no change). Returns True if any value was changed.
    """
    changed = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
                if value != REDACTED:
                    node[key] = REDACTED
                    changed = True
            elif _stub_sensitive_keys(value):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _stub_sensitive_keys(item):
                changed = True
    return changed


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
        changed = kind in redact_set and _stub_data_fields(doc)
        # Key-name scrub runs on every document, regardless of kind.
        changed = _stub_sensitive_keys(doc) or changed
        if changed:
            label = kind if isinstance(kind, str) else "document"
            counts[label] += 1

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
