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


def _validate_algorithm_name(name: str) -> str:
    """Argparse ``type=`` wrapper around ``translation_ref.validate_name``.

    Widened in step-2 PR 2: accepts uppercase, dot, and underscore per
    the shared regex ``^[A-Za-z0-9][A-Za-z0-9._-]*$``. Kept as a thin
    wrapper so CLI errors surface as ``argparse.ArgumentTypeError``.
    """
    from pipeline.lib import translation_ref
    try:
        return translation_ref.validate_name(name)
    except translation_ref.ValidationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


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


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write ``data`` as pretty JSON to ``path`` via a tempfile + os.replace.

    POSIX-atomic on same-filesystem writes. Prevents readers from
    observing a half-written file during ``--force`` alias reassignment.
    """
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp-", suffix=".json"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except Exception:
        # Cleanup on failure; ignore rmtree race with concurrent GC.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _clear_alias_on(other_hash: str) -> None:
    """Rewrite ``translations/<other_hash>/translation_output.json`` with ``alias=null``.

    Used by ``_register_translation`` under ``--force`` to keep aliases
    globally unique. If the target file cannot be read (missing, corrupt),
    raise — the collision-detection path already confirmed it exists via
    ``find_by_alias``; a read failure here indicates a race the operator
    should resolve manually.
    """
    from pipeline.lib import translation_ref
    other_path = layout.translation_output_path(other_hash)
    data = translation_ref.read_translation_output(other_path)
    data["alias"] = None
    _atomic_write_json(other_path, data)


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
    *,
    algorithm_name: str,
    image_ref: str,
    image_digest: str | None,
    config_path: str,
    translation_hash: str,
    source: str,
    alias: str | None,
    created_at: str,
) -> dict:
    """Build the ``translation_output.json`` body for step-2 schema.

    Single-algorithm shape (BYO register in this PR). ``image_ref`` and
    ``image_digest`` live per-algo. ``source_path``/``source_sha256`` are
    ``None`` for BYO (populated by the skill-driven ``translate`` in PR 3).
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": source,
        "alias": alias,
        "algorithms": [
            {
                "name": algorithm_name,
                "source_path": None,
                "source_sha256": None,
                "config_path": config_path,
                "image_ref": image_ref,
                "image_digest": image_digest,
            }
        ],
        "created_at": created_at,
    }


def _build_translate_output(
    *,
    translation_hash: str,
    scenario: str,
    algorithms: list[dict],
    now_iso: str,
) -> dict:
    """Build ``translation_output.json`` body for skill-driven translate.

    Multi-algorithm shape (design §Schemas). Each ``algorithms[i]`` carries
    ``source_path``/``source_sha256`` (populated by ``translate``) and
    ``image_ref``/``image_digest`` == ``None`` — ``sim2real build`` (PR 5)
    fills the image fields in later. ``config_path`` is ``None`` for the
    skill-driven producer; BYO ``translation register`` writes it instead.
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": "skill",
        "alias": scenario,
        "algorithms": [
            {
                "name": a["name"],
                "source_path": a["source_path"],
                "source_sha256": a["source_sha256"],
                "config_path": None,
                "image_ref": None,
                "image_digest": None,
            }
            for a in algorithms
        ],
        "created_at": now_iso,
    }


def _build_skill_input(
    *,
    translation_hash: str,
    experiment_root: Path,
    translations_dir: Path,
    scenario: str,
    baseline: dict | None,
    algorithms: list[dict],
    context: dict,
) -> dict:
    """Build ``skill_input.json`` body per design §Schemas.

    Absolute paths at the top level so the skill doesn't have to compose
    them; per-algorithm ``output_dir`` and ``config_output_path`` are
    relative to ``translations_dir``. ``source_path`` is relative to
    ``experiment_root``. ``baseline`` is ``None`` when the manifest has no
    baseline overlay.
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "experiment_root": str(experiment_root),
        "translations_dir": str(translations_dir),
        "scenario": scenario,
        "baseline": baseline,
        "algorithms": [
            {
                "name": a["name"],
                "source_path": a["source_path"],
                "source_sha256": a["source_sha256"],
                "output_dir": f"generated/{a['name']}",
                "config_output_path": f"generated/{a['name']}/{a['name']}_config.yaml",
                "notes": a.get("notes", ""),
            }
            for a in algorithms
        ],
        "context": context,
    }


