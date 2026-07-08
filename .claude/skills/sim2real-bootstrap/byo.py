#!/usr/bin/env python3
"""BYO branch of ``/sim2real-bootstrap``.

For operators who arrive with pre-built EPP images + per-algorithm scenario
overlays + a full baseline scenario, and NOT a BLIS-format experiment folder.
Copies operator files into canonical experiment-repo locations, emits a valid
``transfer.yaml`` with per-algorithm ``byo: true`` markers, and prints one
copy-pasteable ``sim2real translation register`` command carrying all
algorithms as a batched (N-algorithm) invocation.

BLIS-mode logic lives elsewhere in this skill (SKILL.md Tasks 0-6,
``generate_from_config.py``, ``generate_scenarios.py``); this module is
BYO-only.

See ``docs/epics/step-4/design.md#byo-mode-specification`` for the full spec.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Kubernetes DNS-label subset, 1-20 chars. Matches the constraint enforced by
# pipeline/lib/translation_ref.validate_name(); duplicated here so bootstrap
# can fail fast without importing from the pipeline package.
NAME_REGEX = re.compile(r"^[a-z0-9]([-a-z0-9]{0,18}[a-z0-9])?$")

# Algorithm/baseline names that would collide with framework concepts.
# `baseline` and `baselines` are reserved as *algorithm* names. `baseline`
# is the canonical baseline identifier (see BASELINE_IDENTIFIER below);
# `baselines` names the per-baseline overlay umbrella under
# `translations/<hash>/generated/baselines/` (see pipeline/lib/assemble_run.py).
RESERVED_ANY_ROLE = frozenset({"default", "defaults"})
RESERVED_ALGORITHM_NAMES = RESERVED_ANY_ROLE | frozenset({"baseline", "baselines"})

# The canonical baseline identifier used everywhere in the pipeline. Kept as
# a module-level constant so bootstrap and tests share one source of truth.
BASELINE_IDENTIFIER = "baseline"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class BYOError(Exception):
    """Raised for operator-input errors. Message is surfaced to stderr as-is."""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class BYOArgs:
    """Structured form of the CLI arguments after parsing.

    Kept flat and JSON-serializable so the ``parse_args_dict`` helper can be
    used to compare a structured-args invocation against an NL-derived invocation
    in unit tests (design.md#risks: NL parsing is not CI-tested; the equivalence
    is asserted on the shape of the parsed dict).
    """
    byo: bool
    baseline_path: str | None
    algorithms: list[str]  # order preserved for register command
    algorithm_images: dict[str, str]
    algorithm_configs: dict[str, str]
    scenario: str | None
    force: bool
    non_interactive: bool


@dataclass
class ResolvedInputs:
    """Post-validation view of the operator's inputs.

    Every path here has been resolved and verified to exist; every YAML has
    been safe-loaded and validated as a single-document non-empty mapping.
    Algorithm entries are in the same order the operator provided them.
    """
    exp_root: Path
    scenario: str
    baseline_src: Path
    algorithms: list["ResolvedAlgorithm"] = field(default_factory=list)
    workloads: list[Path] = field(default_factory=list)
    force: bool = False


@dataclass
class ResolvedAlgorithm:
    name: str
    image_ref: str
    config_src: Path


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def _kv_pair(value: str) -> tuple[str, str]:
    """Split ``name=value`` on the first ``=``. Rejects empties."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"{value!r}: expected '<name>=<value>'"
        )
    name, _, val = value.partition("=")
    if not name or not val:
        raise argparse.ArgumentTypeError(
            f"{value!r}: '<name>=<value>' must have non-empty name and value"
        )
    return name, val


