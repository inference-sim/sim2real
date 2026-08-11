#!/usr/bin/env python3
"""Resolve `path:line` citations in a sim2real bundle against pinned checkouts.

Mechanical only: verifies that a cited path exists in exactly one pinned tree and
that the cited line numbers are within that file. Cannot detect a correct citation
attached to a wrong transcription -- that is the audit agent's job.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class Failure:
    file: str
    source_line: int
    citation: str
    kind: str
    detail: str


def index_tree(root: Path) -> dict[str, list[Path]]:
    """Map basename -> files, skipping dot-directories."""
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        index.setdefault(path.name, []).append(path)
    return index


def _candidates(cit: Citation, indexes: list[dict[str, list[Path]]]) -> list[Path]:
    """Files whose path ends with the cited path.

    Suffix matching is required because real citations range from a rooted
    `sim/edpp_var.go` to a bare `extractor.go`. Ambiguity is reported rather than
    guessed, which pushes authors toward more specific citations.
    """
    basename = cit.path.rsplit("/", 1)[-1]
    out: list[Path] = []
    for index in indexes:
        for path in index.get(basename, []):
            if path.as_posix().endswith(cit.path):
                out.append(path)
    return out


def resolve(cit: Citation, indexes: list[dict[str, list[Path]]]) -> Failure | None:
    matches = _candidates(cit, indexes)
    if not matches:
        return Failure(
            "", cit.source_line, str(cit), "unresolved-path",
            "no file in any pinned tree ends with this path",
        )
    if len(set(matches)) > 1:
        shown = ", ".join(sorted(p.as_posix() for p in set(matches))[:4])
        return Failure(
            "", cit.source_line, str(cit), "ambiguous-path",
            f"matches {len(set(matches))} files: {shown}",
        )
    target = matches[0]
    nlines = len(target.read_text(errors="replace").splitlines())
    over = [n for n in cit.lines if n > nlines or n < 1]
    if over:
        return Failure(
            "", cit.source_line, str(cit), "line-out-of-range",
            f"{target.name} has {nlines} lines; cited {over}",
        )
    return None


def lint_bundle(
    bundle: Path, trees: list[Path], exts: tuple[str, ...]
) -> list[Failure]:
    # The bundle indexes itself so intra-bundle references (README.md:22) resolve
    # without being reported as dangling.
    indexes = [index_tree(t) for t in [*trees, bundle]]
    failures: list[Failure] = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.suffix not in exts:
            continue
        if any(part.startswith(".") for part in path.relative_to(bundle).parts[:-1]):
            continue
        text = path.read_text(errors="replace")
        for cit in parse_citations(text):
            failure = resolve(cit, indexes)
            if failure is not None:
                rel = path.relative_to(bundle).as_posix()
                failures.append(
                    Failure(
                        rel, failure.source_line, failure.citation,
                        failure.kind, failure.detail,
                    )
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lint path:line citations in a sim2real bundle."
    )
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument(
        "--tree", action="append", default=[], type=Path,
        help="pinned checkout root; repeatable",
    )
    ap.add_argument(
        "--ext", default=".go,.md",
        help="comma-separated file extensions to scan",
    )
    args = ap.parse_args(argv)

    if not args.bundle.is_dir():
        print(f"usage error: --bundle {args.bundle} is not a directory", file=sys.stderr)
        return 2
    for tree in args.tree:
        if not tree.is_dir():
            print(f"usage error: --tree {tree} is not a directory", file=sys.stderr)
            return 2

    exts = tuple(e if e.startswith(".") else f".{e}" for e in args.ext.split(","))
    failures = lint_bundle(args.bundle, args.tree, exts)
    for f in failures:
        print(
            f"FAIL {f.kind} {f.file}:{f.source_line} "
            f"cite={f.citation} -- {f.detail}"
        )
    print(f"{len(failures)} citation failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
