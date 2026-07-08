#!/usr/bin/env python3
"""List analyses in the sim2real-analyze catalog.

Discovers .md files under `.claude/skills/sim2real-analyze/analyses/`,
parses YAML front-matter, and prints a JSON array of entries to stdout.
Malformed entries are reported to stderr and skipped so the catalog
remains usable when a single entry is broken.

Front-matter schema:
    name: <slug>          # required
    title: <str>          # required
    when-to-use: <str>    # required
    inputs: <str>         # required (currently only "run")
    output: <str>         # required ("html" | "png" | "table")
    runner: <str>         # required ("script" | "prompt")
    script: <filename>    # required when runner == "script";
                          # path is relative to the analyses/ directory

Usage:
    python .claude/skills/sim2real-analyze/scripts/list_analyses.py \
        [--analyses-dir PATH]
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "Error: PyYAML required — install with `pip install PyYAML`",
        file=sys.stderr,
    )
    sys.exit(1)


REQUIRED_FIELDS = ("name", "title", "when-to-use", "inputs", "output", "runner")
VALID_RUNNERS = {"script", "prompt"}
VALID_OUTPUTS = {"html", "png", "table"}


def warn(msg: str) -> None:
    print(f"Warning: {msg}", file=sys.stderr)


def _default_analyses_dir() -> Path:
    """analyses/ directory as a sibling of scripts/ (this script's location)."""
    return Path(__file__).resolve().parent.parent / "analyses"


def _parse_front_matter(text: str) -> dict | None:
    """Extract the YAML block delimited by leading `---` / `---`.

    Returns the parsed dict, or None when the file lacks a front-matter block
    or the block fails to parse. Errors are reported by the caller with the
    file path for context.
    """
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return None
    body = text.split("\n", 1)[1] if text.startswith("---\n") else text.split("\r\n", 1)[1]
    end = body.find("\n---")
    if end == -1:
        return None
    yaml_block = body[:end]
    try:
        data = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _validate(entry: dict, entry_path: Path, analyses_dir: Path) -> str | None:
    """Return None if the entry is valid, else a reason string for the caller."""
    missing = [f for f in REQUIRED_FIELDS if f not in entry or entry[f] in (None, "")]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"

    runner = entry["runner"]
    if runner not in VALID_RUNNERS:
        return f"invalid runner {runner!r} — expected one of {sorted(VALID_RUNNERS)}"

    output = entry["output"]
    if output not in VALID_OUTPUTS:
        return f"invalid output {output!r} — expected one of {sorted(VALID_OUTPUTS)}"

    if runner == "script":
        script = entry.get("script")
        if not script:
            return "runner is 'script' but the 'script' field is missing"
        script_path = analyses_dir / script
        if not script_path.exists():
            return f"script {script!r} does not exist at {script_path}"

    return None


def load_catalog(analyses_dir: Path) -> list[dict]:
    """Discover and parse every .md entry under `analyses_dir`.

    Malformed entries are logged to stderr and skipped. The returned list is
    sorted by `name` for stable output.
    """
    entries: list[dict] = []
    for md in sorted(analyses_dir.glob("*.md")):
        try:
            text = md.read_text()
        except OSError as e:
            warn(f"{md}: could not read ({e})")
            continue
        fm = _parse_front_matter(text)
        if fm is None:
            warn(f"{md}: missing or malformed YAML front-matter — skipped")
            continue
        reason = _validate(fm, md, analyses_dir)
        if reason:
            warn(f"{md}: {reason} — skipped")
            continue
        fm["path"] = str(md.relative_to(analyses_dir.parent))
        entries.append(fm)
    entries.sort(key=lambda e: e["name"])
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List analyses in the sim2real-analyze catalog"
    )
    parser.add_argument(
        "--analyses-dir",
        type=Path,
        default=None,
        help="Path to the analyses/ directory (default: alongside this script)",
    )
    args = parser.parse_args()

    analyses_dir = args.analyses_dir or _default_analyses_dir()
    if not analyses_dir.is_dir():
        print(f"Error: analyses directory not found: {analyses_dir}", file=sys.stderr)
        sys.exit(1)

    entries = load_catalog(analyses_dir)
    print(json.dumps(entries, indent=2))


if __name__ == "__main__":
    main()