def _translate_state(
    translation_hash: str, expected_algorithm_names: list[str]
) -> tuple[str, list[str]]:
    """Classify on-disk state of ``translations/<hash>/`` for the state machine.

    Returns ``(state, missing_names)`` where ``state`` is one of
    ``"nothing"``, ``"partial"``, ``"complete"`` per design's PR-3 state
    table. ``missing_names`` is populated only for ``"partial"`` — the list
    of algorithm names whose ``generated/<algo>/<algo>_output.json`` is not
    on disk. A ``translation_output.json`` recording a divergent algorithm
    set surfaces as ``"partial"`` (the currently-expected algorithms are
    the missing ones).
    """
    tdir = layout.translation_dir(translation_hash)
    if not tdir.exists():
        return "nothing", []
    tout = layout.translation_output_path(translation_hash)
    if not tout.exists():
        return "partial", list(expected_algorithm_names)
    missing = [
        name for name in expected_algorithm_names
        if not (tdir / "generated" / name / f"{name}_output.json").exists()
    ]
    if missing:
        return "partial", missing
    return "complete", []


def _translate_delete_dir(translation_hash: str) -> None:
    """Recursively remove ``translations/<hash>/``. No-op if absent."""
    import shutil
    tdir = layout.translation_dir(translation_hash)
    if tdir.exists():
        shutil.rmtree(tdir)


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
    force: bool = False,
) -> tuple[str, str]:
    """Register a BYO translation on disk.

    Returns ``(translation_hash, status)`` where status is either
    ``"created"`` (fresh registration) or ``"idempotent"`` (matching
    translation already existed; no writes performed).

    Raises:
        RuntimeError: ``--registered-hash`` given and does not match
            computed; OR alias collision on a different hash without
            ``force``.
        ValueError: existing translation dir has the same hash but records
            a different algorithm name (corrupted state or collision).
    """
    from pipeline.lib import translation_ref
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
        existing = translation_ref.read_translation_output(out_path)
        existing_algos = [a.get("name") for a in existing.get("algorithms", [])]
        if algorithm_name not in existing_algos:
            raise ValueError(
                f"algorithm name mismatch: translation {thash} records "
                f"{existing_algos}, refusing to register {algorithm_name!r}"
            )
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

    # Alias collision — detect BEFORE creating any new files. Only a
    # different-hash collision matters; same-hash "collision" fell out
    # into the idempotent path above.
    other_hash = translation_ref.find_by_alias(
        algorithm_name, layout.translations_dir()
    )
    if other_hash is not None and other_hash != thash:
        if not force:
            raise RuntimeError(
                f"alias {algorithm_name!r} already assigned to translation "
                f"{other_hash}; pass --force to reassign"
            )
        _clear_alias_on(other_hash)

    tdir = layout.translation_dir(thash)
    (tdir / "generated" / algorithm_name).mkdir(parents=True, exist_ok=True)

    out = _build_translation_output(
        algorithm_name=algorithm_name,
        image_ref=image_ref,
        image_digest=image_digest,
        config_path=f"generated/{algorithm_name}/{algorithm_name}_config.yaml",
        translation_hash=thash,
        source="byo",
        alias=algorithm_name,
        created_at=now_iso,
    )
    _atomic_write_json(out_path, out)

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
        help=(
            "Algorithm name; also written as the translation's alias. "
            "Must match [A-Za-z0-9][A-Za-z0-9._-]*, max 128 chars; "
            "'.' and '..' are rejected."
        ),
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
    reg.add_argument(
        "--force",
        action="store_true",
        help="Reassign the alias (--algorithm) from a previous translation.",
    )

    asm = sub.add_parser(
        "assemble", help="Assemble a run from a registered translation"
    )
    asm.add_argument(
        "--translation",
        required=True,
        metavar="REF",
        help="alias, hash prefix, or full translation hash",
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
    lsub.add_parser("translations", help="List translations, newest first")

    trans = sub.add_parser(
        "translate",
        help="Skill-driven translation: write checkpoint files or validate resume",
    )
    mode = trans.add_mutually_exclusive_group()
    mode.add_argument(
        "--force",
        action="store_true",
        help=(
            "Delete and recreate the translation dir; the operator must "
            "re-run '/sim2real-translate' after."
        ),
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Validate that '/sim2real-translate' produced all declared "
            "algorithm outputs; never mutates the translation dir."
        ),
    )

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
            force=args.force,
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


