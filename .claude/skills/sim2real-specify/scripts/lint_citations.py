#!/usr/bin/env python3
"""Resolve `path:line` citations in a sim2real bundle against pinned checkouts.

Mechanical only: verifies that a cited path exists in exactly one pinned tree and
that the cited line numbers are within that file. Cannot detect a correct citation
attached to a wrong transcription -- that is the audit agent's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCE_EXTS = ("go", "py", "md", "yaml", "yml", "json")

CITATION_RE = re.compile(
    r"(?<![\w/.-])"                                    # not mid-token
    r"([A-Za-z0-9_][\w./-]*\.(?:" + "|".join(SOURCE_EXTS) + r"))"
    r":(\d+(?:[-,]\d+)*)"                              # 12 | 12-20 | 12,20
    r"(?!\d)"                                          # whole number only
)
# NOTE: do not add `.` to that final lookahead. Citations frequently end a
# sentence -- `sim/edpp.go:1707.` -- and excluding a trailing period silently
# drops every one of them. Version-like noise is already excluded by requiring a
# known source extension in the path group.

SKIP_MARKER = "lint-skip"


@dataclass(frozen=True)
class Citation:
    path: str
    lines: tuple[int, ...]
    source_line: int

    def __str__(self) -> str:
        return f"{self.path}:{','.join(str(n) for n in self.lines)}"


def parse_line_spec(spec: str) -> tuple[int, ...]:
    """'168' -> (168,); '144-157' -> (144, 157); '33,153' -> (33, 153)."""
    return tuple(int(part) for part in re.split(r"[-,]", spec) if part)


def parse_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if SKIP_MARKER in line:
            continue
        for path, spec in CITATION_RE.findall(line):
            out.append(
                Citation(path=path, lines=parse_line_spec(spec), source_line=lineno)
            )
    return out