def build_parser() -> argparse.ArgumentParser:
    """Argparse spec for the BYO branch. Exported for testing."""
    p = argparse.ArgumentParser(
        prog="sim2real-bootstrap --byo",
        description=(
            "BYO branch of sim2real-bootstrap. Scaffold an experiment repo "
            "from a pre-built EPP image + per-algorithm overlay(s) + a full "
            "baseline scenario. Emits transfer.yaml with byo: true markers "
            "and prints a batched `sim2real translation register` command."
        ),
        add_help=True,
    )
    p.add_argument(
        "--byo",
        action="store_true",
        help="Dispatch to the BYO branch (required; presence-only).",
    )
    p.add_argument(
        "--baseline",
        metavar="<scenario-path>",
        help=(
            "Path to the full baseline scenario YAML. The baseline "
            f"identifier in transfer.yaml is always {BASELINE_IDENTIFIER!r} "
            "(hardcoded — see docs/pipeline/README.md for the rationale)."
        ),
    )
    p.add_argument(
        "--algorithm",
        action="append",
        default=[],
        metavar="<name>",
        help="Declare an algorithm by name. Repeatable; at least one required.",
    )
    p.add_argument(
        "--algorithm-image",
        action="append",
        default=[],
        type=_kv_pair,
        metavar="<name>=<image-ref>",
        help="Map an algorithm name to its container image reference. Repeatable.",
    )
    p.add_argument(
        "--algorithm-config",
        action="append",
        default=[],
        type=_kv_pair,
        metavar="<name>=<overlay-path>",
        help="Map an algorithm name to a partial-YAML overlay path. Repeatable.",
    )
    p.add_argument(
        "--scenario",
        metavar="<name>",
        help="Override the derived transfer.yaml scenario name.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Bypass overwrite confirmation on destinations that already exist.",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Never prompt. Missing required fields are fatal errors. "
            "Auto-enabled when stdin is not a TTY."
        ),
    )
    return p


def parse_args_dict(argv: list[str]) -> dict:
    """Return the parsed CLI as a plain dict.

    Used both by ``parse_args`` (as an intermediate form) and by the
    args-vs-NL equivalence test: an NL description produces a dict of the
    same shape; equality on that dict is what we assert.
    """
    ns = build_parser().parse_args(argv)
    return {
        "byo": bool(ns.byo),
        "baseline": ns.baseline,
        "algorithms": list(ns.algorithm),
        "algorithm_images": dict(ns.algorithm_image),
        "algorithm_configs": dict(ns.algorithm_config),
        "scenario": ns.scenario,
        "force": bool(ns.force),
        "non_interactive": bool(ns.non_interactive),
    }


def parse_args(argv: list[str]) -> BYOArgs:
    d = parse_args_dict(argv)
    return BYOArgs(
        byo=d["byo"],
        baseline_path=d["baseline"],
        algorithms=d["algorithms"],
        algorithm_images=d["algorithm_images"],
        algorithm_configs=d["algorithm_configs"],
        scenario=d["scenario"],
        force=d["force"],
        non_interactive=d["non_interactive"],
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_name(name: str, role: str) -> None:
    """Enforce the DNS-label subset and reserved-name check.

    Only ``role == "algorithm"`` is used today — the baseline identifier is
    hardcoded (see BASELINE_IDENTIFIER). The role parameter is retained for
    error-message specificity in case a future call site needs a different
    role label.
    """
    if not NAME_REGEX.match(name):
        raise BYOError(
            f"{role} name {name!r} is invalid: must match "
            f"[a-z0-9]([-a-z0-9]{{0,18}}[a-z0-9])? (1-20 chars, "
            "lowercase alphanumeric with internal hyphens)"
        )
    if role == "algorithm" and name in RESERVED_ALGORITHM_NAMES:
        raise BYOError(
            f"algorithm name {name!r} is reserved "
            f"(reserved: {', '.join(sorted(RESERVED_ALGORITHM_NAMES))})"
        )


def _validate_image_ref(ref: str, algo_name: str) -> None:
    """Minimal image-ref sanity. Final validation is register's job."""
    if not ref:
        raise BYOError(f"algorithm {algo_name!r}: image ref is empty")
    if any(c.isspace() for c in ref):
        raise BYOError(
            f"algorithm {algo_name!r}: image ref {ref!r} contains whitespace"
        )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in ref):
        raise BYOError(
            f"algorithm {algo_name!r}: image ref {ref!r} contains control chars"
        )


