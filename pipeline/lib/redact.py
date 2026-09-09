"""YAML redaction for collected plan and resource files.

Stubs out sensitive field values so collected YAMLs do not carry credentials
into developer laptops or shared run dirs — both the llm-d-benchmark plan files
and each iteration's ``resources/`` snapshot, which since #886 is redacted on
every collect path rather than only under ``--skip-logs``.

Two independent stubbing mechanisms run over every file (defense in depth):

  1. **Kind-based** — for Kubernetes objects whose `kind` matches a
     denylist (default: ``Secret``), every value under `data` /
     `stringData` is replaced with ``REDACTED``.
  2. **Key-name-based** — for *any* document regardless of `kind`, a
     recursive walk replaces values whose key names a credential
     (``token``, ``password``, ``apiKey`` … — see ``SENSITIVE_KEYS``).
     This catches credentials inlined as ordinary config fields, e.g.
     an llm-d-benchmark harness plan's ``huggingface.token`` (issue
     #819), which is neither a ``Secret`` nor under `data`/`stringData`.

A third mechanism *prunes* rather than stubs:

  3. **Annotation prune** — removes annotations that are a verbatim copy
     of the manifest they annotate (``PRUNE_ANNOTATIONS``, currently just
     ``kubectl.kubernetes.io/last-applied-configuration``). Unlike the two
     stubbing mechanisms this deletes a key outright, because the value
     carries no information the same document does not already hold — and
     because keeping it *defeats* mechanism 2: the copy is a JSON
     **string**, so a credential inside it is not a YAML field the
     recursive walk can reach, and would survive on disk while the live
     field beside it was stubbed (issue #894).

Behavior:
  - Matching values are replaced with the literal string ``REDACTED``.
    Key names are always preserved.
  - Reference/name fields that merely point at a secret (``secretName``,
    ``tokenKey``, ``contextSecretName``) are NOT credentials and are left
    intact — key matching is exact (case-insensitive), not substring.
  - Multi-doc YAML files are processed per-document. A document is
    counted once if any mechanism changed it; documents with no
    `kind` are labelled ``document`` in the summary header. Pruned
    annotations are counted separately in that header, since a prune
    removes data rather than masking it and should not be silent.
  - When a prune empties an ``annotations`` mapping, the mapping itself
    is removed rather than left behind as ``annotations: {}``.
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

# Annotation keys removed outright wherever an ``annotations`` mapping appears.
#
# ``kubectl.kubernetes.io/last-applied-configuration`` is a verbatim JSON copy
# of the manifest that was applied, so it duplicates the spec already present in
# the same document. Two reasons it is pruned rather than tolerated:
#
#   - It is a redaction blind spot. The value is one long JSON *string*, so
#     ``_stub_sensitive_keys`` — which descends dicts and lists — cannot reach a
#     credential inside it. Stubbing the live field while its copy survives in
#     the annotation leaves the value on disk. Pruning removes the class of miss
#     instead of chasing each instance (issue #894).
#   - It is the dominant high-entropy finding in collected manifests, and being
#     a whole JSON document rather than a credential-shaped token, it is not
#     suppressible by field name in the bootstrap-scaffolded detect-secrets hook
#     (issue #822).
#
# Matching is exact on the annotation key — no normalization — because these are
# fully-qualified Kubernetes annotation keys, not the free-form config field
# names ``SENSITIVE_KEYS`` has to tolerate spelling variants of.
PRUNE_ANNOTATIONS: frozenset[str] = frozenset({
    "kubectl.kubernetes.io/last-applied-configuration",
})

# Key names whose values are credentials and must be stubbed wherever they
# appear, in any document. Matching is exact against a normalized form of
# the key: lowercased with ``_`` / ``-`` separators stripped (see
# ``_normalize_key``), so ``accessKey``, ``access_key``, ``access-key`` and
# ``ACCESSKEY`` all match the single entry ``accesskey``. Entries here are
# therefore stored in that same separator-free lowercase form.
#
# Deliberately NOT matched — these reference or name a secret but are not
# themselves secret; none normalizes to an entry below: ``secretName``
# (→ ``secretname``), ``tokenKey`` (→ ``tokenkey``), ``contextSecretName``
# (→ ``contextsecretname``).
SENSITIVE_KEYS: frozenset[str] = frozenset({
    "token",
    "tokenbase64",
    "password",
    "apikey",
    "accesskey",
    "secretaccesskey",
    "authorization",
    "bearertoken",
})


def _normalize_key(key: str) -> str:
    """Lowercase and strip ``_`` / ``-`` so separator style doesn't matter.

    ``accessKey``, ``access_key``, ``access-key`` and ``ACCESSKEY`` all
    normalize to ``accesskey``. This keeps the denylist a single entry per
    key while catching every common YAML spelling — without it, camelCase
    and snake_case forms of the same field would need separate entries and
    a missing one would silently bypass redaction (the class of bug in #819).
    """
    return key.lower().replace("_", "").replace("-", "")


def _stub_sensitive_keys(node: object) -> bool:
    """Recursively stub values under sensitive keys, in-place.

    Walks nested dicts and lists. A value is replaced with ``REDACTED``
    when its key matches ``SENSITIVE_KEYS`` after normalization (see
    ``_normalize_key`` — case- and separator-insensitive exact match); the
    sensitive value is stubbed wholesale and not descended into. Recursion
    continues through every other container value.

    Already-redacted values are left as-is (so a re-run over a scrubbed
    file reports no change). Returns True if any value was changed.
    """
    changed = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if isinstance(key, str) and _normalize_key(key) in SENSITIVE_KEYS:
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


def _prune_annotations(node: object) -> int:
    """Recursively remove ``PRUNE_ANNOTATIONS`` keys, in-place.

    Walks nested dicts and lists looking for any mapping under an
    ``annotations`` key — top-level ``metadata.annotations`` in a collected
    object, but also nested ones such as a Deployment's
    ``spec.template.metadata.annotations``, since an applied manifest can carry
    the annotation there too.

    When the prune empties an ``annotations`` mapping the mapping itself is
    deleted, so a document whose only annotation was pruned does not keep a
    childless ``annotations: {}``. An ``annotations`` mapping that was already
    empty before the walk is left alone — this function reports what it removed,
    and deleting a key it did not empty would make the count a lie.

    Returns the number of annotation keys removed.
    """
    removed = 0
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key == "annotations" and isinstance(value, dict):
                hit = [k for k in value if k in PRUNE_ANNOTATIONS]
                for k in hit:
                    del value[k]
                removed += len(hit)
                if hit and not value:
                    del node[key]
                    continue
            removed += _prune_annotations(value)
    elif isinstance(node, list):
        for item in node:
            removed += _prune_annotations(item)
    return removed


def _format_header(counts: Counter, pruned: int = 0) -> str:
    """Render the summary comment prepended to a rewritten file.

    *counts* is docs-stubbed-per-kind; *pruned* is the number of annotation
    keys removed across the whole file. The prune is reported alongside the
    stubs rather than silently, because it deletes data instead of masking it.
    """
    parts = []
    for kind, n in sorted(counts.items()):
        suffix = "s" if n != 1 else ""
        parts.append(f"{n} {kind}{suffix} stubbed")
    if pruned:
        suffix = "s" if pruned != 1 else ""
        parts.append(f"{pruned} annotation{suffix} pruned")
    return f"# REDACTED by sim2real collect: {', '.join(parts)}\n"


def redact_yaml_file(path: Path, kinds: Iterable[str] | None = None) -> int:
    """Redact credentials in `path`, in place.

    Three mechanisms run per document (see the module docstring): the
    kind-based `data`/`stringData` scrub for docs whose `kind` is in
    `kinds`, the key-name scrub (`_stub_sensitive_keys`) on every doc
    regardless of `kind`, and the annotation prune (`_prune_annotations`),
    also on every doc.

    Returns the count of docs changed by any of the three. Returns 0
    (without rewriting the file) for: files with nothing to redact or
    prune, files that aren't valid YAML, or files that can't be read.
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

    # `counts` drives the "N <kind> stubbed" terms in the header and so is
    # incremented only for documents a *stubbing* mechanism changed. Pruned
    # annotations are tallied separately in `pruned`: a document whose only
    # change was a prune must not be reported as stubbed, since nothing in it
    # was masked. `docs_changed` is what the caller gets, and what decides
    # whether the file is rewritten at all.
    counts: Counter = Counter()
    pruned = 0
    docs_changed = 0
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
        # Prune order against the stubs does not matter: it deletes whole keys
        # the stubs never touch, and the stubs cannot reach inside the JSON
        # string it removes — which is the reason it is removed (#894).
        doc_pruned = _prune_annotations(doc)
        pruned += doc_pruned
        if changed or doc_pruned:
            docs_changed += 1

    if docs_changed == 0:
        return 0

    body = yaml.safe_dump_all(docs, sort_keys=False, default_flow_style=False)
    output = _format_header(counts, pruned) + body

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

    return docs_changed


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
