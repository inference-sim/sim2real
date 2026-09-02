"""CLI filter-value primitives shared by ``deploy.py`` and ``sim2real assemble``.

Both commands expose ``--workload`` / ``--package`` flags with the same
grammar: ``nargs="+"`` values that may additionally be comma-separated, and
that may be shell-glob patterns rather than literal names. The parsing and
glob expansion live here so the two commands cannot drift apart (issue #876).
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable

# Any of these in a value makes it a shell-glob pattern; otherwise the value
# is a literal.
_GLOB_METACHARS = ("*", "?", "[")


def is_glob(value: str) -> bool:
    return any(c in value for c in _GLOB_METACHARS)


def parse_name_list(value) -> "list[str] | None":
    """Flatten a CLI flag value (possibly a list from nargs='+') by splitting on commas."""
    if value is None:
        return None
    if isinstance(value, list):
        result = [v.strip() for item in value for v in item.split(",") if v.strip()]
    else:
        result = [v.strip() for v in value.split(",") if v.strip()]
    return result if result else None


def expand_glob_values(
    values: "Iterable[str]",
    valid: "Iterable[str]",
    *,
    exclude_from_pattern: "Iterable[str]" = frozenset(),
) -> "tuple[list[str], list[str]]":
    """Expand a mixed list of literals and shell-glob patterns against *valid*.

    A value containing ``*``, ``?``, or ``[`` is treated as an ``fnmatch`` pattern;
    otherwise it must be a literal member of *valid*. Patterns match against
    ``valid - exclude_from_pattern`` so magic tokens (e.g. ``experiment``) remain
    literal-only and are never surfaced by a pattern like ``exp*``.

    Returns ``(expanded, unknown)`` where *expanded* preserves the order of the
    user's input (first occurrence wins) and *unknown* lists literals not in
    *valid* plus patterns that matched zero names.
    """
    pattern_pool = sorted(set(valid) - set(exclude_from_pattern))
    valid_set = set(valid)
    expanded: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []
    for v in values:
        if is_glob(v):
            matches = [n for n in pattern_pool if fnmatch.fnmatchcase(n, v)]
            if not matches:
                unknown.append(v)
                continue
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    expanded.append(m)
        elif v in valid_set:
            if v not in seen:
                seen.add(v)
                expanded.append(v)
        else:
            unknown.append(v)
    return expanded, unknown