def _cmd_translate(args) -> int:
    """Skill-driven translation state machine.

    Nine-cell state × command table per
    ``docs/epics/step-2/design.md#pr-3--sim2real-translate-command``:
      • nothing / plain → write checkpoint, exit 0
      • nothing / resume → error "no translation to resume", exit 2
      • nothing / force → same as plain
      • partial / plain → error "incomplete", exit 2 (never mutates)
      • partial / resume → error "missing outputs for: ...", exit 2
      • partial / force → delete + recreate as if nothing
      • complete / plain → "already complete", exit 0 (idempotent)
      • complete / resume → same as complete / plain
      • complete / force → delete + recreate; user re-runs the skill
    """
    from pipeline.lib import manifest as _manifest, slicer, translation_ref

    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )
    manifest_path = exp_root / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = exp_root / "config" / "transfer.yaml"
    if not manifest_path.exists():
        print(f"error: transfer.yaml not found under {exp_root}", file=sys.stderr)
        return 2

    try:
        manifest = _manifest.load_manifest(manifest_path)
    except _manifest.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    scenario = manifest.get("scenario")
    if not scenario:
        print("error: transfer.yaml missing required 'scenario' field", file=sys.stderr)
        return 2

    # Scenario doubles as the translation's alias (design §Alias/algorithm-name
    # validation). Reject invalid names before touching disk.
    try:
        translation_ref.validate_name(scenario)
    except translation_ref.ValidationError as exc:
        print(f"error: invalid scenario name (used as alias): {exc}", file=sys.stderr)
        return 2

    declared_algos = manifest.get("algorithms") or []
    if not declared_algos:
        print("error: transfer.yaml has no algorithms declared", file=sys.stderr)
        return 2
    for algo in declared_algos:
        try:
            translation_ref.validate_name(algo.get("name", ""))
        except translation_ref.ValidationError as exc:
            print(f"error: invalid algorithm name: {exc}", file=sys.stderr)
            return 2

    try:
        thash = slicer.translation_hash_with_sources(manifest, exp_root)
    except (_assemble_run_lib.AssembleError, OSError) as exc:
        # slicer.translation_hash_with_sources raises AssembleError when an
        # algorithm's ``source`` file is missing; OSError covers unreadable
        # source files (permissions, etc). Both surface as actionable
        # per-file errors rather than crashing the CLI.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    expected_names = [a["name"] for a in declared_algos]
    state, missing = _translate_state(thash, expected_names)

    if args.resume:
        if state == "nothing":
            print(
                f"error: no translation to resume for hash {thash} — "
                f"run 'sim2real translate' first",
                file=sys.stderr,
            )
            return 2
        if state == "partial":
            print(
                f"error: missing outputs for: {', '.join(missing)} — "
                f"run '/sim2real-translate' first",
                file=sys.stderr,
            )
            return 2
        print(f"translation {thash} already complete — run 'sim2real build' next")
        return 0

    if args.force:
        _translate_delete_dir(thash)
        return _translate_write_checkpoint(
            thash=thash,
            scenario=scenario,
            manifest=manifest,
            exp_root=exp_root,
            declared_algos=declared_algos,
        )

    if state == "partial":
        print(
            f"error: translation {thash} incomplete — "
            f"run '/sim2real-translate' then 'sim2real translate --resume'",
            file=sys.stderr,
        )
        return 2
    if state == "complete":
        print(f"translation {thash} already complete — run 'sim2real build' next")
        return 0
    return _translate_write_checkpoint(
        thash=thash,
        scenario=scenario,
        manifest=manifest,
        exp_root=exp_root,
        declared_algos=declared_algos,
    )