def _validate_yaml_file(path: Path, role: str) -> None:
    """Load once, enforce: single-document, non-empty, mapping root.

    Path traversal / existence checks live upstream; this function assumes
    ``path`` was resolved and exists.
    """
    try:
        text = path.read_text()
    except OSError as exc:
        raise BYOError(f"{role} at {path}: cannot read: {exc}") from exc
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise BYOError(f"{role} at {path}: YAML parse failed: {exc}") from exc
    if len(docs) == 0:
        raise BYOError(f"{role} at {path}: empty document")
    if len(docs) > 1:
        raise BYOError(
            f"{role} at {path}: multi-document YAML not supported "
            f"(found {len(docs)} documents)"
        )
    doc = docs[0]
    if doc is None:
        raise BYOError(f"{role} at {path}: document is empty/null")
    if not isinstance(doc, dict):
        raise BYOError(
            f"{role} at {path}: top-level must be a mapping, "
            f"got {type(doc).__name__}"
        )


def _resolve_inside(path: Path, exp_root: Path, role: str) -> Path:
    """Resolve ``path``; require existence + regular-file + inside/outside checks.

    ``path`` is a *source* path. Bootstrap accepts sources outside
    ``<exp-root>`` (operators supply paths from anywhere), but symlinks are
    resolved and the final target must be a regular file. Enforced separately
    from ``_check_dest_inside``, which enforces destination containment.
    """
    if not path.exists():
        raise BYOError(f"{role}: path {path} does not exist")
    if path.is_symlink() or (path.parent / path.name).is_symlink():
        # Follow the symlink once via resolve(strict=True); require regular file.
        pass
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BYOError(
            f"{role}: cannot resolve path {path}: {exc}"
        ) from exc
    if not resolved.is_file():
        raise BYOError(
            f"{role}: {path} (resolved {resolved}) is not a regular file"
        )
    return resolved


def _check_dest_inside(dst: Path, exp_root: Path) -> Path:
    """Return the resolved destination, enforcing it lies inside ``exp_root``.

    Uses ``os.path.realpath`` on the destination parent (which exists) plus
    the basename, so a not-yet-existing destination is still safely
    validated against symlink games in its parent chain.
    """
    dst_parent_resolved = Path(os.path.realpath(dst.parent))
    exp_resolved = Path(os.path.realpath(exp_root))
    try:
        dst_parent_resolved.relative_to(exp_resolved)
    except ValueError:
        raise BYOError(
            f"destination {dst} resolves to {dst_parent_resolved / dst.name}, "
            f"outside experiment root {exp_resolved}"
        )
    return dst_parent_resolved / dst.name


# ---------------------------------------------------------------------------
# Scenario name derivation
# ---------------------------------------------------------------------------

_SCENARIO_SANITIZE = re.compile(r"[^a-z0-9-]+")
_SCENARIO_COLLAPSE = re.compile(r"-+")


def normalize_scenario(raw: str) -> str:
    """Normalize a derived scenario string per design.md:118-125.

    lowercase → non-[a-z0-9-] replaced with '-' → runs of '-' collapsed →
    leading/trailing '-' stripped → truncated to 40 chars.
    """
    s = raw.strip().lower()
    s = _SCENARIO_SANITIZE.sub("-", s)
    s = _SCENARIO_COLLAPSE.sub("-", s)
    s = s.strip("-")
    return s[:40]


def derive_scenario_name(exp_root: Path, override: str | None) -> str:
    """Derive scenario name for transfer.yaml.

    Order: --scenario override → README.md first-header title → exp_root basename.
    Result is normalized and required to be non-empty.
    """
    if override is not None:
        candidate = override
    else:
        readme = exp_root / "README.md"
        candidate = None
        if readme.exists():
            for line in readme.read_text().splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                # Only the FIRST non-empty line is considered per spec.
                if stripped.startswith("# "):
                    candidate = stripped[2:].strip()
                break
        if not candidate:
            candidate = exp_root.name
    normalized = normalize_scenario(candidate)
    if not normalized:
        raise BYOError(
            "cannot derive scenario name — override with --scenario "
            f"(source: {candidate!r})"
        )
    return normalized


# ---------------------------------------------------------------------------
# Workload enumeration
# ---------------------------------------------------------------------------

