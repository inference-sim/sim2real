#!/usr/bin/env python3
"""sim2real top-level CLI.

Subcommands: ``translation register`` (BYO), ``translate`` (skill-driven
checkpoint + ``--resume``), ``build`` (per-algorithm image build against
a checkpointed translation), ``assemble`` (materialize a run from a
translation), ``use`` (flip active run), ``list runs`` /
``list translations``.
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
from pipeline.lib.build import atomic_write_json as _atomic_write_json  # noqa: E402


def _positive_int(s: str) -> int:
    """argparse type= callable — accepts strings parsing to integers >= 1."""
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {s!r}"
        )
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {v}")
    return v


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


def _parse_algorithm_triple(value: str) -> tuple[str, str, str]:
    """Parse ``<name>=<image-ref>@<config-path>`` into ``(name, image_ref, config_path)``.

    Splits on the first ``=`` (names cannot contain ``=`` by regex) and
    then on the rightmost ``@`` in the RHS (digest refs contain ``@``;
    overlay paths must not contain ``@`` — enforced by rejecting image
    refs with more than one ``@`` after the split). Config paths
    containing ``=`` are supported.

    Raises ``argparse.ArgumentTypeError`` on any parse failure.
    """
    from pipeline.lib import source_locator as _source_locator
    from pipeline.lib import translation_ref
    # Redact once so every error-message interpolation is credential-safe.
    # An operator mispaste of a git URL into --algorithm (both --algorithm
    # and --build accept NAME=X@CONFIG; easy to confuse) would otherwise
    # leak a PAT-in-URL to stderr via the outer catch. Mirrors the
    # _parse_build_triple treatment below.
    safe = _source_locator.redact_url(value)
    eq_idx = value.find("=")
    if eq_idx < 0:
        raise argparse.ArgumentTypeError(
            f"--algorithm value {safe!r} missing '=' "
            "(expected '<name>=<image-ref>@<config-path>')"
        )
    name = value[:eq_idx]
    rhs = value[eq_idx + 1:]
    at_idx = rhs.rfind("@")
    if at_idx < 0:
        raise argparse.ArgumentTypeError(
            f"--algorithm value {safe!r} missing '@' after '=' "
            "(expected '<name>=<image-ref>@<config-path>')"
        )
    image_ref = rhs[:at_idx]
    config_path = rhs[at_idx + 1:]
    if not image_ref:
        raise argparse.ArgumentTypeError(
            f"--algorithm value {safe!r} has empty image-ref"
        )
    if not config_path:
        raise argparse.ArgumentTypeError(
            f"--algorithm value {safe!r} has empty config-path"
        )
    # Valid image refs have at most one '@' (the digest suffix). More than
    # one '@' in the parsed image ref means the config path likely had a
    # '@' that rightmost-@ split cannot disambiguate — reject.
    if image_ref.count("@") > 1:
        raise argparse.ArgumentTypeError(
            f"--algorithm value {safe!r}: overlay path cannot contain '@' "
            "(parsed image ref has multiple '@'; the rightmost-@ split rule "
            "cannot distinguish a digest '@' from a path '@')"
        )
    try:
        translation_ref.validate_name(name)
    except translation_ref.ValidationError as exc:
        raise argparse.ArgumentTypeError(
            f"--algorithm value {safe!r} has invalid name: {exc}"
        ) from exc
    return name, image_ref, config_path


def _parse_build_triple(value: str) -> tuple[str, str, str]:
    """Parse ``<name>=<location>@<config-path>`` into ``(name, location, config_path)``.

    ``<location>`` is either a filesystem path or a ``git+<url>#<ref>`` string.
    The parser is content-agnostic — it just splits on ``=`` and the rightmost
    ``@``. Location interpretation is delegated to
    :func:`pipeline.lib.source_locator.parse_location`.

    Split rules mirror :func:`_parse_algorithm_triple`: first ``=`` separates
    name from the rest; the RIGHTMOST ``@`` separates location from config
    path. Git-SSH URLs contain ``@`` in their host segment
    (``git+ssh://git@host/...``); the rightmost-@ split keeps that ``@``
    with the location, which is correct as long as the config path does
    not itself contain ``@``.

    Raises ``argparse.ArgumentTypeError`` on any parse failure.
    """
    from pipeline.lib import source_locator as _source_locator
    from pipeline.lib import translation_ref
    # Redact once so every error-message interpolation is credential-safe.
    # A user typo (missing '=', empty config-path, etc.) on a PAT-in-URL
    # --build spec would otherwise leak the token to stderr via the outer
    # argparse.ArgumentTypeError catch — iter-6's outer redaction only
    # covered the SourceLocatorError side of the parse.
    safe = _source_locator.redact_url(value)
    eq_idx = value.find("=")
    if eq_idx < 0:
        raise argparse.ArgumentTypeError(
            f"--build value {safe!r} missing '=' "
            "(expected '<name>=<location>@<config-path>')"
        )
    name = value[:eq_idx]
    rhs = value[eq_idx + 1:]
    at_idx = rhs.rfind("@")
    if at_idx < 0:
        raise argparse.ArgumentTypeError(
            f"--build value {safe!r} missing '@' after '=' "
            "(expected '<name>=<location>@<config-path>')"
        )
    location = rhs[:at_idx]
    config_path = rhs[at_idx + 1:]
    if not location:
        raise argparse.ArgumentTypeError(
            f"--build value {safe!r} has empty location"
        )
    if not config_path:
        raise argparse.ArgumentTypeError(
            f"--build value {safe!r} has empty config-path"
        )
    try:
        translation_ref.validate_name(name)
    except translation_ref.ValidationError as exc:
        raise argparse.ArgumentTypeError(
            f"--build value {safe!r} has invalid name: {exc}"
        ) from exc
    return name, location, config_path


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


def _compute_translation_hash(entries: list[dict]) -> str:
    """SHA-256 hex over canonical-JSON of the sorted-by-name algorithm list.

    Each entry is ``{"name": str, "image": str, "config_sha": str}`` where
    ``image`` is the ``sha256:...`` digest when the image ref carried one,
    else the raw ref. ``config_sha`` is ``sha256(config_bytes).hexdigest()``.

    Order-invariant: entries are sorted by ``name`` before serialization.
    Deterministic in the algorithm-set membership — same triples in a
    different order produce the same hash. N=1 uses the same shape as
    N>1 (list of one entry), superseding the step-1 formula.
    """
    sorted_entries = sorted(entries, key=lambda e: e["name"])
    canonical = json.dumps(
        [
            {
                "config": e["config_sha"],
                "image": e["image"],
                "name": e["name"],
            }
            for e in sorted_entries
        ],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_translation_output(
    *,
    algorithms: list[dict],
    translation_hash: str,
    source: str,
    alias: str | None,
    created_at: str,
) -> dict:
    """Build the ``translation_output.json`` body — step-2 batched shape.

    Each entry in ``algorithms`` is ``{"name", "image_ref", "image_digest",
    "config_path"}`` (``config_path`` is the relative path
    ``generated/<name>/<name>_config.yaml``). ``source_path``/``source_sha256``
    are ``None`` for BYO. ``alias`` is the shared translation-level alias
    (algorithm name when N==1, ``None`` when N>1).

    An optional ``provenance`` dict on an input entry is merged into the
    output algorithm entry. Used by ``--build`` git-URL entries to carry
    ``source_git_url`` and ``source_git_ref``. Empty / missing provenance
    contributes no extra keys — BYO entries and path-based ``--build``
    entries record nothing beyond the standard fields.
    """
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "source": source,
        "alias": alias,
        "algorithms": [
            {
                "name": a["name"],
                "source_path": None,
                "source_sha256": None,
                "config_path": a["config_path"],
                "image_ref": a["image_ref"],
                "image_digest": a["image_digest"],
                **(a.get("provenance") or {}),
            }
            for a in algorithms
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
    baselines: list[dict],
    algorithms: list[dict],
    context: dict,
) -> dict:
    """Build ``skill_input.json`` body per design §Schemas.

    Absolute paths at the top level so the skill doesn't have to compose
    them; per-algorithm ``output_dir`` and ``config_output_path`` are
    relative to ``translations_dir``. ``source_path`` is relative to
    ``experiment_root``. ``baselines`` is a list — one entry per baseline
    that any algorithm's ``defaults`` cross-references (see
    ``manifest.py``'s baselines↔algorithms xref). Empty list is valid
    (no referenced baseline overlay) — the writer's Phase 2 loop
    collapses to a no-op.
    """
    baseline_overlay_by_name = {
        bl["name"]: bl["generated_overlay_path"] for bl in baselines
    }
    return {
        "version": 1,
        "translation_hash": translation_hash,
        "experiment_root": str(experiment_root),
        "translations_dir": str(translations_dir),
        "scenario": scenario,
        "baselines": baselines,
        "algorithms": [
            {
                "name": a["name"],
                "source_path": a["source_path"],
                "source_sha256": a["source_sha256"],
                "output_dir": f"generated/{a['name']}",
                "config_output_path": f"generated/{a['name']}/{a['name']}_config.yaml",
                "baseline_overlay_path": baseline_overlay_by_name.get(a.get("defaults")),
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
    prepared: list[dict],
    registered_at: str,
) -> dict:
    """Build the ``registered.json`` body for a BYO translation.

    Batched shape: N per-algorithm entries under ``algorithms``. For N==1
    the shape is the same (a list of one). ``prepared`` is the internal
    per-algorithm record produced by ``_register_translation`` and carries
    ``name``, ``image_ref``, ``image_digest``.
    """
    return {
        "version": 1,
        "source": "byo",
        "registered_at": registered_at,
        "algorithms": [
            {
                "name": p["name"],
                "image_ref": p["image_ref"],
                "image_digest": p["image_digest"],
            }
            for p in prepared
        ],
    }


def _dispatch_build(entry: dict, *, thash: str, build_context: dict) -> None:
    """Build the image for one ``--build`` entry and record ref+digest.

    Mutates ``entry`` in place: sets ``image_ref`` (composed as
    ``<registry>/<repo>:<thash[:12]>-<name>``) and ``image_digest`` (from
    a post-build ``skopeo inspect`` probe; ``None`` if the probe fails
    but the build succeeded — recorded so the caller can surface a
    warning).

    Uses :func:`pipeline.lib.build.dispatch_buildkit_build` — the same
    path ``sim2real build`` uses for the skill-driven flow. Source is
    obtained via ``entry["location"].materialize()`` (a context manager
    that clones a git repo into a scratch dir for :class:`GitLocation`,
    or passes the path through for :class:`PathLocation`). Any failure
    is surfaced as :class:`RuntimeError` — the caller aborts before
    writing ``translation_output.json``.

    Idempotency: if the image tag already exists in the registry with a
    resolvable digest, skips buildkit and records the pre-existing digest
    (matches ``sim2real build``'s pre-build probe path). This lets a
    re-run with the same source (same identity → same ``thash``) short-
    circuit the buildkit step.
    """
    from pipeline.lib import build
    image_ref = build.compose_image_ref(
        registry=build_context["registry"],
        repo=build_context["repo_name"],
        tag=f"{thash[:12]}-{entry['name']}",
    )
    entry["image_ref"] = image_ref

    # Pre-build probe: if the image already exists in the registry with
    # a resolvable digest, skip the build. Matches sim2real build's
    # idempotency path for previously-built algorithms.
    prior_digest = build.probe_image_digest(image_ref)
    if prior_digest is not None:
        entry["image_digest"] = prior_digest
        return

    with entry["location"].materialize() as source_dir:
        build_id = f"sim2real-register-{thash[:8]}-{entry['name']}"
        rc = build.dispatch_buildkit_build(
            image_ref=image_ref,
            build_id=build_id,
            namespace=build_context["build_namespace"],
            source_dir=source_dir,
            run_dir=layout.translation_dir(thash),
            repo_root=_REPO_ROOT,
            registry_secret_name=build_context["registry_secret_name"],
        )
    if rc != 0:
        raise RuntimeError(
            f"buildkit dispatch failed for --build {entry['name']!r} "
            f"(image_ref={image_ref}, rc={rc})"
        )
    entry["image_digest"] = build.probe_image_digest(image_ref)
    if entry["image_digest"] is None:
        # Buildkit push succeeded but the post-build skopeo probe couldn't
        # resolve a digest (transient network, auth flake, missing skopeo).
        # image_digest lands as null in translation_output.json; surface a
        # warning so operators can `skopeo inspect` and diagnose. Mirrors
        # `sim2real build`'s post-probe null-digest warning.
        print(
            f"warning: built {image_ref}; digest not recorded "
            f"(post-build skopeo probe returned no digest — "
            f"image_digest for {entry['name']!r} recorded as null)",
            file=sys.stderr,
        )


def _register_translation(
    *,
    algorithms: list[dict],
    baseline_config_path: Path | None,
    registered_hash: str | None,
    now_iso: str,
    force: bool = False,
    build_context: dict | None = None,
) -> tuple[str, str]:
    """Register a translation on disk — BYO, assisted-build, or a mix.

    ``algorithms`` is a list of ``AlgorithmSpec`` dicts. Each carries a
    ``kind`` marker (``"byo"`` or ``"build"``):
      • BYO: ``{"kind": "byo", "name": str, "image_ref": str,
        "config_path": Path}``. Image already exists in the registry.
      • Build: ``{"kind": "build", "name": str, "location": Location,
        "config_path": Path}``. Framework materializes the source,
        composes ``image_ref``, dispatches buildkit, records
        ``image_ref`` + ``image_digest``.

    Caller has already validated names and confirmed each config file
    exists and parses as YAML — this function does not re-validate.

    When any entry has ``kind == "build"``, ``build_context`` must be a
    dict with ``registry``, ``repo_name``, ``build_namespace``, and
    ``registry_secret_name`` — resolved by :func:`_resolve_build_context`.

    Alias policy: when ``len(algorithms) == 1``, the translation's
    top-level ``alias`` is the sole algorithm's name (matches step-1
    behavior for N=1). When ``len(algorithms) > 1``, top-level ``alias``
    is ``None`` — batched translations are referenced by hash. Alias
    collision is still checked per-algorithm name against
    ``find_by_alias`` regardless of N (each algo name must not shadow an
    existing translation's alias unless ``force`` is set, in which case
    the previous alias is cleared).

    Returns ``(translation_hash, status)`` where ``status`` is
    ``"created"`` (fresh registration) or ``"idempotent"`` (matching
    translation already existed; no writes).
    """
    from pipeline.lib import translation_ref

    # Default kind to "byo" when unset — preserves the pre-#587 caller
    # contract where algorithm entries carried only image_ref/config_path.
    # New callers set kind explicitly.
    for a in algorithms:
        a.setdefault("kind", "byo")

    if any(a["kind"] == "build" for a in algorithms) and build_context is None:
        raise RuntimeError(
            "internal error: _register_translation called with build "
            "entries but no build_context"
        )

    # Prepare per-algorithm derived fields. Both kinds carry config_bytes
    # + config_sha; identity (the string fed to _compute_translation_hash
    # as the ``image`` component) diverges by kind:
    #   BYO   → skopeo-probed digest if present in the ref, else raw ref
    #   Build → source-content identity (path-content-hash or resolved
    #           git commit sha); the actual image_ref/digest are filled
    #           in AFTER the hash is computed and the translation dir is
    #           reserved.
    # ``location`` is preserved on build entries so the build phase can
    # materialize the source once ``thash`` is known.
    prepared: list[dict] = []
    for a in algorithms:
        cfg_bytes = a["config_path"].read_bytes()
        entry: dict = {
            "kind": a["kind"],
            "name": a["name"],
            "config_bytes": cfg_bytes,
            "config_sha": hashlib.sha256(cfg_bytes).hexdigest(),
            # Populated below based on kind:
            "image_ref": None,
            "image_digest": None,
            "digest_or_ref": None,
            "location": None,
            "provenance": {},
        }
        if a["kind"] == "byo":
            digest = _extract_digest_from_ref(a["image_ref"])
            entry["image_ref"] = a["image_ref"]
            entry["image_digest"] = digest
            entry["digest_or_ref"] = digest if digest is not None else a["image_ref"]
        else:  # build
            location = a["location"]
            entry["location"] = location
            entry["digest_or_ref"] = location.identity()
            entry["provenance"] = location.provenance()
        prepared.append(entry)

    # Compute the batched translation hash. Same formula regardless of
    # kind — the identity input just varies (see prepared[i]["digest_or_ref"]).
    thash = _compute_translation_hash([
        {"name": p["name"], "image": p["digest_or_ref"], "config_sha": p["config_sha"]}
        for p in prepared
    ])

    if registered_hash is not None and registered_hash != thash:
        raise RuntimeError(
            f"--registered-hash mismatch: expected {registered_hash}, got {thash}"
        )

    out_path = layout.translation_output_path(thash)
    if out_path.exists():
        existing = translation_ref.read_translation_output(out_path)
        existing_names = sorted(a.get("name") for a in existing.get("algorithms", []))
        wanted_names = sorted(p["name"] for p in prepared)
        if existing_names != wanted_names:
            raise ValueError(
                f"algorithm-set mismatch: translation {thash} records "
                f"{existing_names}, refusing to re-register with {wanted_names} "
                "(hash collision — investigate)"
            )
        # Verify all on-disk artifacts are present; incomplete state → surface.
        missing: list[str] = []
        if not layout.registered_path(thash).exists():
            missing.append("registered.json")
        for p in prepared:
            if not layout.generated_config_path(thash, p["name"]).exists():
                missing.append(f"generated/{p['name']}/{p['name']}_config.yaml")
        if missing:
            raise RuntimeError(
                f"translation {thash} directory is incomplete (missing: "
                f"{', '.join(missing)}); remove {layout.translation_dir(thash)} "
                f"and re-run to recover"
            )
        return thash, "idempotent"

    # Alias-collision check per algorithm — must precede any writes.
    for p in prepared:
        other = translation_ref.find_by_alias(p["name"], layout.translations_dir())
        if other is not None and other != thash:
            if not force:
                raise RuntimeError(
                    f"alias {p['name']!r} already assigned to translation "
                    f"{other}; pass --force to reassign"
                )
            _clear_alias_on(other)

    # All checks passed — materialize the translation dir.
    tdir = layout.translation_dir(thash)
    for p in prepared:
        (tdir / "generated" / p["name"]).mkdir(parents=True, exist_ok=True)

    # Build phase for --build entries. Runs BEFORE the atomic write of
    # translation_output.json so recorded image_ref/image_digest reflect
    # the actual build result. Failure here raises RuntimeError, which
    # bubbles up to _cmd_translation_register — the partially-materialized
    # generated/<name>/ directories are left on disk (empty of contents;
    # the config file write happens later in this function). If any
    # build fails, the caller sees the error and the translation is not
    # recorded — no translation_output.json is written.
    for p in prepared:
        if p["kind"] != "build":
            continue
        _dispatch_build(p, thash=thash, build_context=build_context)

    alias = prepared[0]["name"] if len(prepared) == 1 else None
    tout = _build_translation_output(
        algorithms=[
            {
                "name": p["name"],
                "image_ref": p["image_ref"],
                "image_digest": p["image_digest"],
                "config_path": f"generated/{p['name']}/{p['name']}_config.yaml",
                "provenance": p["provenance"],
            }
            for p in prepared
        ],
        translation_hash=thash,
        source="byo",
        alias=alias,
        created_at=now_iso,
    )
    _atomic_write_json(out_path, tout)

    reg = _build_registered(prepared, now_iso)
    layout.registered_path(thash).write_text(json.dumps(reg, indent=2) + "\n")

    for p in prepared:
        layout.generated_config_path(thash, p["name"]).write_bytes(p["config_bytes"])

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

    reg = tsub.add_parser(
        "register",
        help="Register one or more BYO (pre-built) translations into a single translation dir",
    )
    reg.add_argument(
        "--algorithm",
        required=False,
        default=None,
        action="append",
        metavar="ALG",
        help=(
            "Pre-built (BYO) algorithm spec — repeatable, mixable with --build. "
            "Preferred form: '<name>=<image-ref>@<config-path>' (all fields "
            "inline). Deprecated form: '<name>' alone, with --image and "
            "--config supplied separately (N=1 only; emits deprecation "
            "warning). Names match [A-Za-z0-9][A-Za-z0-9._-]*, max 128 chars. "
            "At least one of --algorithm or --build must be supplied."
        ),
    )
    reg.add_argument(
        "--image",
        default=None,
        metavar="REF",
        help=(
            "DEPRECATED: EPP image reference. Use the '<name>=<image>@<config>' "
            "form of --algorithm instead."
        ),
    )
    reg.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "DEPRECATED: Treatment overlay YAML path. Use the "
            "'<name>=<image>@<config>' form of --algorithm instead."
        ),
    )
    reg.add_argument(
        "--build",
        default=None,
        action="append",
        metavar="SPEC",
        dest="build",
        help=(
            "Assisted-BYO source spec — repeatable, mixable with --algorithm. "
            "Form: '<name>=<location>@<config-path>'. <location> is a "
            "filesystem path or 'git+<url>#<ref>'. Framework materializes "
            "the source, dispatches buildkit, records image_ref/digest "
            "(and source_git_url/source_git_ref for git locations). "
            "Requires a provisioned cluster (see 'cluster.py provision')."
        ),
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
        help="Reassign an alias from a previous translation.",
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
    asm.add_argument(
        "--replicas",
        type=_positive_int,
        default=1,
        metavar="N",
        help="number of replica iterations per (workload, package) pair (default: 1)",
    )

    use = sub.add_parser("use", help="Set the active run in setup_config.json")
    use.add_argument(
        "--run",
        required=True,
        metavar="RUN_NAME",
        help="run name — must correspond to workspace/runs/<RUN_NAME>/",
    )

    res = sub.add_parser(
        "resolve",
        help="Emit a hydrated JSON view of a run (metadata + paths)",
    )
    res.add_argument(
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

    b = sub.add_parser(
        "build",
        help="Build EPP images for each algorithm in a translation",
    )
    b.add_argument(
        "--translation",
        required=True,
        metavar="REF",
        help="alias, hash prefix, or full translation hash",
    )
    b.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Rebuild and push even when the registry already has the tag",
    )
    b.add_argument(
        "--skip-build",
        action="store_true",
        help=(
            "Skip probe + build for every algorithm. Downstream 'sim2real "
            "assemble' will fail if any image_ref is still null."
        ),
    )

    return parser


# Private alias used by tests that import _build_parser.
_build_parser = build_parser


def _cmd_translation_register(args) -> int:
    # Imports scoped to this command are hoisted here so every downstream
    # reference (spec parsing, prereq checks, outer except tuple) uses
    # the same aliases. Prior iterations left duplicate imports inside
    # nested scopes; consolidating avoids the maintenance hazard.
    from pipeline.lib import build as _build
    from pipeline.lib import source_locator as _source_locator

    # Normalize CLI forms into a list of AlgorithmSpec dicts. Each entry
    # carries a ``kind`` marker: ``"byo"`` for --algorithm (pre-built image
    # already in the registry) or ``"build"`` for --build (framework builds
    # source before registering the resulting image). Order within each
    # source is preserved; the final list interleaves both kinds in
    # invocation order (--algorithm first, then --build).
    algo_values: list[str] = args.algorithm or []
    build_values: list[str] = args.build or []
    if not algo_values and not build_values:
        print(
            "error: at least one of --algorithm or --build must be supplied",
            file=sys.stderr,
        )
        return 2
    algorithms: list[dict] = []
    if args.image is not None or args.config is not None:
        # Deprecated form: single --algorithm plus separate --image/--config.
        if build_values:
            print(
                "error: --image/--config (deprecated form) cannot be combined "
                "with --build; use inline '<name>=<image>@<config>' on --algorithm",
                file=sys.stderr,
            )
            return 2
        if len(algo_values) != 1:
            print(
                "error: --image/--config are only valid with a single --algorithm "
                "in the deprecated form; use --algorithm '<name>=<image>@<config>' "
                "for multiple algorithms",
                file=sys.stderr,
            )
            return 2
        if args.image is None or args.config is None:
            print(
                "error: deprecated form requires BOTH --image and --config",
                file=sys.stderr,
            )
            return 2
        if "=" in algo_values[0]:
            print(
                f"error: --algorithm value {algo_values[0]!r} appears to be an "
                "inline triple; do not combine with --image/--config",
                file=sys.stderr,
            )
            return 2
        from pipeline.lib import translation_ref
        try:
            name = translation_ref.validate_name(algo_values[0])
        except translation_ref.ValidationError as exc:
            print(f"error: --algorithm: {exc}", file=sys.stderr)
            return 2
        algorithms.append({
            "kind": "byo",
            "name": name,
            "image_ref": args.image,
            "config_path": Path(args.config),
        })
        print(
            "warning: --image/--config are deprecated; use "
            "--algorithm '<name>=<image-ref>@<config-path>' instead",
            file=sys.stderr,
        )
    else:
        # New form: each --algorithm value is an inline triple.
        for val in algo_values:
            try:
                name, image_ref, config_path_str = _parse_algorithm_triple(val)
            except argparse.ArgumentTypeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            algorithms.append({
                "kind": "byo",
                "name": name,
                "image_ref": image_ref,
                "config_path": Path(config_path_str),
            })

    # --build specs: parse to (name, location string, config path) and
    # resolve the location string into a Location object. Malformed
    # locations (empty git ref, missing '#', etc.) fail here before any
    # cluster or build work.
    if build_values:
        for val in build_values:
            try:
                name, loc_str, config_path_str = _parse_build_triple(val)
            except argparse.ArgumentTypeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            try:
                location = _source_locator.parse_location(loc_str)
            except _source_locator.SourceLocatorError as exc:
                # Redact `val` before printing — the exception message
                # is already redacted at raise time inside
                # parse_location, but this outer echo would otherwise
                # print the raw CLI argument (which may embed a PAT-in-
                # URL) to stderr AND shell history / CI logs.
                safe_val = _source_locator.redact_url(val)
                print(
                    f"error: --build value {safe_val!r}: {exc}",
                    file=sys.stderr,
                )
                return 2
            algorithms.append({
                "kind": "build",
                "name": name,
                "location": location,
                "config_path": Path(config_path_str),
            })

    # Duplicate-name check (either form).
    seen: dict[str, int] = {}
    for a in algorithms:
        seen[a["name"]] = seen.get(a["name"], 0) + 1
    dupes = sorted(n for n, c in seen.items() if c > 1)
    if dupes:
        print(
            f"error: duplicate algorithm name(s) in one register call: "
            f"{', '.join(dupes)}",
            file=sys.stderr,
        )
        return 2

    # Existence + YAML validation for every config file (fail-fast, no writes).
    for a in algorithms:
        cp: Path = a["config_path"]
        if not cp.exists():
            print(f"error: config file not found: {cp}", file=sys.stderr)
            return 2
        try:
            yaml.safe_load(cp.read_text())
        except yaml.YAMLError as exc:
            print(f"error: {cp} is not valid YAML: {exc}", file=sys.stderr)
            return 2

    baseline_config_path = Path(args.baseline_config) if args.baseline_config else None
    if baseline_config_path is not None:
        if not baseline_config_path.exists():
            print(
                f"error: --baseline-config file not found: {baseline_config_path}",
                file=sys.stderr,
            )
            return 2
        try:
            yaml.safe_load(baseline_config_path.read_text())
        except yaml.YAMLError as exc:
            print(f"error: --baseline-config is not valid YAML: {exc}", file=sys.stderr)
            return 2

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # If any --build spec is present, resolve the workspace's registry
    # (setup_config.json) and cluster (cluster_config.json) up-front so
    # we fail fast before touching any translation directory. Pure-BYO
    # invocations (no --build) skip this — no build needed, no cluster
    # required.
    build_context: dict | None = None
    if any(a["kind"] == "build" for a in algorithms):
        # Fail-fast prereq checks for the --build path — same shape as
        # `sim2real build`. check_skopeo() surfaces a missing binary with
        # an actionable install hint before any translation dir or
        # buildkit work happens; without it, probe_image_digest silently
        # swallows FileNotFoundError and returns None, disabling the
        # idempotency short-circuit and recording image_digest as null
        # with no user-visible signal.
        try:
            _build.check_skopeo()
        except _build.BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        # If any --build spec is a git-URL, also check for git. Path-based
        # --build specs don't invoke git. Symmetric with skopeo's install-
        # hint surface.
        if any(
            isinstance(a.get("location"), _source_locator.GitLocation)
            for a in algorithms
            if a["kind"] == "build"
        ):
            try:
                _source_locator.check_git()
            except _source_locator.SourceLocatorError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        try:
            build_context = _resolve_build_context()
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        thash, status = _register_translation(
            algorithms=algorithms,
            baseline_config_path=baseline_config_path,
            registered_hash=args.registered_hash,
            now_iso=now_iso,
            force=args.force,
            build_context=build_context,
        )
    except (
        RuntimeError,
        ValueError,
        OSError,
        _source_locator.SourceLocatorError,
        _build.BuildError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if status == "idempotent":
        print(
            f"warning: translation {thash} already registered, no-op",
            file=sys.stderr,
        )
    else:
        # Digest warning applies only to BYO entries (user supplied the
        # image ref, may not have carried an @sha256:... digest). Build
        # entries have their digest populated by the post-build probe;
        # a null digest there is a probe-side issue reported separately.
        for a in algorithms:
            if a["kind"] != "byo":
                continue
            if _extract_digest_from_ref(a["image_ref"]) is None:
                print(
                    f"warning: image_digest for {a['name']!r} recorded as null "
                    f"(no @sha256: in image); hash falls back to using image_ref",
                    file=sys.stderr,
                )
    print(f"registered translation {thash}")
    return 0


def _resolve_build_context() -> dict:
    """Resolve cluster + registry info needed for ``--build`` specs.

    Returns a dict with ``registry``, ``repo_name``, ``build_namespace``,
    ``registry_secret_name``. Raises :class:`RuntimeError` with a
    user-facing message on any missing prerequisite. Mirrors the shape
    ``sim2real build`` uses (see ``_cmd_build`` for the same lookups)
    so behavior is consistent across the two entry points.
    """
    from pipeline.lib import cluster_ops
    setup_cfg_path = layout.setup_config_path()
    if not setup_cfg_path.exists():
        raise RuntimeError(
            "workspace/setup_config.json not found — "
            "run setup.py first (needed for --build)"
        )
    try:
        setup_cfg = json.loads(setup_cfg_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"reading setup_config.json: {exc}") from exc
    registry = setup_cfg.get("registry") or ""
    repo_name = setup_cfg.get("repo_name") or ""
    if not registry or not repo_name:
        raise RuntimeError(
            "workspace/setup_config.json is missing 'registry' or "
            "'repo_name' — re-run setup.py with --registry (needed for --build)"
        )
    cluster_ids = layout.list_cluster_ids()
    if not cluster_ids:
        raise RuntimeError(
            "no cluster provisioned; run "
            "'cluster.py provision <cluster_id>' first (needed for --build)"
        )
    if len(cluster_ids) > 1:
        raise RuntimeError(
            f"multiple clusters found ({cluster_ids}); "
            "sim2real assumes a single cluster per workspace"
        )
    try:
        cluster_config = cluster_ops.read_cluster_config(cluster_ids[0])
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"reading cluster_config.json: {exc}") from exc
    namespaces = cluster_config.get("namespaces") or []
    if not namespaces:
        raise RuntimeError(
            "cluster_config.json has no namespaces; "
            "re-run 'cluster.py provision --namespaces NS1,...'"
        )
    registry_secret_name = (
        (cluster_config.get("secret_names") or {}).get("registry_creds") or ""
    )
    if not registry_secret_name:
        raise RuntimeError(
            "cluster_config.json has no secret_names.registry_creds; "
            "re-run 'cluster.py provision --registry-user U --registry-token T'"
        )
    return {
        "registry": registry,
        "repo_name": repo_name,
        "build_namespace": namespaces[0],
        "registry_secret_name": registry_secret_name,
    }


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

    # BYO guard (issue #497): BYO algorithms have no `source:` for the
    # skill-driven translate pipeline. The BYO path is `sim2real
    # translation register`, which attaches a pre-built image directly.
    # Error early with a pointer rather than silently writing a
    # checkpoint whose sources slice omits the BYO entries.
    for algo in declared_algos:
        if algo.get("byo") is True:
            print(
                f"error: cannot translate algorithm '{algo['name']}' — no "
                f"`source:` in transfer.yaml (BYO algorithm; use "
                f"`sim2real translation register` directly).",
                file=sys.stderr,
            )
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

    # Alias uniqueness — the scenario doubles as the translation's alias.
    # Design §Alias: "translate and register both refuse if another
    # translation has the same alias value pointing at a different hash,
    # unless --force is passed." Mirror `_cmd_translation_register`'s check
    # (sim2real.py:_register_translation), but skip it for --resume — resume
    # never mutates the dir, so a stale alias elsewhere doesn't affect it.
    if not args.resume:
        other_hash = translation_ref.find_by_alias(
            scenario, layout.translations_dir()
        )
        if other_hash is not None and other_hash != thash:
            if not args.force:
                print(
                    f"error: alias {scenario!r} already assigned to translation "
                    f"{other_hash}; pass --force to reassign",
                    file=sys.stderr,
                )
                return 2
            try:
                _clear_alias_on(other_hash)
            except OSError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2

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
        print(
            f"translation {thash} (alias: {scenario}) complete — "
            f"run 'sim2real build --translation {scenario}' next"
        )
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
        print(
            f"translation {thash} (alias: {scenario}) already complete — "
            f"run 'sim2real build --translation {scenario}' next"
        )
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
            "defaults": algo.get("defaults"),
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

    # skill_input.baselines is the list of baseline entries whose overlays
    # the skill's Phase 2 must produce. Only baselines that are actually
    # cross-referenced by ``algorithms[*].defaults`` need overlays;
    # unreferenced baselines are dropped. Manifest validation
    # (``manifest.py``) makes ``defaults`` required on every algorithm and
    # ensures it names a real baseline, so the referenced set matches the
    # set of baselines any downstream treatment scenario merges onto. The
    # defensive ``baselines[0]`` fallback covers manifests with baselines
    # but zero algorithms (assemble can still run for baseline-only
    # standby workloads).
    manifest_baselines = manifest.get("baselines") or []
    referenced: set[str] = {
        a["defaults"] for a in manifest.get("algorithms", []) if a.get("defaults")
    }
    if not referenced and manifest_baselines:
        referenced = {manifest_baselines[0]["name"]}
    skin_baselines: list[dict] = [
        {
            "name": bl["name"],
            # Nested per-baseline layout under a ``baselines/`` umbrella
            # (issue #544); assemble reads from the same path.
            "generated_overlay_path": (
                f"generated/baselines/{bl['name']}/baseline_config.yaml"
            ),
        }
        for bl in manifest_baselines
        if bl["name"] in referenced
    ]

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
        translations_dir=layout.translation_dir(thash),
        scenario=scenario,
        baselines=skin_baselines,
        algorithms=algo_records,
        context=context,
    )
    _atomic_write_json(tdir / "skill_input.json", skin)

    print(
        f"translation {thash} (alias: {scenario}) checkpoint written — "
        f"run '/sim2real-translate' then 'sim2real translate --resume'"
    )
    return 0


def _cmd_build(args) -> int:
    """Build EPP images for every algorithm in a translation.

    Prerequisites (checked in order):
      1. skopeo on PATH (unless --skip-build).
      2. Translation ref resolves via translation_ref.resolve_translation_ref.
      3. Every algorithm has generated/<algo>/<algo>_output.json on disk.
      4. workspace/setup_config.json has non-empty registry and repo_name.

    Per algorithm:
      - Compose image_ref = <registry>/<repo>:<translation_hash[:12]>-<algo>.
      - --skip-build → skip everything, do not touch translation_output.json.
      - Already-recorded image_ref+digest + no --force-rebuild → idempotent skip.
      - Pre-build skopeo probe (skipped by --force-rebuild): success writes
        the digest and continues; failure of any kind → build (fail-safe).
      - dispatch_buildkit_build; non-zero rc aborts the loop, prior algos'
        state is preserved (atomic per-algo writes).
      - Post-build probe records the digest; probe failure → image_digest
        recorded as null with a warning.
    """
    from pipeline.lib import build, cluster_ops, manifest as _manifest, translation_ref

    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )

    # BYO guard (issue #497): if a transfer.yaml is discoverable and
    # declares no `component:` (all algorithms marked `byo: true`), the
    # images are pre-built and there is nothing to build. Error early
    # rather than proceeding to skopeo/translation resolution. Unparseable
    # manifests defer to the downstream path so their error wins.
    manifest_path = exp_root / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = exp_root / "config" / "transfer.yaml"
    if manifest_path.exists():
        try:
            _mf = _manifest.load_manifest(manifest_path)
        except _manifest.ManifestError:
            _mf = None
        if _mf is not None and "component" not in _mf:
            print(
                "error: nothing to build — this transfer.yaml declares no "
                "component (all algorithms are BYO; images are pre-built).",
                file=sys.stderr,
            )
            return 2

    layout.set_experiment_root(str(exp_root))

    if not args.skip_build:
        try:
            build.check_skopeo()
        except build.BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        translation_hash = translation_ref.resolve_translation_ref(args.translation)
    except translation_ref.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    tout_path = layout.translation_output_path(translation_hash)
    try:
        tout = translation_ref.read_translation_output(tout_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    algorithms = tout.get("algorithms") or []
    if not algorithms:
        print(
            f"error: translation {translation_hash} has no algorithms recorded",
            file=sys.stderr,
        )
        return 2

    # Completeness check: every algo must have generated/<algo>/<algo>_output.json.
    tdir = layout.translation_dir(translation_hash)
    missing = [
        a["name"] for a in algorithms
        if not (tdir / "generated" / a["name"] / f"{a['name']}_output.json").exists()
    ]
    if missing:
        print(
            f"error: translation {translation_hash} incomplete — "
            f"missing outputs for: {', '.join(missing)}; "
            f"run '/sim2real-translate' then 'sim2real translate --resume'",
            file=sys.stderr,
        )
        return 2

    # Registry / repo prereq — read from setup_config.json (mirrors deploy.py).
    setup_cfg_path = layout.setup_config_path()
    if not setup_cfg_path.exists():
        print(
            "error: workspace/setup_config.json not found — run setup.py first",
            file=sys.stderr,
        )
        return 2
    try:
        setup_cfg = json.loads(setup_cfg_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    registry = setup_cfg.get("registry") or ""
    repo_name = setup_cfg.get("repo_name") or ""
    if not registry or not repo_name:
        print(
            "error: workspace/setup_config.json is missing 'registry' or "
            "'repo_name' — re-run setup.py with --registry",
            file=sys.stderr,
        )
        return 2

    # Cluster resolution — matches step-0 "single cluster per workspace".
    if not args.skip_build:
        cluster_ids = layout.list_cluster_ids()
        if not cluster_ids:
            print(
                "error: no cluster provisioned; run "
                "'cluster.py provision <cluster_id>' first",
                file=sys.stderr,
            )
            return 2
        if len(cluster_ids) > 1:
            print(
                f"error: multiple clusters found ({cluster_ids}); "
                "sim2real assumes a single cluster per workspace",
                file=sys.stderr,
            )
            return 2
        try:
            cluster_config = cluster_ops.read_cluster_config(cluster_ids[0])
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        namespaces = cluster_config.get("namespaces") or []
        if not namespaces:
            print(
                "error: cluster_config.json has no namespaces; "
                "re-run 'cluster.py provision --namespaces NS1,...'",
                file=sys.stderr,
            )
            return 2
        build_namespace = namespaces[0]
        registry_secret_name = (
            (cluster_config.get("secret_names") or {}).get("registry_creds") or ""
        )
        if not registry_secret_name:
            print(
                "error: cluster_config.json has no secret_names.registry_creds; "
                "re-run 'cluster.py provision --registry-user U --registry-token T'",
                file=sys.stderr,
            )
            return 2
    else:
        build_namespace = ""
        registry_secret_name = ""

    source_dir = exp_root / repo_name
    tag_prefix = translation_hash[:12]
    any_failure = False

    for algo in algorithms:
        algo_name = algo["name"]
        try:
            image_ref = build.compose_image_ref(
                registry, repo_name, f"{tag_prefix}-{algo_name}"
            )
        except build.BuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        # Idempotency — already recorded with the composed ref and a digest?
        if (algo.get("image_ref") == image_ref
                and algo.get("image_digest")
                and not args.force_rebuild
                and not args.skip_build):
            print(f"already built: {image_ref} ({algo['image_digest']})")
            continue

        if args.skip_build:
            print(f"skipped (--skip-build) for {algo_name}")
            continue

        # Pre-build probe (skip if --force-rebuild).
        if not args.force_rebuild:
            digest = build.probe_image_digest(image_ref)
            if digest is not None:
                algo["image_ref"] = image_ref
                algo["image_digest"] = digest
                try:
                    build.atomic_write_json(tout_path, tout)
                except OSError as exc:
                    print(
                        f"error: failed to write translation_output.json "
                        f"for {algo_name}: {exc}",
                        file=sys.stderr,
                    )
                    return 2
                print(f"probe hit: {image_ref} ({digest})")
                continue

        # About to build: apply this algorithm's source overlay to source_dir
        # so buildkit uploads the correct plugin implementation + runner.go
        # registration. Mirrors the pattern in deploy.py:_cmd_build on main.
        import subprocess
        from pipeline.lib.source_toggle import restore_baseline, restore_treatment

        algo_output_path = (
            tdir / "generated" / algo_name / f"{algo_name}_output.json"
        )
        try:
            algo_output = json.loads(algo_output_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"error: failed to read {algo_output_path}: {exc}",
                file=sys.stderr,
            )
            return 2
        generated_dir = tdir / "generated"

        print(
            f"[sim2real build] applying overlay for {algo_name}: "
            f"files_created={len(algo_output.get('files_created', []))} "
            f"files_modified={len(algo_output.get('files_modified', []))} "
            f"source_dir={source_dir}",
            flush=True,
        )
        try:
            restore_baseline(source_dir, algo_output)
            restore_treatment(
                source_dir, generated_dir, algo_output, algo_name=algo_name
            )
        except (subprocess.CalledProcessError, OSError, FileNotFoundError) as exc:
            print(
                f"error: failed to apply source overlay for {algo_name}: {exc}",
                file=sys.stderr,
            )
            return 2

        try:
            # Build via buildkit.
            build_id = f"sim2real-build-{translation_hash[:8]}-{algo_name}"
            try:
                rc = build.dispatch_buildkit_build(
                    image_ref=image_ref,
                    build_id=build_id,
                    namespace=build_namespace,
                    source_dir=source_dir,
                    run_dir=tdir,
                    repo_root=_REPO_ROOT,
                    registry_secret_name=registry_secret_name,
                )
            except build.BuildError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            if rc != 0:
                print(
                    f"error: build failed for {algo_name} (image_ref={image_ref})",
                    file=sys.stderr,
                )
                any_failure = True
                break

            # Post-build probe. The image has been pushed successfully at this
            # point; a filesystem fault here means we can't record the digest,
            # not that the build failed. The image_ref is still worth surfacing.
            digest = build.probe_image_digest(image_ref)
            algo["image_ref"] = image_ref
            algo["image_digest"] = digest
            try:
                build.atomic_write_json(tout_path, tout)
            except OSError as exc:
                print(
                    f"error: built {image_ref} but failed to record digest in "
                    f"translation_output.json: {exc} — re-run 'sim2real build' "
                    f"to record it",
                    file=sys.stderr,
                )
                return 2
            if digest is None:
                print(f"built {image_ref}; digest not recorded (probe failed)")
            else:
                print(f"built {image_ref} ({digest})")
        finally:
            # Restore baseline for the next iteration regardless of build
            # outcome. Any per-algo files the overlay copied are removed and
            # modified files are reverted, so subsequent iterations start
            # from a clean tree. If this cleanup fails partway (files_created
            # partially deleted, git checkout not yet run) the tree is in an
            # unknown state and subsequent iterations would silently upload
            # the wrong sources — fail loud, set any_failure, and break so
            # the caller sees exit 2 instead of a false success.
            print(
                f"[sim2real build] restoring baseline after {algo_name} build",
                flush=True,
            )
            try:
                restore_baseline(source_dir, algo_output)
            except (subprocess.CalledProcessError, OSError) as exc:
                print(
                    f"error: failed to restore baseline after build for "
                    f"{algo_name}: {exc}",
                    file=sys.stderr,
                )
                any_failure = True
                break

    return 2 if any_failure else 0


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

    from pipeline.lib import manifest as _manifest_mod
    from pipeline.lib import translation_ref
    try:
        translation_hash = translation_ref.resolve_translation_ref(args.translation)
    except translation_ref.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Fail-early: every algorithm declared in transfer.yaml that is also
    # recorded in translation_output.json must have a non-null image_ref
    # (design §Commands §sim2real assemble). Skill-driven translations
    # start with image_ref: null; the operator must run 'sim2real build'
    # before 'sim2real assemble'.
    tout_path = layout.translation_output_path(translation_hash)
    try:
        tout = translation_ref.read_translation_output(tout_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        _manifest_data = _manifest_mod.load_manifest(manifest_path)
    except _manifest_mod.ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    declared_names = {a["name"] for a in (_manifest_data.get("algorithms") or [])}
    recorded_by_name = {
        a["name"]: a for a in (tout.get("algorithms") or [])
        if isinstance(a, dict) and a.get("name")
    }
    unbuilt = [
        name for name in sorted(declared_names & recorded_by_name.keys())
        if recorded_by_name[name].get("image_ref") is None
    ]
    if unbuilt:
        print(
            f"error: translation {args.translation} not built for algorithms: "
            f"{', '.join(unbuilt)} — run 'sim2real build --translation "
            f"{args.translation}' first",
            file=sys.stderr,
        )
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
            replicas=args.replicas,
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
    status = getattr(_assemble_run_lib.assemble_run, "status", "written")
    if status == "noop":
        prior_assembled_at = getattr(
            _assemble_run_lib.assemble_run, "prior_assembled_at", ""
        ) or "unknown"
        print(
            f"No change needed for run '{args.run}': manifest, replicas, and "
            f"translation are unchanged since {prior_assembled_at}.\n"
            "To rebuild anyway (e.g. after an assembler code update), "
            "pass --force."
        )
    else:
        print(f"assembled run {args.run}")
    return 0


def _cmd_resolve(args) -> int:
    """Emit the hydrated JSON view of ``workspace/runs/<--run>/`` on stdout.

    Exit 0 on success (parseable JSON on stdout). Exit 2 on any
    ``ResolveError`` from ``pipeline.lib.resolve`` — the specific
    message goes to stderr and includes a pointer to the ``sim2real``
    command that would repair the missing/corrupt state.
    """
    from pipeline.lib import resolve as resolve_mod
    try:
        view = resolve_mod.resolve_run(
            layout.experiment_root(), args.run
        )
    except resolve_mod.ResolveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(view, indent=2))
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
    if args.command == "resolve":
        return _cmd_resolve(args)
    if args.command == "list" and args.subcommand == "runs":
        return _cmd_list_runs(args)
    if args.command == "list" and args.subcommand == "translations":
        return _cmd_list_translations(args)
    if args.command == "translate":
        return _cmd_translate(args)
    if args.command == "build":
        return _cmd_build(args)
    # argparse's required=True on subparsers means this is unreachable in
    # practice; kept for defensive parity with cluster.py.
    return 1


if __name__ == "__main__":
    sys.exit(main())