def _translate_write_checkpoint(
    *,
    thash: str,
    scenario: str,
    manifest: dict,
    exp_root: Path,
    declared_algos: list[dict],
) -> int:
    """Write both checkpoint files and print the operator hint.

    Caller has ensured on-disk state is ``"nothing"`` (either originally
    or after ``_translate_delete_dir``). Writes go through
    ``_atomic_write_json`` so an interruption never leaves a
    half-written file visible.
    """
    tdir = layout.translation_dir(thash)
    tdir.mkdir(parents=True, exist_ok=True)

    algo_records = []
    for algo in declared_algos:
        name = algo["name"]
        source_rel = algo.get("source")
        source_sha = None
        if source_rel:
            source_sha = hashlib.sha256(
                (exp_root / source_rel).read_bytes()
            ).hexdigest()
        algo_records.append({
            "name": name,
            "source_path": source_rel,
            "source_sha256": source_sha,
            "notes": algo.get("notes", ""),
        })

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tout = _build_translate_output(
        translation_hash=thash,
        scenario=scenario,
        algorithms=algo_records,
        now_iso=now_iso,
    )
    _atomic_write_json(layout.translation_output_path(thash), tout)

    baseline_manifest = manifest.get("baseline") or None
    if baseline_manifest:
        skin_baseline: dict | None = {
            "config_path": baseline_manifest.get("config"),
            "generated_overlay_path": "generated/baseline_config.yaml",
        }
    else:
        skin_baseline = None

    # Bridge manifest ``context.files`` → skill_input ``context.file_paths``
    # (design §Schemas §skill_input.json). Manifest keys are pinned by
    # ``manifest.py``; the skill_input schema is pinned by the design.
    manifest_context = manifest.get("context") or {}
    context = {
        "text": manifest_context.get("text", "") or "",
        "file_paths": list(manifest_context.get("files") or []),
    }
    skin = _build_skill_input(
        translation_hash=thash,
        experiment_root=exp_root,
        translations_dir=layout.translations_dir(),
        scenario=scenario,
        baseline=skin_baseline,
        algorithms=algo_records,
        context=context,
    )
    _atomic_write_json(tdir / "skill_input.json", skin)

    print(
        f"translation {thash} checkpoint written — "
        f"run '/sim2real-translate' then 'sim2real translate --resume'"
    )
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

    from pipeline.lib import translation_ref
    try:
        translation_hash = translation_ref.resolve_translation_ref(args.translation)
    except translation_ref.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        _assemble_run_lib.assemble_run(
            translation_hash=translation_hash,
            translation_ref=args.translation,
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
    for name in getattr(_assemble_run_lib.assemble_run, "missing_submodules", []):
        print(
            f"warning: framework submodule '{name}' not initialized — "
            "PipelineRun params will use 'unknown' as the commit SHA; "
            "cluster-side clone will fail. Run `git submodule update --init` "
            "in the sim2real repo to fix.",
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


def _summarize_images(source: str, algos: list[dict]) -> str:
    """Return the IMAGES column value for one translation.

    - BYO source: 'N registered' — BYO images are pre-built at register.
    - Skill source, all algos have image_ref: 'N built'.
    - Skill source, all null: 'N pending'.
    - Skill source, mixed: 'N/M built'.
    - Empty algos list: '-'.
    """
    total = len(algos)
    if total == 0:
        return "-"
    built = sum(1 for a in algos if a.get("image_ref"))
    if source == "byo":
        return f"{total} registered"
    if built == total:
        return f"{total} built"
    if built == 0:
        return f"{total} pending"
    return f"{built}/{total} built"


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


def _cmd_list_translations(_args) -> int:
    from pipeline.lib import translation_ref
    base = layout.translations_dir()
    entries = list(translation_ref.iter_translations(base))
    if not entries:
        print("no translations yet")
        return 0

    # Newest-first by created_at; tie-break on hash for determinism.
    def sort_key(item):
        thash, data = item
        return (data.get("created_at") or "", thash)

    entries.sort(key=sort_key, reverse=True)

    fmt = "{alias:<20} {hash:<12} {source:<8} {images:<15} {created}"
    print(fmt.format(
        alias="ALIAS", hash="HASH", source="SOURCE",
        images="IMAGES", created="CREATED",
    ))
    for thash, data in entries:
        alias = data.get("alias") or "-"
        source = data.get("source") or "?"
        images = _summarize_images(source, data.get("algorithms") or [])
        created = _format_assembled(data.get("created_at") or "")
        print(fmt.format(
            alias=alias, hash=thash[:12], source=source,
            images=images, created=created,
        ))
    return 0


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
    if args.command == "list" and args.subcommand == "translations":
        return _cmd_list_translations(args)
    if args.command == "translate":
        return _cmd_translate(args)
    # argparse's required=True on subparsers means this is unreachable in
    # practice; kept for defensive parity with cluster.py.
    return 1


if __name__ == "__main__":
    sys.exit(main())