def enumerate_workloads(exp_root: Path) -> list[Path]:
    """Non-recursive glob of ``<exp-root>/workloads/*.yaml``.

    Returns absolute paths sorted lexicographically. Empty result (missing
    dir or no matches) is a fatal error naming the directory.
    """
    workloads_dir = exp_root / "workloads"
    if not workloads_dir.is_dir():
        raise BYOError(
            f"workloads directory missing: {workloads_dir}"
        )
    matches: list[Path] = []
    for entry in workloads_dir.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.suffix != ".yaml":
            continue
        # Resolve to enforce the "symlinks stay inside workloads/" rule.
        try:
            resolved = entry.resolve(strict=True)
        except (OSError, RuntimeError):
            raise BYOError(
                f"workload {entry}: cannot resolve (broken symlink?)"
            )
        try:
            resolved.relative_to(Path(os.path.realpath(workloads_dir)))
        except ValueError:
            raise BYOError(
                f"workload {entry} resolves outside {workloads_dir}"
            )
        if not resolved.is_file():
            raise BYOError(f"workload {entry} is not a regular file")
        matches.append(entry)
    if not matches:
        raise BYOError(
            f"no workload files found under {workloads_dir}/*.yaml"
        )
    matches.sort(key=lambda p: p.name)
    return matches


# ---------------------------------------------------------------------------
# Copy operations
# ---------------------------------------------------------------------------

def _atomic_copy_file(src: Path, dst: Path) -> None:
    """Copy src → dst by writing to a sibling temp file then renaming.

    Never leaves a half-written destination. Preserves default file mode
    (0644 subject to umask); we do NOT copy source mode — the operator's
    overlay file might be 0600 on their disk but we want the experiment
    repo files to be readable by tooling.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=dst.name + ".", suffix=".tmp", dir=str(dst.parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            with src.open("rb") as sf:
                shutil.copyfileobj(sf, f)
        os.replace(tmp_name, str(dst))
    except Exception:
        # Best-effort cleanup; the temp file's name is randomized so no
        # existing file at ``dst`` is at risk.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _prompt_overwrite(dst: Path) -> bool:
    """Interactive confirmation for an existing destination.

    Returns True to proceed with overwrite. Prompts on stderr, reads stdin.
    Called only when stdin is a TTY (or the caller has explicitly opted in);
    non-interactive dispatch takes a different branch entirely.
    """
    print(f"destination exists: {dst}", file=sys.stderr)
    print("overwrite? [y/N]: ", file=sys.stderr, end="", flush=True)
    resp = sys.stdin.readline().strip().lower()
    return resp in ("y", "yes")


def _decide_dest(dst: Path, force: bool, non_interactive: bool) -> None:
    """Raise BYOError if destination exists and we shouldn't overwrite."""
    if not dst.exists():
        return
    if force:
        return
    if non_interactive:
        raise BYOError(
            f"destination {dst} exists; refusing overwrite in non-interactive "
            "mode (pass --force to override)"
        )
    if not _prompt_overwrite(dst):
        raise BYOError(f"aborted: destination {dst} exists")


def copy_operator_files(
    inputs: ResolvedInputs, non_interactive: bool
) -> tuple[Path, list[Path]]:
    """Copy baseline + per-algorithm overlays into the experiment repo.

    Returns (baseline_dst, [algo_config_dsts]). All destinations are inside
    ``inputs.exp_root``; every write is atomic; existing destinations honor
    ``inputs.force`` and ``non_interactive``.
    """
    # baseline
    baseline_dst = _check_dest_inside(
        inputs.exp_root / "baselines" / f"{BASELINE_IDENTIFIER}.yaml",
        inputs.exp_root,
    )
    _decide_dest(baseline_dst, inputs.force, non_interactive)
    _atomic_copy_file(inputs.baseline_src, baseline_dst)

    # per-algorithm overlays
    algo_dsts: list[Path] = []
    for algo in inputs.algorithms:
        algo_dst = _check_dest_inside(
            inputs.exp_root / "algorithms" / algo.name / f"{algo.name}_config.yaml",
            inputs.exp_root,
        )
        _decide_dest(algo_dst, inputs.force, non_interactive)
        _atomic_copy_file(algo.config_src, algo_dst)
        algo_dsts.append(algo_dst)

    return baseline_dst, algo_dsts


