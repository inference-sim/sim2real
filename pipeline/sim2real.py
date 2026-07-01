#!/usr/bin/env python3
"""sim2real top-level CLI.

Subcommands land incrementally across the step-1 epic. This file is
created by PR 1 with only ``translation register``. Subsequent PRs add
``assemble``, ``use``, ``list runs``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Ensure repo root is on sys.path when run as a script (python pipeline/sim2real.py)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.lib import assemble_run as _assemble_run_lib  # noqa: E402
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


def _extract_digest_from_ref(image_ref: str) -> str | None:
    """Return the ``sha256:...`` fragment from an image ref, or None.

    Recognizes only the pinned form ``registry/repo@sha256:HEX`` (64 hex chars).
    Tag-only refs (``registry/repo:v1``) return None; the caller records
    ``image_digest: null`` per design's offline case.
    """
    idx = image_ref.rfind("@sha256:")
    if idx < 0:
        return None
    digest = image_ref[idx + 1:]
    if not digest.startswith("sha256:"):
        return None
    hex_part = digest[len("sha256:"):]
    if len(hex_part) != 64 or not all(c in "0123456789abcdef" for c in hex_part):
        return None
    return digest


def _compute_translation_hash(
    image_digest_or_ref: str,
    config_bytes: bytes,
    algorithm_name: str,
) -> str:
    """SHA-256 hex over canonical JSON of the three BYO inputs.

    Design: ``translation_hash = sha256(image_digest_or_ref || config || name)``.
    Implementation embeds ``sha256(config)`` in a canonical JSON envelope
    (sorted keys, no whitespace) to prevent boundary-shift collisions
    from raw concatenation while preserving determinism. Mirrors
    ``pipeline/lib/slicer.translation_hash``'s canonical-JSON approach.
    """
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    canonical = json.dumps(
        {
            "algorithm_name": algorithm_name,
            "config_sha256": config_sha,
            "image_digest_or_ref": image_digest_or_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_translation_output(
    algorithm_name: str,
    image_ref: str,
    translation_hash: str,
    created_at: str,
) -> dict:
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": "byo",
        "algorithms": [{"name": algorithm_name}],
        "image_ref": image_ref,
        "created_at": created_at,
    }


def _build_registered(
    image_ref: str,
    image_digest: str | None,
    registered_at: str,
) -> dict:
    return {
        "version": 1,
        "image_ref": image_ref,
        "image_digest": image_digest,
        "source": "byo",
        "registered_at": registered_at,
    }


def _register_translation(
    *,
    algorithm_name: str,
    image_ref: str,
    config_path: Path,
    baseline_config_path: Path | None,
    registered_hash: str | None,
    now_iso: str,
) -> tuple[str, str]:
    """Register a BYO translation on disk.

    Returns ``(translation_hash, status)`` where status is either
    ``"created"`` (fresh registration) or ``"idempotent"`` (matching
    translation already existed; no writes performed).

    Raises:
        RuntimeError: ``--registered-hash`` given and does not match computed.
        ValueError: existing translation dir has the same hash but records
            a different algorithm name (corrupted state or collision).
    """
    config_bytes = config_path.read_bytes()
    image_digest = _extract_digest_from_ref(image_ref)
    digest_or_ref = image_digest if image_digest is not None else image_ref
    thash = _compute_translation_hash(digest_or_ref, config_bytes, algorithm_name)

    if registered_hash is not None and registered_hash != thash:
        raise RuntimeError(
            f"--registered-hash mismatch: expected {registered_hash}, got {thash}"
        )

    out_path = layout.translation_output_path(thash)
    if out_path.exists():
        existing = json.loads(out_path.read_text())
        existing_algos = [a.get("name") for a in existing.get("algorithms", [])]
        if algorithm_name not in existing_algos:
            raise ValueError(
                f"algorithm name mismatch: translation {thash} records "
                f"{existing_algos}, refusing to register {algorithm_name!r}"
            )
        # Detect a partial-write left behind by an earlier failed register: the
        # translation_output.json landed but a later write (registered.json or
        # the config overlay) raised. Without this check we would silently
        # short-circuit to "idempotent" and downstream consumers would fail
        # with confusing missing-file errors. Refuse and require manual cleanup.
        missing = []
        if not layout.registered_path(thash).exists():
            missing.append("registered.json")
        if not layout.generated_config_path(thash, algorithm_name).exists():
            missing.append(f"generated/{algorithm_name}/{algorithm_name}_config.yaml")
        if missing:
            raise RuntimeError(
                f"translation {thash} directory is incomplete (missing: "
                f"{', '.join(missing)}); remove {layout.translation_dir(thash)} "
                f"and re-run to recover"
            )
        return thash, "idempotent"

    tdir = layout.translation_dir(thash)
    (tdir / "generated" / algorithm_name).mkdir(parents=True, exist_ok=True)

    out = _build_translation_output(algorithm_name, image_ref, thash, now_iso)
    out_path.write_text(json.dumps(out, indent=2) + "\n")

    reg = _build_registered(image_ref, image_digest, now_iso)
    layout.registered_path(thash).write_text(json.dumps(reg, indent=2) + "\n")

    layout.generated_config_path(thash, algorithm_name).write_bytes(config_bytes)

    if baseline_config_path is not None:
        (tdir / "generated" / "baseline_config.yaml").write_bytes(
            baseline_config_path.read_bytes()
        )

    return thash, "created"


# ── Argparse ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline/sim2real.py",
        description="sim2real top-level CLI.",
    )
    parser.add_argument(
        "--experiment-root",
        metavar="PATH",
        default=None,
        help="Experiment root (default: current working directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    translation = sub.add_parser("translation", help="Manage translations")
    tsub = translation.add_subparsers(dest="subcommand", required=True)

    reg = tsub.add_parser("register", help="Register a BYO (pre-built) translation")
    reg.add_argument(
        "--algorithm",
        required=True,
        type=_validate_algorithm_name,
        help="Algorithm name (a-z, 0-9, hyphens)",
    )
    reg.add_argument(
        "--image",
        required=True,
        metavar="REF",
        help="EPP image reference (e.g. ghcr.io/foo/bar:v1 or ...@sha256:...)",
    )
    reg.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        help="Path to the treatment overlay YAML",
    )
    reg.add_argument(
        "--baseline-config",
        metavar="PATH",
        default=None,
        help="Optional path to a baseline overlay YAML",
    )
    reg.add_argument(
        "--registered-hash",
        metavar="HASH",
        default=None,
        help="Assert the computed translation hash equals this value",
    )

    asm = sub.add_parser(
        "assemble", help="Assemble a run from a registered translation"
    )
    asm.add_argument(
        "--translation",
        required=True,
        metavar="HASH",
        help="translation hash (from `sim2real translation register`)",
    )
    asm.add_argument(
        "--cluster",
        required=True,
        metavar="CLUSTER_ID",
        help="cluster id (matches workspace/clusters/<id>/)",
    )
    asm.add_argument(
        "--run",
        required=True,
        metavar="RUN_NAME",
        help="run name — directory created at workspace/runs/<run>/",
    )
    asm.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing runs/<run>/ directory",
    )

    use = sub.add_parser("use", help="Set the active run in setup_config.json")
    use.add_argument(
        "--run",
        required=True,
        metavar="RUN_NAME",
        help="run name — must correspond to workspace/runs/<RUN_NAME>/",
    )

    lst = sub.add_parser("list", help="List workspace-scoped resources")
    lsub = lst.add_subparsers(dest="subcommand", required=True)
    lsub.add_parser("runs", help="List runs, newest first")

    return parser


def _cmd_translation_register(args) -> int:
    config_path = Path(args.config)
    baseline_config_path = Path(args.baseline_config) if args.baseline_config else None

    if not config_path.exists():
        print(f"error: --config file not found: {config_path}", file=sys.stderr)
        return 2

    # Fail-fast YAML validation. Malformed overlay → error before any writes.
    try:
        yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as e:
        print(f"error: --config is not valid YAML: {e}", file=sys.stderr)
        return 2

    if baseline_config_path is not None:
        if not baseline_config_path.exists():
            print(
                f"error: --baseline-config file not found: {baseline_config_path}",
                file=sys.stderr,
            )
            return 2
        try:
            yaml.safe_load(baseline_config_path.read_text())
        except yaml.YAMLError as e:
            print(f"error: --baseline-config is not valid YAML: {e}", file=sys.stderr)
            return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        thash, status = _register_translation(
            algorithm_name=args.algorithm,
            image_ref=args.image,
            config_path=config_path,
            baseline_config_path=baseline_config_path,
            registered_hash=args.registered_hash,
            now_iso=now_iso,
        )
    except (RuntimeError, ValueError, OSError) as e:
        # OSError covers filesystem faults from mkdir/write_text/write_bytes/
        # read_bytes inside _register_translation (disk full, permission
        # denied, missing parent unwritable). Surfacing as the same
        # `error: ...; return 2` shape used elsewhere keeps failures
        # consistent with the rest of the command.
        print(f"error: {e}", file=sys.stderr)
        return 2

    if status == "idempotent":
        print(
            f"warning: translation {thash} already registered, no-op",
            file=sys.stderr,
        )
    else:
        digest = _extract_digest_from_ref(args.image)
        if digest is None:
            print(
                "warning: image_digest recorded as null "
                "(no @sha256: in --image); hash falls back to using image_ref",
                file=sys.stderr,
            )
    print(f"registered translation {thash}")
    return 0


def _cmd_assemble(args) -> int:
    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )
    manifest_path = exp_root / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = exp_root / "config" / "transfer.yaml"
    if not manifest_path.exists():
        print(
            f"error: transfer.yaml not found under {exp_root}",
            file=sys.stderr,
        )
        return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _assemble_run_lib.assemble_run(
            translation_hash=args.translation,
            cluster_id=args.cluster,
            run_name=args.run,
            experiment_root=exp_root,
            manifest_path=manifest_path,
            force=args.force,
            now_iso=now_iso,
        )
    except _assemble_run_lib.AssembleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for name in getattr(_assemble_run_lib.assemble_run, "skipped_algorithms", []):
        print(
            f"warning: algorithm '{name}' declared in transfer.yaml but not "
            "in translation_output.json — skipped",
            file=sys.stderr,
        )
    print(f"assembled run {args.run}")
    return 0


def _cmd_use(args) -> int:
    run_dir = layout.runs_dir() / args.run
    if not run_dir.is_dir() or not (run_dir / "run_metadata.json").exists():
        print(
            "error: run doesn't exist; try 'sim2real list runs'",
            file=sys.stderr,
        )
        return 2

    cfg_path = layout.setup_config_path()
    existing = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            # Corrupted setup_config.json — treat as empty and rewrite. The
            # `use` command's contract is "flip current_run"; preserving
            # unreadable garbage isn't a goal.
            existing = {}
    existing["current_run"] = args.run
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"current_run → {args.run}")
    return 0


def _read_current_run() -> str:
    """Return current_run from setup_config.json, or "" if absent/unreadable."""
    cfg_path = layout.setup_config_path()
    if not cfg_path.exists():
        return ""
    try:
        return json.loads(cfg_path.read_text()).get("current_run", "") or ""
    except (json.JSONDecodeError, OSError):
        return ""


def _format_assembled(iso: str) -> str:
    """Turn an ISO-8601 UTC timestamp into "YYYY-MM-DD HH:MM" for display.

    Returns "?" if the input isn't parseable — the CLI degrades gracefully
    rather than erroring on one bad row.
    """
    try:
        # datetime.fromisoformat accepts "...Z" only in 3.11+; strip it for parity.
        s = iso[:-1] if iso.endswith("Z") else iso
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return "?"


def _cmd_list_runs(_args) -> int:
    runs_dir = layout.runs_dir()
    if not runs_dir.is_dir():
        print("no runs yet")
        return 0

    entries = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "run_metadata.json"
        if not meta_path.exists():
            continue
        mtime = meta_path.stat().st_mtime
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            meta = None
        entries.append((mtime, run_dir.name, meta))

    if not entries:
        print("no runs yet")
        return 0

    entries.sort(key=lambda e: e[0], reverse=True)
    current = _read_current_run()

    fmt = "{marker} {name:<20} {translation:<14} {cluster:<11} {assembled}"
    print(fmt.format(
        marker=" ", name="RUN_NAME", translation="TRANSLATION",
        cluster="CLUSTER", assembled="ASSEMBLED",
    ))
    for _mtime, name, meta in entries:
        if meta is None:
            translation = "?"
            cluster = "?"
            assembled = "?"
        else:
            thash = meta.get("translation_hash") or ""
            translation = thash[:8] if thash else "?"
            cluster = meta.get("cluster_id") or "?"
            assembled = _format_assembled(meta.get("assembled_at") or "")
        marker = "*" if name == current else " "
        print(fmt.format(
            marker=marker, name=name, translation=translation,
            cluster=cluster, assembled=assembled,
        ))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    layout.set_experiment_root(args.experiment_root)
    if args.command == "translation" and args.subcommand == "register":
        return _cmd_translation_register(args)
    if args.command == "assemble":
        return _cmd_assemble(args)
    if args.command == "use":
        return _cmd_use(args)
    if args.command == "list" and args.subcommand == "runs":
        return _cmd_list_runs(args)
    # argparse's required=True on subparsers means this is unreachable in
    # practice; kept for defensive parity with cluster.py.
    return 1


if __name__ == "__main__":
    sys.exit(main())
