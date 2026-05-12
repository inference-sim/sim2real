"""Manifest loader for sim2real pipeline (v3 schema)."""
import re
import warnings
import yaml
from pathlib import Path


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
            for f in ("name", "source", "defaults"):
                if f not in entry:
                    raise ManifestError(f"algorithms[{i}] missing required field: {f}")
            _validate_package_name(entry["name"], f"algorithms[{i}]")
            if entry["name"] in seen_algo_names:
                raise ManifestError(f"algorithms: duplicate name '{entry['name']}'")
            seen_algo_names.add(entry["name"])
    else:
        data["algorithms"] = []

    # Cross-collision check: all package names must be globally unique
    all_names = [bl["name"] for bl in data["baselines"]] + [a["name"] for a in data["algorithms"]]
    if len(all_names) != len(set(all_names)):
        dupes = [n for n in all_names if all_names.count(n) > 1]
        raise ManifestError(
            f"Package names must be globally unique across baselines and algorithms; "
            f"duplicates: {sorted(set(dupes))}"
        )

    # Hints section (optional)
    hints_raw = data.get("hints", {}) or {}
    hints_text = hints_raw.get("text", "") or ""
    hints_files_raw = hints_raw.get("files", []) or []
    hints_files = []
    for fpath in hints_files_raw:
        fp = Path(fpath)
        if not fp.exists():
            raise ManifestError(f"hints.files entry not found: {fpath}")
        hints_files.append({"path": str(fp), "content": fp.read_text()})
    data["hints"] = {"text": hints_text, "files": hints_files}

    _validate_v3_fields(data)

    # Deprecation warning for context.notes
    if data.get("context", {}).get("notes"):
        warnings.warn(
            "context.notes is deprecated; use hints.text instead. "
            "The value is currently ignored.",
            DeprecationWarning,
            stacklevel=2,
        )

    return data


def _validate_v3_fields(data: dict) -> None:
    """Validate and apply defaults for v3-specific fields."""

    # target (required)
    target = data.get("target")
    if target is None:
        raise ManifestError("Missing required field: target")
    if not isinstance(target, dict):
        raise ManifestError("target must be a mapping")
    if "repo" not in target:
        raise ManifestError("Missing required field: target.repo")

    # config (required)
    config = data.get("config")
    if config is None:
        raise ManifestError("Missing required field: config")
    if not isinstance(config, dict):
        raise ManifestError("config must be a mapping")
    if "kind" not in config:
        raise ManifestError("Missing required field: config.kind")

    # build (optional, defaults applied)
    build = data.get("build")
    if build is None:
        data["build"] = {"commands": []}
    elif not isinstance(build, dict):
        raise ManifestError("build must be a mapping")
    else:
        build.setdefault("commands", [])
        cmds = build["commands"]
        if not isinstance(cmds, list):
            raise ManifestError("build.commands must be a list")

    # epp_image (optional)
    epp = data.get("epp_image")
    if epp is not None:
        if not isinstance(epp, dict):
            raise ManifestError("epp_image must be a mapping")
        upstream = epp.get("upstream")
        if upstream is None:
            raise ManifestError("Missing required field: epp_image.upstream")
        if not isinstance(upstream, dict):
            raise ManifestError("epp_image.upstream must be a mapping")
        for f in ("hub", "name", "tag"):
            if f not in upstream:
                raise ManifestError(f"Missing required field: epp_image.upstream.{f}")
        build_img = epp.get("build")
        if build_img is not None:
            if not isinstance(build_img, dict):
                raise ManifestError("epp_image.build must be a mapping")
            for f in ("hub", "name", "tag"):
                if f not in build_img:
                    raise ManifestError(f"Missing required field: epp_image.build.{f}")

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
