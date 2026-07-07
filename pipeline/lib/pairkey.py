"""Pair-key parser for the sim2real ConfigMap key model (step-5).

Grammar (canonical form):

    pair_key := "wl-" workload "|" package [ "|" iter ]
    workload := [a-z0-9]([a-z0-9-]*[a-z0-9])?     # kebab-case, no leading/trailing hyphen
    package  := [a-z0-9]([a-z0-9-]*[a-z0-9])?     # same as workload
    iter     := "i" [1-9][0-9]*                   # positive decimal, no leading zeros

The ``wl-`` prefix is a literal marker, not part of the workload name;
``PairKeyParts.workload`` stores the workload without it. Metadata keys
(``_meta``, ``_notes``) are not pair keys and must be filtered out
upstream (typically via ``deploy._is_pair_key``) before parsing.

Legacy pair keys (no ``|iN`` suffix) parse as ``iteration=1``. The
canonical rendering emitted by ``PairKeyParts.to_key`` always includes
the ``|iN`` suffix — legacy input round-trips to a canonical form.

Malformed keys raise ``ValueError`` with a message naming the offending
key. Callers that want tolerance (e.g. ``deploy._load_pairs``) wrap
``parse_pair_key`` in a try/except.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_IDENT = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_ITER_SUFFIX = r"i[1-9][0-9]*"
_PAIR_KEY_RE = re.compile(
    rf"^wl-(?P<workload>{_IDENT})\|(?P<package>{_IDENT})(?:\|(?P<iter>{_ITER_SUFFIX}))?$"
)


@dataclass(frozen=True)
class PairKeyParts:
    """Structural view of a canonical pair key.

    ``workload`` and ``package`` are kebab-case identifiers with no
    ``wl-`` prefix and no leading/trailing hyphens. ``iteration`` is a
    positive integer (>= 1). Legacy keys without an ``|iN`` suffix
    parse to ``iteration=1``.
    """

    workload: str
    package: str
    iteration: int

    def to_key(self) -> str:
        """Reconstruct the canonical pair-key string.

        Always emits the ``|iN`` suffix, even for iteration 1. This
        normalizes legacy input to the canonical form.
        """
        return f"wl-{self.workload}|{self.package}|i{self.iteration}"


def parse_pair_key(key: str) -> PairKeyParts:
    """Parse ``key`` per the grammar above.

    Legacy keys (no ``|iN`` suffix) parse as ``iteration=1``. Raises
    ``ValueError`` with a message naming ``key`` on any grammar
    violation (empty parts, uppercase characters, embedded ``|``
    inside a component, ``i0`` or ``i01`` iteration, missing ``wl-``
    prefix, extra segments).

    Callers should filter metadata keys (``_meta``, ``_notes``) with a
    predicate such as ``deploy._is_pair_key`` before calling this.
    """
    if not isinstance(key, str):
        raise ValueError(f"malformed pair key {key!r}: expected str")
    m = _PAIR_KEY_RE.match(key)
    if not m:
        raise ValueError(f"malformed pair key {key!r}: does not match grammar")
    iter_group = m.group("iter")
    iteration = int(iter_group[1:]) if iter_group else 1
    return PairKeyParts(
        workload=m.group("workload"),
        package=m.group("package"),
        iteration=iteration,
    )


def parse_iteration_spec(spec: str) -> set[int]:
    """Parse an ``--iteration`` filter spec into a set of iteration ints.

    Supported syntax (list + range):

        "2"        -> {2}
        "1,3"      -> {1, 3}
        "1-3"      -> {1, 2, 3}
        "1,3-5"    -> {1, 3, 4, 5}

    Whitespace around commas and hyphens is tolerated. Raises
    ``ValueError`` on empty spec, non-positive integers (``0``,
    negative), reversed ranges (``5-1``), non-integer tokens (``abc``),
    or malformed tokens (leading zeros, empty range endpoint).
    """
    if not isinstance(spec, str):
        raise ValueError(f"malformed iteration spec {spec!r}: expected str")
    stripped = spec.strip()
    if not stripped:
        raise ValueError(f"malformed iteration spec {spec!r}: empty")
    result: set[int] = set()
    for token in stripped.split(","):
        token = token.strip()
        if not token:
            raise ValueError(f"malformed iteration spec {spec!r}: empty token")
        if "-" in token:
            parts = [p.strip() for p in token.split("-")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"malformed iteration spec {spec!r}: bad range {token!r}")
            lo = _parse_positive_int(parts[0], spec)
            hi = _parse_positive_int(parts[1], spec)
            if lo > hi:
                raise ValueError(f"malformed iteration spec {spec!r}: reversed range {token!r}")
            result.update(range(lo, hi + 1))
        else:
            result.add(_parse_positive_int(token, spec))
    return result


def _parse_positive_int(token: str, spec: str) -> int:
    """Parse a positive decimal integer with no leading zeros.

    ``spec`` is the caller's full spec string, quoted verbatim in error
    messages so operators can trace the offending input.
    """
    if not re.fullmatch(r"[1-9][0-9]*", token):
        raise ValueError(f"malformed iteration spec {spec!r}: bad integer {token!r}")
    return int(token)