def copy_framework_defaults(
    skill_dir: Path, exp_root: Path, force: bool, non_interactive: bool
) -> list[str]:
    """Copy ``<skill>/templates/defaults/*.yaml`` → ``<exp>/baselines/defaults/``.

    Returns the sorted list of filename stems, used to populate
    ``defaults.disable`` in transfer.yaml. Duplicate stems (e.g. foo.yaml +
    foo.yml — cannot happen today because we only copy .yaml, but the guard
    is cheap) are a fatal error.
    """
    src_dir = skill_dir / "templates" / "defaults"
    if not src_dir.is_dir():
        raise BYOError(
            f"framework defaults directory missing: {src_dir} "
            "(is this skill's templates/defaults/ populated?)"
        )
    dst_dir = exp_root / "baselines" / "defaults"
    stems: list[str] = []
    seen_stems: set[str] = set()
    for src in sorted(src_dir.iterdir(), key=lambda p: p.name):
        if src.name.startswith("."):
            continue
        if src.suffix != ".yaml":
            continue
        stem = src.stem
        if stem in seen_stems:
            raise BYOError(
                f"duplicate default fragment stem {stem!r} in {src_dir}"
            )
        seen_stems.add(stem)
        dst = _check_dest_inside(dst_dir / src.name, exp_root)
        _decide_dest(dst, force, non_interactive)
        _atomic_copy_file(src, dst)
        stems.append(stem)
    stems.sort()
    return stems


# ---------------------------------------------------------------------------
# transfer.yaml emission
# ---------------------------------------------------------------------------

def build_transfer_yaml(
    *,
    scenario: str,
    algorithms: list[ResolvedAlgorithm],
    workloads: Iterable[Path],
    exp_root: Path,
    defaults_stems: list[str],
) -> dict:
    """Assemble the transfer.yaml body per design.md#emitted-transfer-yaml-shape.

    - No ``component:`` key (all algorithms are BYO).
    - Every ``algorithms[]`` entry has ``byo: true``, no ``source``.
    - ``workloads`` paths are expressed relative to ``exp_root``.
    - ``defaults.disable`` matches the stems of files copied to
      ``<exp>/baselines/defaults/``.
    - The baseline identifier is always ``BASELINE_IDENTIFIER`` and the file
      lives at ``baselines/baseline.yaml`` (issue #544).
    """
    return {
        "kind": "sim2real-transfer",
        "version": 3,
        "scenario": scenario,
        "baselines": [
            {
                "name": BASELINE_IDENTIFIER,
                "scenario": f"baselines/{BASELINE_IDENTIFIER}.yaml",
            },
        ],
        "algorithms": [
            {"name": a.name, "defaults": BASELINE_IDENTIFIER, "byo": True}
            for a in algorithms
        ],
        "workloads": [
            str(w.relative_to(exp_root)) for w in workloads
        ],
        "defaults": {"disable": defaults_stems},
        "context": {
            "files": [],
            "text": (
                "Bring-your-own translation. Images and per-algorithm overlays "
                "supplied externally; no BLIS source in this experiment repo."
            ),
        },
    }


