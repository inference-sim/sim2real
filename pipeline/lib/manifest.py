"""Manifest loader for sim2real pipeline (v2 schema)."""
import warnings
import yaml
from pathlib import Path


class ManifestError(Exception):
    """Manifest validation error."""


_REQUIRED_TOP = ["kind", "version", "scenario", "algorithm", "baseline"]
_REQUIRED_ALGORITHM = ["source"]


def load_manifest(path: "Path | str") -> dict:
    """Load and validate a v2 sim2real transfer manifest."""
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
    if version == 1:
        raise ManifestError(
            "This is a v1 manifest. v2 is required.\n"
            "Migration: rename algorithm.policy \u2192 algorithm.config, "
            "add scenario field, move target/config to env_defaults.yaml.\n"
            "See docs/transfer/migration-v1-to-v2.md for details."
        )
    if version not in (2, 3):
        raise ManifestError(f"Unsupported manifest version: {version}")

    for field in _REQUIRED_TOP:
        if field not in data:
            raise ManifestError(f"Missing required field: {field}")

    algo = data["algorithm"]
    if not isinstance(algo, dict):
        raise ManifestError("algorithm must be a mapping")
    for f in _REQUIRED_ALGORITHM:
        if f not in algo:
            raise ManifestError(f"Missing required field: algorithm.{f}")

    bl = data["baseline"]
    if not isinstance(bl, dict):
        raise ManifestError("baseline must be a mapping")

    if version == 2:
        # v2: flat baseline.config — normalize to v3 shape for uniform downstream access
        if "config" not in bl:
            raise ManifestError("Missing required field: baseline.config")
        data["baseline"] = {
            "sim": {"config": bl["config"]},
            "real": {"config": None, "notes": ""},
        }
    else:
        # v3: baseline.sim.config optional (None when scenario has no sim baseline);
        # baseline.real.* optional with defaults
        sim_section = bl.get("sim")
        if sim_section is None:
            data["baseline"]["sim"] = {"config": None}
        elif not isinstance(sim_section, dict):
            raise ManifestError("baseline.sim must be a mapping")
        else:
            sim_section.setdefault("config", None)
        real_section = bl.get("real")
        if real_section is None:
            data["baseline"]["real"] = {"config": None, "notes": ""}
        elif not isinstance(real_section, dict):
            raise ManifestError("baseline.real must be a mapping")
        else:
            data["baseline"]["real"].setdefault("config", None)
            data["baseline"]["real"].setdefault("notes", "")

    # Normalize workloads: absent/null → [] (standby mode — stack up, no benchmarks)
    wl = data.get("workloads")
    if wl is None:
        data["workloads"] = []
    elif not isinstance(wl, list):
        raise ManifestError("workloads must be a list")

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

    # ── v3 fields (migrated from env_defaults.yaml) ────────────────────────────
    if version >= 3:
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
    """Validate and apply defaults for v3 fields migrated from env_defaults.yaml."""

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
    if "helm_path" not in config:
        raise ManifestError("Missing required field: config.helm_path")

    # observe (optional, defaults applied)
    observe = data.get("observe")
    if observe is None:
        data["observe"] = {"request_multiplier": 1}
    elif not isinstance(observe, dict):
        raise ManifestError("observe must be a mapping")
    else:
        observe.setdefault("request_multiplier", 1)

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
