"""Manifest loader for sim2real pipeline (v3 schema)."""
import re
import yaml
from pathlib import Path

from pipeline.lib import observe_argv


class ManifestError(Exception):
    """Manifest validation error."""


_PACKAGE_NAME_RE = re.compile(r'^[a-z0-9]{1,20}$')


def _validate_package_name(name: str, context: str) -> None:
    if not _PACKAGE_NAME_RE.match(name):
        raise ManifestError(
            f"{context} name '{name}' is invalid "
            "(lowercase alphanumeric only, 1-20 chars, no hyphens or underscores)"
        )


_REQUIRED_TOP = ["kind", "version", "scenario"]

#: Every legal top-level manifest key. Unknown keys are REJECTED (#911) rather
#: than silently accepted: before this, a bundle could carry ``measurement:``
#: (or a typo of any real key) and validate cleanly while the value it named was
#: never read — the observe protocol then fell through to the renderer's roster
#: defaults and the run measured something the bundle did not describe. Same
#: posture #901 took for the workload document, applied one level up.
_VALID_TOP_KEYS = frozenset({
    "kind", "version", "scenario", "baselines", "algorithms", "workloads",
    "context", "defaults", "component", "pipeline", "measurement",
})

#: The measurement protocol file's own envelope keys, which are NOT roster keys
#: and so are exempt from the roster allowlist inside the file.
_MEASUREMENT_KIND = "measurement-protocol"
_MEASUREMENT_VERSION = 1
_MEASUREMENT_META_KEYS = frozenset({"kind", "version"})