def _inject_available_fragments_comment(yaml_text: str, available_stems: list[str]) -> str:
    """Insert an ``# Available fragments`` comment block after ``defaults.disable``.

    PyYAML's ``safe_dump`` cannot emit comments, but the operator-facing
    ``transfer.yaml`` should document which filename stems live in
    ``baselines/defaults/`` so a reader knows what values are legal in the
    ``defaults.disable`` list. This helper post-processes the serialized YAML
    text: it locates the ``defaults.disable`` block and inserts one
    ``# Available fragments…`` comment plus one ``#   - <stem>`` per fragment
    immediately after the list.

    Idempotent-safe against re-invocation only in the sense that the caller
    controls ``yaml_text`` — we do not deduplicate an existing comment block.
    Callers pass freshly-serialized YAML.
    """
    if not available_stems:
        return yaml_text
    comment_lines = [
        "  # Available fragments (filename stems in baselines/defaults/):",
    ] + [f"  #   - {stem}" for stem in sorted(available_stems)]

    lines = yaml_text.split("\n")
    # Find the ``disable:`` line under a top-level ``defaults:`` key. YAML
    # emitted by safe_dump with sort_keys=False + default_flow_style=False
    # produces predictable indentation: top-level keys at column 0, nested
    # keys at 2-space indent.
    disable_idx = -1
    in_defaults = False
    for i, line in enumerate(lines):
        if line == "defaults:" or line.startswith("defaults:"):
            in_defaults = True
            continue
        if in_defaults:
            if line.startswith("  disable:"):
                disable_idx = i
                break
            if line and not line.startswith(" "):
                # Left the defaults block before finding disable — nothing to do.
                return yaml_text
    if disable_idx < 0:
        return yaml_text

    # Walk forward from disable_idx: the list ends at the first line that is
    # neither blank nor indented with ``  -`` / ``  `` at 2+ spaces where the
    # first non-space char is ``-``.
    list_end = disable_idx
    for j in range(disable_idx + 1, len(lines)):
        line = lines[j]
        if line.startswith("  - "):
            list_end = j
            continue
        # Any other line — top-level key, blank line, or unindented content —
        # ends the list.
        break
    else:
        list_end = len(lines) - 1

    lines[list_end + 1:list_end + 1] = comment_lines
    return "\n".join(lines)


def write_transfer_yaml(exp_root: Path, doc: dict, force: bool, non_interactive: bool) -> Path:
    """Serialize doc and write atomically to ``<exp-root>/transfer.yaml``."""
    dst = _check_dest_inside(exp_root / "transfer.yaml", exp_root)
    _decide_dest(dst, force, non_interactive)
    text = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)
    available_stems = list(doc.get("defaults", {}).get("disable") or [])
    text = _inject_available_fragments_comment(text, available_stems)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=dst.name + ".", suffix=".tmp", dir=str(dst.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp_name, str(dst))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dst


# ---------------------------------------------------------------------------
# Register command emission
# ---------------------------------------------------------------------------