def _load_measurement(pointer, manifest_path: Path) -> dict:
    """Resolve a ``measurement:`` pointer to its validated protocol values.

    Returns ``{}`` when the key is absent — every roster key then falls through
    to ``observe_argv``'s defaults, which is the pre-#911 behaviour for a bundle
    that never configured the instrument. What #911 removes is the case where a
    bundle DID try to configure it and was silently ignored.

    Validated against ``observe_argv``'s roster rather than a second schema, so
    the file's key names, types and the flags they drive cannot drift from the
    table that renders them (the mistake that produced ``--post-hoc-detector``
    vs ``--detectors``). ``kind``/``version`` are the file's own envelope and are
    exempt from the roster check.
    """
    if pointer is None:
        return {}
    if not isinstance(pointer, str) or not pointer.strip():
        raise ManifestError(
            f"measurement must be a non-empty string naming a protocol file, "
            f"got: {pointer!r}"
        )
    if Path(pointer).is_absolute():
        raise ManifestError(
            f"measurement must be a relative path (resolved against the "
            f"experiment root), got: {pointer}"
        )

    mpath = manifest_path.parent / pointer
    if not mpath.exists():
        raise ManifestError(f"measurement file not found: {mpath}")
    try:
        raw = yaml.safe_load(mpath.read_text())
    except yaml.YAMLError as exc:
        raise ManifestError(f"YAML parse error in {mpath}: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"cannot read measurement file {mpath}: {exc}") from exc

    if raw is None:
        raise ManifestError(
            f"measurement file {mpath} is empty; it must declare "
            f"kind: {_MEASUREMENT_KIND} and at least one protocol key"
        )
    if not isinstance(raw, dict):
        raise ManifestError(f"measurement file {mpath} must be a mapping")

    if raw.get("kind") != _MEASUREMENT_KIND:
        raise ManifestError(
            f"measurement file {mpath}: expected kind: {_MEASUREMENT_KIND}, "
            f"got: {raw.get('kind')!r}"
        )
    mversion = raw.get("version")
    if mversion is None:
        raise ManifestError(
            f"measurement file {mpath}: missing required field: version"
        )
    if mversion != _MEASUREMENT_VERSION:
        raise ManifestError(
            f"measurement file {mpath}: unsupported version: {mversion} "
            f"(expected {_MEASUREMENT_VERSION})"
        )

    values = {k: v for k, v in raw.items() if k not in _MEASUREMENT_META_KEYS}

    unknown = set(values) - observe_argv.VALID_OBSERVE_KEYS
    if unknown:
        raise ManifestError(
            f"measurement file {mpath} contains unknown keys: {sorted(unknown)}. "
            f"Valid keys: {sorted(observe_argv.VALID_OBSERVE_KEYS)}"
        )

    # Per-key TYPES come from the same table that renders the flags, so this
    # allowlist cannot disagree with what a key actually accepts (#900). VALUE
    # validity (a real detector name, a legal api-format) is the renderer's job
    # at render time and is not duplicated here.
    for k, v in values.items():
        problem = observe_argv.check_observe_type(k, v)
        if problem:
            raise ManifestError(
                f"measurement file {mpath}: {k} {problem}, got {v!r}"
            )

    return values


def load_manifest(path: "Path | str") -> dict:
    """Load and validate a sim2real transfer manifest."""
    path = Path(path)
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ManifestError(f"YAML parse error in {path}: {e}") from e

    if data.get("kind") != "sim2real-transfer":
        raise ManifestError(f"Expected kind: sim2real-transfer, got: {data.get('kind')}")

    version = data.get("version")
    if version is None:
        raise ManifestError("Missing required field: version")
    if version != 3:
        raise ManifestError(f"Unsupported manifest version: {version}")

    for field in _REQUIRED_TOP:
        if field not in data:
            raise ManifestError(f"Missing required field: {field}")

    # ── Hard cutover: blis_observe: → measurement: (#911) ───────────────────
    # Checked BEFORE the unknown-key sweep so the operator gets the migration
    # path rather than a bare "unknown key". Rejected, not dual-supported, for
    # the same reason #901 rejected ``trace:``: a single global block with
    # fall-through defaults is exactly what let a half-migrated bundle validate
    # and then measure with roster defaults. Both keys present also lands here,
    # which is the error we want — it means a migration was started, not
    # finished.
    if "blis_observe" in data:
        raise ManifestError(
            "transfer.yaml declares 'blis_observe:', which was replaced by a "
            "bundle-level measurement protocol file (issue #911). Delete the "
            "block and add 'measurement: measurement.yaml', then put the same "
            "keys — unchanged names — in that file:\n"
            "  kind: measurement-protocol\n"
            "  version: 1\n"
            "  maxConcurrency: 10000\n"
            "  timeout: 1800          # PER-REQUEST HTTP timeout, not a run cap\n"
            "  warmupRequests: 50     # 0 for closed-loop multi-turn replay\n"
            "  prewarmDuration: 60s\n"
            "  detectors: composite\n"
            "  apiFormat: completions\n"
            "  recordItl: false\n"
            "  streaming: true\n"
            "The protocol is bundle-scoped and constant across cells — holding "
            "it identical is what makes cells comparable. "
            "See pipeline/README.md#measurement-protocol"
        )

    # ── Reject unknown top-level keys (#911) ───────────────────────────────
    unknown_top = set(data.keys()) - _VALID_TOP_KEYS
    if unknown_top:
        raise ManifestError(
            f"transfer.yaml contains unknown top-level keys: "
            f"{sorted(unknown_top)}. Valid keys: {sorted(_VALID_TOP_KEYS)}. "
            f"Unknown keys are rejected rather than ignored: a key that is "
            f"silently accepted but never read makes a bundle describe a run "
            f"it does not produce."
        )

    # Normalize workloads: absent/null → [] (standby mode — stack up, no benchmarks)
    wl = data.get("workloads")
    if wl is None:
        data["workloads"] = []
    elif not isinstance(wl, list):
        raise ManifestError("workloads must be a list")

    # ── Validate list-form sections ─────────────────────────────────────────
    if "baselines" not in data:
        raise ManifestError("Missing required field: baselines")
    bls = data["baselines"]
    if not isinstance(bls, list):
        raise ManifestError("baselines must be a list")
    seen_names: set[str] = set()
    for i, entry in enumerate(bls):
        if not isinstance(entry, dict):
            raise ManifestError(f"baselines[{i}] must be a mapping")
        if "name" not in entry:
            raise ManifestError(f"baselines[{i}] missing required field: name")
        if "scenario" not in entry:
            raise ManifestError(f"baselines[{i}] missing required field: scenario")
        _validate_package_name(entry["name"], f"baselines[{i}]")
        if entry["name"] in seen_names:
            raise ManifestError(f"baselines: duplicate name '{entry['name']}'")
        seen_names.add(entry["name"])

    if "algorithms" in data:
        algos = data["algorithms"]
        if not isinstance(algos, list):
            raise ManifestError("algorithms must be a list")
        seen_algo_names: set[str] = set()
        for i, entry in enumerate(algos):
            if not isinstance(entry, dict):
                raise ManifestError(f"algorithms[{i}] must be a mapping")
            # `byo: true` marker (v3 schema addition). Bool type; if True,
            # `source:` becomes optional for this entry. Non-BYO entries
            # still require `source:` — per-command validation in
            # sim2real.py rejects BYO manifests where downstream needs it.
            byo = entry.get("byo", False)
            if "byo" in entry and not isinstance(byo, bool):
                raise ManifestError(
                    f"algorithms[{i}].byo must be a bool, "
                    f"got {type(byo).__name__}"
                )
            required = ("name", "defaults") if byo else ("name", "source", "defaults")
            for f in required:
                if f not in entry:
                    raise ManifestError(f"algorithms[{i}] missing required field: {f}")
            _validate_package_name(entry["name"], f"algorithms[{i}]")
            if entry["name"] in seen_algo_names:
                raise ManifestError(f"algorithms: duplicate name '{entry['name']}'")
            seen_algo_names.add(entry["name"])
    else:
        data["algorithms"] = []

    # Cross-reference check: algorithm.defaults must name an existing baseline
    baseline_names = {bl["name"] for bl in data["baselines"]}
    for i, algo in enumerate(data["algorithms"]):
        if algo["defaults"] not in baseline_names:
            raise ManifestError(
                f"algorithms[{i}].defaults references unknown baseline "
                f"'{algo['defaults']}'. Available: {sorted(baseline_names)}"
            )

    # Cross-collision check: all package names must be globally unique
    all_names = [bl["name"] for bl in data["baselines"]] + [a["name"] for a in data["algorithms"]]
    if len(all_names) != len(set(all_names)):
        dupes = [n for n in all_names if all_names.count(n) > 1]
        raise ManifestError(
            f"Package names must be globally unique across baselines and algorithms; "
            f"duplicates: {sorted(set(dupes))}"
        )

    # Context section (optional): only 'text' and 'files' are valid keys
    ctx_raw = data.get("context", {}) or {}
    _valid_context_keys = {"text", "files"}
    unknown = set(ctx_raw.keys()) - _valid_context_keys
    if unknown:
        raise ManifestError(
            f"context contains unknown keys: {sorted(unknown)}. "
            f"Valid keys: text, files"
        )
    ctx_text = ctx_raw.get("text", "") or ""
    ctx_files = ctx_raw.get("files", []) or []
    if not isinstance(ctx_files, list):
        raise ManifestError("context.files must be a list")
    data["context"] = {"text": ctx_text, "files": ctx_files}

    # Defaults section (optional): controls the framework defaults overlay
    # applied from <experiment-root>/baselines/defaults/. Currently only
    # 'disable' is recognized (list of fragment stems to skip).
    defaults_raw = data.get("defaults")
    if defaults_raw is None:
        data["defaults"] = {"disable": []}
    elif not isinstance(defaults_raw, dict):
        raise ManifestError("defaults must be a mapping")
    else:
        _valid_defaults_keys = {"disable"}
        unknown_defaults = set(defaults_raw.keys()) - _valid_defaults_keys
        if unknown_defaults:
            raise ManifestError(
                f"defaults contains unknown keys: {sorted(unknown_defaults)}. "
                f"Valid keys: disable"
            )
        disable = defaults_raw.get("disable", []) or []
        if not isinstance(disable, list):
            raise ManifestError("defaults.disable must be a list")
        for i, entry in enumerate(disable):
            if not isinstance(entry, str) or not entry.strip():
                raise ManifestError(
                    f"defaults.disable[{i}] must be a non-empty string (fragment stem)"
                )
        data["defaults"] = {"disable": disable}

    _validate_v3_fields(data)

    # measurement (optional): a bundle-level pointer to the observe protocol
    # file, replacing the inline ``blis_observe:`` block (#911). Resolved here
    # rather than in ``_validate_v3_fields`` because it needs ``path`` to locate
    # the file relative to the experiment root.
    #
    # The POINTER is replaced by the RESOLVED VALUES under the same key. That is
    # load-bearing, not a convenience: ``slicer.assembly_slice`` copies every
    # non-translation top-level key and ``params_hash`` is taken over those
    # bytes. A bare pointer would put only the FILENAME in the hash, so editing
    # the protocol file would leave ``params_hash`` unchanged and two runs with
    # identical hashes could have been measured with different instrument
    # settings. Inlining preserves exactly the reproducibility the inline block
    # had, and keeps ``manifest.assembly.yaml`` self-contained — an operator
    # reading the snapshot sees values, not a filename pointing at a file that
    # may since have changed.
    data["measurement"] = _load_measurement(data.get("measurement"), path)

    return data


def _validate_v3_fields(data: dict) -> None:
    """Validate and apply defaults for v3-specific fields."""

    # component (required only when at least one algorithm is BLIS-shape;
    # all-BYO manifests may omit component: since BYO images are pre-built).
    component = data.get("component")
    algos = data.get("algorithms") or []
    has_blis_algorithm = any(algo.get("byo") is not True for algo in algos)

    if component is None:
        if has_blis_algorithm:
            raise ManifestError(
                "component is required when non-BYO algorithms are specified"
            )
        # Remove explicit null so downstream .get("component", {}) returns {} not None
        data.pop("component", None)
    elif not isinstance(component, dict):
        raise ManifestError("component must be a mapping")
    else:
        # component.repo (required)
        repo = component.get("repo")
        if not repo or not isinstance(repo, str):
            raise ManifestError("Missing required field: component.repo")

        # component.kind (required)
        kind = component.get("kind")
        if not kind or not isinstance(kind, str):
            raise ManifestError("Missing required field: component.kind")

        # component.path (optional, defaults from last segment of repo)
        if "path" not in component:
            component["path"] = repo.rstrip("/").rsplit("/", 1)[-1]

        # component.ref (optional)
        ref = component.get("ref")
        if ref is not None:
            if not isinstance(ref, str) or not ref.strip():
                raise ManifestError("component.ref must be a non-empty string")

        # component.base_image (optional)
        base_image = component.get("base_image")
        if base_image is not None:
            if not isinstance(base_image, dict):
                raise ManifestError("component.base_image must be a mapping")
            for f in ("hub", "name"):
                if f not in base_image:
                    raise ManifestError(f"Missing required field: component.base_image.{f}")

        # component.build (optional)
        build = component.get("build")
        if build is not None:
            if not isinstance(build, dict):
                raise ManifestError("component.build must be a mapping")
            build.setdefault("commands", [])
            cmds = build["commands"]
            if not isinstance(cmds, list):
                raise ManifestError("component.build.commands must be a list")
            build_image = build.get("image")
            if build_image is not None:
                if not isinstance(build_image, dict):
                    raise ManifestError("component.build.image must be a mapping")
                if base_image is not None:
                    build_image.setdefault("hub", base_image["hub"])
                    build_image.setdefault("name", base_image["name"])
                if "hub" not in build_image:
                    raise ManifestError("Missing required field: component.build.image.hub")

    # pipeline (optional, defaults applied)
    pipeline = data.get("pipeline")
    if pipeline is None:
        data["pipeline"] = {"name": "sim2real", "yaml": "pipeline/pipeline.yaml"}
    elif not isinstance(pipeline, dict):
        raise ManifestError("pipeline must be a mapping")
    else:
        pipeline.setdefault("name", "sim2real")
        pipeline.setdefault("yaml", "pipeline/pipeline.yaml")
    pipeline = data["pipeline"]
    if not isinstance(pipeline["name"], str) or not pipeline["name"].strip():
        raise ManifestError("pipeline.name must be a non-empty string")
    if not isinstance(pipeline["yaml"], str) or not pipeline["yaml"].strip():
        raise ManifestError("pipeline.yaml must be a non-empty string")
    if Path(pipeline["yaml"]).is_absolute():
        raise ManifestError(
            f"pipeline.yaml must be a relative path, got: {pipeline['yaml']}"
        )