def emit_register_command(
    exp_root_abs: Path, algorithms: list[ResolvedAlgorithm]
) -> str:
    """Emit the batched register command, shlex-quoted.

    Shape (matches design.md#register-command-emission):

        cd '<abs-exp-root>'
        sim2real translation register \\
            --algorithm '<name1>=<image1>@algorithms/<name1>/<name1>_config.yaml' \\
            --algorithm '<name2>=<image2>@algorithms/<name2>/<name2>_config.yaml'

    Every path and every triple is quoted with ``shlex.quote``; the block
    round-trips cleanly through ``shlex.split``.
    """
    if not algorithms:
        raise BYOError("cannot emit register command: no algorithms")
    lines = [
        f"cd {shlex.quote(str(exp_root_abs))}",
        "sim2real translation register \\",
    ]
    for i, algo in enumerate(algorithms):
        overlay_rel = f"algorithms/{algo.name}/{algo.name}_config.yaml"
        triple = f"{algo.name}={algo.image_ref}@{overlay_rel}"
        suffix = " \\" if i < len(algorithms) - 1 else ""
        lines.append(f"    --algorithm {shlex.quote(triple)}{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _collect(args: BYOArgs, exp_root: Path) -> ResolvedInputs:
    """Enforce required fields, validate every input file, resolve paths.

    Argparse is handled upstream; this function operates on an already-parsed
    ``BYOArgs``. Non-interactive mode is decided by the caller (based on
    ``stdin.isatty()`` and ``--non-interactive``) and passed through to the
    copy stage separately.
    """
    if not args.byo:
        # This entry point is BYO-only; SKILL.md handles dispatch. Guarding
        # here catches direct-invocation mistakes with a clear message.
        raise BYOError("--byo required (this entry point is BYO-only)")

    # baseline
    if not args.baseline_path:
        raise BYOError("missing required field: --baseline <scenario-path>")
    baseline_src = _resolve_inside(
        Path(args.baseline_path), exp_root, role="--baseline"
    )
    _validate_yaml_file(baseline_src, role="baseline")

    # algorithms
    if not args.algorithms:
        raise BYOError("missing required field: at least one --algorithm <name>")
    if len(args.algorithms) != len(set(args.algorithms)):
        seen: set[str] = set()
        dupes: set[str] = set()
        for n in args.algorithms:
            if n in seen:
                dupes.add(n)
            seen.add(n)
        raise BYOError(
            f"duplicate --algorithm names: {', '.join(sorted(dupes))}"
        )
    for name in args.algorithms:
        _validate_name(name, role="algorithm")

    # image + config per algorithm — no missing, no extras, no cross-name refs
    declared = set(args.algorithms)
    extra_images = set(args.algorithm_images) - declared
    if extra_images:
        raise BYOError(
            f"--algorithm-image name(s) not declared with --algorithm: "
            f"{', '.join(sorted(extra_images))}"
        )
    extra_configs = set(args.algorithm_configs) - declared
    if extra_configs:
        raise BYOError(
            f"--algorithm-config name(s) not declared with --algorithm: "
            f"{', '.join(sorted(extra_configs))}"
        )
    resolved_algos: list[ResolvedAlgorithm] = []
    for name in args.algorithms:
        image = args.algorithm_images.get(name)
        if not image:
            raise BYOError(
                f"algorithm {name!r}: missing --algorithm-image {name}=..."
            )
        _validate_image_ref(image, algo_name=name)
        cfg = args.algorithm_configs.get(name)
        if not cfg:
            raise BYOError(
                f"algorithm {name!r}: missing --algorithm-config {name}=..."
            )
        cfg_src = _resolve_inside(
            Path(cfg), exp_root, role=f"--algorithm-config {name!r}"
        )
        _validate_yaml_file(cfg_src, role=f"overlay for algorithm {name!r}")
        resolved_algos.append(
            ResolvedAlgorithm(name=name, image_ref=image, config_src=cfg_src)
        )

    # workloads (fail-fast; nothing to copy but enumeration must succeed)
    workloads = enumerate_workloads(exp_root)

    # scenario derivation
    scenario = derive_scenario_name(exp_root, args.scenario)

    return ResolvedInputs(
        exp_root=exp_root,
        scenario=scenario,
        baseline_src=baseline_src,
        algorithms=resolved_algos,
        workloads=workloads,
        force=args.force,
    )


def run_byo(
    argv: list[str],
    exp_root: Path,
    skill_dir: Path,
    stdin_isatty: bool = True,
) -> tuple[int, str]:
    """Run the full BYO branch. Returns (exit_code, register_command_or_message).

    - ``exit_code == 0`` → success; second field is the emitted register
      command block, suitable for printing on stdout.
    - ``exit_code == 2`` → any BYO validation error; second field is the
      error message (already printed to stderr by ``main()``).
    """
    try:
        exp_root_resolved = Path(os.path.realpath(exp_root))
        args = parse_args(argv)
        non_interactive = args.non_interactive or (not stdin_isatty)
        inputs = _collect(args, exp_root_resolved)
        copy_operator_files(inputs, non_interactive=non_interactive)
        defaults_stems = copy_framework_defaults(
            skill_dir, inputs.exp_root, inputs.force, non_interactive
        )
        doc = build_transfer_yaml(
            scenario=inputs.scenario,
            algorithms=inputs.algorithms,
            workloads=inputs.workloads,
            exp_root=inputs.exp_root,
            defaults_stems=defaults_stems,
        )
        write_transfer_yaml(inputs.exp_root, doc, inputs.force, non_interactive)
        register_cmd = emit_register_command(inputs.exp_root, inputs.algorithms)
        return 0, register_cmd
    except BYOError as exc:
        return 2, str(exc)
    except SystemExit as exc:
        # argparse exits on --help or parse errors; convert to our contract.
        code = exc.code if isinstance(exc.code, int) else 2
        return code, ""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    exp_root = Path.cwd()
    skill_dir = Path(__file__).resolve().parent
    exit_code, message = run_byo(
        argv, exp_root, skill_dir, stdin_isatty=sys.stdin.isatty()
    )
    if exit_code == 0 and message:
        print("BYO bootstrap complete. Next: register all algorithms in one call.")
        print()
        print(message)
        print()
        print("The command prints one translation hash. Then:")
        print()
        print("    sim2real assemble --translation <hash> --cluster <cluster_id> --run <run-name>")
    elif exit_code != 0 and message:
        print(f"error: {message}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
