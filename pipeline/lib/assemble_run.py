"""Sim2real assemble command: pure logic behind `sim2real assemble`.

Reads a registered translation and an experiment repo's ``transfer.yaml``,
snapshots the assembly-slice into ``runs/<R>/manifest.assembly.yaml``,
deep-merges baseline + treatment scenarios (framework defaults → baseline
bundle → per-algorithm overlay), generates one PipelineRun per
(workload, package), and writes ``run_metadata.json`` with a stable
``params_hash`` over the assembly-slice bytes.

Pure module: no argparse, no print. Callers surface errors via the
``AssembleError`` exception.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import yaml

from pipeline.lib import cluster_ops, layout, slicer
from pipeline.lib.manifest import ManifestError, load_manifest
from pipeline.lib.tekton import make_pipelinerun_scenario
from pipeline.lib.values import deep_merge


class AssembleError(Exception):
    """Raised when assembly fails validation."""


def filter_algorithms(
    manifest_algos: list[dict],
    *,
    translated_names: set[str],
) -> tuple[list[dict], list[str]]:
    """Split ``manifest_algos`` by whether each name is in ``translated_names``.

    Returns ``(kept, skipped_names)`` where ``kept`` preserves manifest
    order and ``skipped_names`` lists names present in the manifest but
    absent from ``translated_names``. Callers surface the skipped set as a
    warning; the design lets us prune unregistered algorithms without
    failing the run.
    """
    kept: list[dict] = []
    skipped: list[str] = []
    for algo in manifest_algos:
        name = algo.get("name")
        if name in translated_names:
            kept.append(algo)
        else:
            skipped.append(name)
    return kept, skipped


def _load_yaml(path: Path) -> dict:
    """Load a YAML file into a dict; raise AssembleError on I/O or parse error."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise AssembleError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise AssembleError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AssembleError(
            f"expected YAML mapping at {path}, got {type(data).__name__}"
        )
    return data


def load_defaults_overlay(defaults_dir: Path | None, *, disable: list[str]) -> dict:
    """Merge framework-defaults YAML fragments into one overlay.

    Fragments live under ``defaults_dir`` (typically
    ``<experiment-root>/baselines/defaults/``). Their stems (filename
    without ``.yaml``) act as opt-out keys — any stem in ``disable`` is
    skipped. Returns ``{}`` when ``defaults_dir`` is None or missing.
    Fragments merge in filename-sorted order for determinism.
    """
    if defaults_dir is None or not defaults_dir.exists():
        return {}
    disable_set = set(disable or [])
    merged: dict = {}
    for fragment in sorted(defaults_dir.glob("*.yaml")):
        if fragment.stem in disable_set:
            continue
        merged = deep_merge(merged, _load_yaml(fragment))
    return merged


def inject_image_tag(scenario_dict: dict, image_ref: str) -> None:
    """Inject BYO image into every scenario entry's ``images.inferenceScheduler``.

    Splits a ``registry/repo:tag`` ref on the last colon into ``repository``
    and ``tag``; digest refs (``registry/repo@sha256:...``) keep the whole
    ref as ``repository`` with ``tag=""``. ``pullPolicy`` is always set to
    ``Always`` — mirrors the semantics of
    ``pipeline/lib/epp.py:inject_epp_image`` so downstream benchmark
    charts see a familiar shape.
    """
    scenario_list = scenario_dict.get("scenario")
    if not scenario_list:
        raise AssembleError(
            "cannot inject image_tag: scenario dict has no 'scenario' entries"
        )
    if "@sha256:" in image_ref:
        repository, tag = image_ref, ""
    else:
        # rsplit on the last "/" isolates the registry:port/path portion so
        # only a trailing "repo:tag" colon splits — never a registry-port colon.
        if ":" in image_ref.rsplit("/", 1)[-1]:
            repository, tag = image_ref.rsplit(":", 1)
        else:
            repository, tag = image_ref, ""
    for entry in scenario_list:
        entry["images"] = entry.get("images") or {}
        entry["images"]["inferenceScheduler"] = {
            "repository": repository,
            "tag": tag,
            "pullPolicy": "Always",
        }


def inject_hf_secret_name(scenario_dict: dict, hf_secret_name: str) -> None:
    """Set ``huggingface.secretName`` on every scenario entry.

    Does not overwrite an explicitly set secretName (setdefault semantics).
    Raises AssembleError when the scenario dict has no ``scenario`` entries.
    """
    scenario_list = scenario_dict.get("scenario")
    if not scenario_list:
        raise AssembleError(
            "cannot inject hf secret: scenario dict has no 'scenario' entries"
        )
    for entry in scenario_list:
        hf = entry.setdefault("huggingface", {})
        hf.setdefault("secretName", hf_secret_name)


def write_manifest_assembly(
    run_dir: Path, manifest: dict, *, now_iso: str
) -> Path:
    """Serialize ``slicer.assembly_slice(manifest)`` to ``manifest.assembly.yaml``.

    Prepends a one-line comment header naming the tool and timestamp.
    Returns the written path.
    """
    slice_ = slicer.assembly_slice(manifest)
    body = yaml.dump(
        slice_, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    text = f"# generated by sim2real assemble at {now_iso}; do not edit\n" + body
    out = run_dir / "manifest.assembly.yaml"
    out.write_text(text)
    return out


def compute_params_hash(manifest_assembly_path: Path) -> str:
    """SHA-256 over the raw bytes of ``manifest.assembly.yaml``."""
    return hashlib.sha256(manifest_assembly_path.read_bytes()).hexdigest()


def write_run_metadata(run_dir: Path, meta: dict) -> Path:
    """Write ``runs/<R>/run_metadata.json`` from ``meta`` (v1 schema).

    Caller supplies all fields — this function only serializes. Deterministic
    key order (``sort_keys=True``) so re-runs against unchanged inputs
    produce byte-identical files.
    """
    out = run_dir / "run_metadata.json"
    out.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return out


def resolve_baseline(
    *,
    bundle_path: Path,
    overlay_path: Path | None,
    framework_defaults: dict,
) -> dict:
    """Return ``deep_merge(framework_defaults, bundle, overlay)`` for a baseline.

    ``framework_defaults`` may be ``{}`` (experiment has no
    ``baselines/defaults/`` directory). ``overlay_path`` may be ``None`` or
    point at a non-existent file (BYO baseline without a baseline overlay).
    Bundle is required — a missing bundle raises AssembleError.
    """
    if not bundle_path.exists():
        raise AssembleError(f"baseline scenario not found: {bundle_path}")
    bundle = _load_yaml(bundle_path)
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
    resolved = deep_merge(copy.deepcopy(framework_defaults), bundle)
    resolved = deep_merge(resolved, overlay)
    return resolved


def resolve_treatment(
    *,
    baseline_resolved: dict,
    diffs_path: Path | None,
    overlay_path: Path | None,
) -> dict:
    """Return ``deep_merge(baseline_resolved, treatment_diffs, algo_overlay)``.

    Either or both of ``diffs_path`` / ``overlay_path`` may be ``None`` or
    point at non-existent files — the corresponding layer is treated as
    empty. Baseline is required (starts from an already-resolved dict).
    """
    diffs = (
        _load_yaml(diffs_path)
        if diffs_path is not None and diffs_path.exists()
        else {}
    )
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
    resolved = deep_merge(copy.deepcopy(baseline_resolved), diffs)
    resolved = deep_merge(resolved, overlay)
    return resolved


def write_resolved_scenarios(
    run_dir: Path, packages: list[tuple[str, dict]]
) -> Path:
    """Write each ``(name, resolved_dict)`` pair to ``runs/<R>/cluster/<name>.yaml``.

    Returns the cluster directory path. Creates it if absent.
    """
    cluster_dir_ = run_dir / "cluster"
    cluster_dir_.mkdir(parents=True, exist_ok=True)
    for name, resolved in packages:
        (cluster_dir_ / f"{name}.yaml").write_text(
            yaml.dump(resolved, default_flow_style=False, allow_unicode=True)
        )
    return cluster_dir_


def generate_pipelineruns(
    *,
    run_dir: Path,
    packages: list[tuple[str, dict]],
    workloads: list[dict],
    run_name: str,
    cluster_config: dict,
    pipeline_name: str,
    observe: dict,
    model_name: str,
    submodule_shas: dict,
    submodule_urls: dict,
) -> None:
    """Emit one PipelineRun YAML per (workload, package) pair under ``cluster/``.

    Filename shape: ``pipelinerun-<workload-safe>-<package>.yaml``, where
    ``<workload-safe>`` is the workload name with ``_`` replaced by ``-``.
    Matches the shape that ``deploy.py run``'s pair-discovery expects.
    """
    cluster_dir_ = run_dir / "cluster"
    cluster_dir_.mkdir(parents=True, exist_ok=True)

    namespaces = cluster_config.get("namespaces") or []
    namespace = namespaces[0] if namespaces else "default"
    ws_bindings = cluster_config.get("workspaces") or {}

    for pkg_name, resolved in packages:
        scenario_content = yaml.dump(
            resolved, default_flow_style=False, allow_unicode=True
        )
        for wl in workloads:
            wl_name = wl.get("name", wl.get("workload_name", "unknown"))
            safe_wl = wl_name.replace("_", "-")
            pr = make_pipelinerun_scenario(
                phase=pkg_name,
                workload=wl,
                run_name=run_name,
                namespace=namespace,
                pipeline_name=pipeline_name,
                scenario_content=scenario_content,
                workspace_bindings=ws_bindings if ws_bindings else None,
                benchmark_git_commit=submodule_shas.get("llm-d-benchmark", ""),
                benchmark_git_repo_url=submodule_urls.get("llm-d-benchmark", ""),
                blis_git_commit=submodule_shas.get("inference-sim", ""),
                blis_git_repo_url=submodule_urls.get("inference-sim", ""),
                model=model_name,
                observe=observe,
            )
            (cluster_dir_ / f"pipelinerun-{safe_wl}-{pkg_name}.yaml").write_text(
                yaml.dump(pr, default_flow_style=False, allow_unicode=True)
            )


def _load_workload(exp_root: Path, wl_path_str: str) -> dict:
    """Load a workload YAML relative to the experiment root."""
    wl_path = exp_root / wl_path_str
    if not wl_path.exists():
        raise AssembleError(f"workload file not found: {wl_path}")
    try:
        data = yaml.safe_load(wl_path.read_text())
    except yaml.YAMLError as exc:
        raise AssembleError(f"invalid YAML in workload {wl_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AssembleError(f"workload {wl_path} is not a YAML mapping")
    if "name" not in data and "workload_name" not in data:
        data["workload_name"] = Path(wl_path_str).stem
    return data


def _resolve_scenario_path(
    exp_root: Path, scenario_ref: str | None, fallback_name: str
) -> Path | None:
    """Return the experiment-root-relative path for a scenario reference.

    ``scenario_ref`` is what the manifest recorded (may be a path, ``null``,
    or absent). ``fallback_name`` is the top-level filename to try when the
    manifest omits the reference. Returns ``None`` when neither exists so
    callers can treat that layer as empty.
    """
    if scenario_ref:
        return exp_root / scenario_ref
    fallback = exp_root / fallback_name
    return fallback if fallback.exists() else None


def assemble_run(
    *,
    translation_hash: str,
    cluster_id: str,
    run_name: str,
    experiment_root: Path,
    manifest_path: Path,
    force: bool,
    now_iso: str,
) -> None:
    """Materialize ``workspace/runs/<run_name>/`` per the design.

    Steps (per design §Commands → sim2real assemble):
      1. Validate: translation dir + cluster_config exist; run_dir absent or
         --force.
      2. Load manifest; filter algorithms to those in translation_output.json.
      3. Snapshot assembly-slice → manifest.assembly.yaml; compute params_hash.
      4. Resolve baseline (framework_defaults → bundle → baseline_overlay) and
         each treatment (baseline_resolved → treatment diffs → per-algo overlay).
      5. Inject image_tag into treatment scenarios; inject huggingface.secretName
         into all scenarios.
      6. Write cluster/{package}.yaml files.
      7. Generate cluster/pipelinerun-*.yaml files.
      8. Write run_metadata.json.

    Raises AssembleError on any validation failure. Validation happens
    before ``run_dir`` is (re)created — no partial writes on failure.

    The list of algorithms present in the manifest but absent from the
    registered translation is stored on ``assemble_run.skipped_algorithms``
    for the CLI wrapper to surface as warnings.
    """
    layout.set_experiment_root(experiment_root)
    # Reset side-band state each call — see docstring above.
    assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]

    # 1. Validation --------------------------------------------------------
    tdir = layout.translation_dir(translation_hash)
    tout_path = layout.translation_output_path(translation_hash)
    if not tdir.exists() or not tout_path.exists():
        raise AssembleError(
            f"translation directory not found: {tdir}. "
            "Register a translation with `sim2real translation register` first."
        )

    cluster_config = cluster_ops.read_cluster_config(cluster_id)
    if not cluster_config:
        raise AssembleError(
            f"cluster config not found for '{cluster_id}': "
            f"{layout.cluster_config_path(cluster_id)}"
        )

    run_dir = layout.runs_dir() / run_name
    if run_dir.exists():
        if not force:
            raise AssembleError(
                f"run directory already exists: {run_dir} — pass --force to overwrite"
            )
        shutil.rmtree(run_dir)

    # 2. Load manifest + translation index --------------------------------
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        raise AssembleError(f"cannot load manifest {manifest_path}: {exc}") from exc

    try:
        tout = json.loads(tout_path.read_text())
    except json.JSONDecodeError as exc:
        raise AssembleError(
            f"translation_output.json is not valid JSON: {tout_path}: {exc}"
        ) from exc

    translated_names = {a.get("name") for a in tout.get("algorithms", [])}
    image_ref = tout.get("image_ref")
    if not image_ref:
        raise AssembleError(
            f"translation_output.json missing image_ref: {tout_path}"
        )

    kept_algos, skipped_algo_names = filter_algorithms(
        manifest.get("algorithms", []) or [],
        translated_names=translated_names,
    )

    # 3. Snapshot assembly slice + params_hash ----------------------------
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_assembly_path = write_manifest_assembly(
        run_dir, manifest, now_iso=now_iso
    )
    params_hash = compute_params_hash(manifest_assembly_path)

    # 4. Resolve scenarios ------------------------------------------------
    exp_root = layout.experiment_root()
    defaults_dir = exp_root / "baselines" / "defaults"
    framework_defaults = load_defaults_overlay(
        defaults_dir if defaults_dir.exists() else None,
        disable=(manifest.get("defaults") or {}).get("disable") or [],
    )
    generated_root = tdir / "generated"
    baseline_overlay_path = generated_root / "baseline_config.yaml"

    packages: list[tuple[str, dict]] = []
    resolved_baselines: dict[str, dict] = {}
    for bl in manifest.get("baselines", []):
        bl_name = bl["name"]
        bundle_path = _resolve_scenario_path(
            exp_root, bl.get("scenario"), "baseline.yaml"
        )
        if bundle_path is None:
            raise AssembleError(f"baseline '{bl_name}' has no scenario file")
        resolved = resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=baseline_overlay_path,
            framework_defaults=framework_defaults,
        )
        resolved_baselines[bl_name] = resolved
        packages.append((bl_name, resolved))

    for algo in kept_algos:
        algo_name = algo["name"]
        base_name = algo["defaults"]
        if base_name not in resolved_baselines:
            raise AssembleError(
                f"algorithm '{algo_name}' references unknown baseline "
                f"'{base_name}'; known: {sorted(resolved_baselines)}"
            )
        diffs_path = _resolve_scenario_path(
            exp_root, algo.get("scenario"), "treatment.yaml"
        )
        overlay_path = generated_root / algo_name / f"{algo_name}_config.yaml"
        resolved = resolve_treatment(
            baseline_resolved=resolved_baselines[base_name],
            diffs_path=diffs_path,
            overlay_path=overlay_path,
        )
        inject_image_tag(resolved, image_ref)
        packages.append((algo_name, resolved))

    # 5. Inject hf secret on every package --------------------------------
    hf_secret = (cluster_config.get("secret_names") or {}).get(
        "hf_token", "hf-secret"
    )
    for _, resolved in packages:
        inject_hf_secret_name(resolved, hf_secret)

    # 6. Write scenario YAMLs ---------------------------------------------
    write_resolved_scenarios(run_dir, packages)

    # 7. Generate PipelineRuns --------------------------------------------
    workloads = [_load_workload(exp_root, wl) for wl in manifest.get("workloads", [])]
    pipeline_name = (manifest.get("pipeline") or {}).get("name", "sim2real")
    observe = manifest.get("blis_observe") or {}
    # Model name derives from the first baseline package (matches prior behavior).
    first_baseline = next(
        (resolved for name, resolved in packages if name in resolved_baselines),
        packages[0][1] if packages else {},
    )
    scenarios_list = first_baseline.get("scenario", [])
    model_name = (
        scenarios_list[0].get("model", {}).get("name", "") if scenarios_list else ""
    )

    generate_pipelineruns(
        run_dir=run_dir,
        packages=packages,
        workloads=workloads,
        run_name=run_name,
        cluster_config=cluster_config,
        pipeline_name=pipeline_name,
        observe=observe,
        model_name=model_name,
        # Step-1 does not read git submodule state — PR 3 will. Empty strings
        # accepted by tekton.make_pipelinerun_scenario; downstream chart handles
        # the absence.
        submodule_shas={},
        submodule_urls={},
    )

    # 8. Write run_metadata.json ------------------------------------------
    write_run_metadata(
        run_dir,
        {
            "version": 1,
            "run_name": run_name,
            "translation_hash": translation_hash,
            "cluster_id": cluster_id,
            "params_hash": params_hash,
            "image_tag": image_ref,
            "assembled_at": now_iso,
        },
    )
    # Skipped-algorithm list exposed for the CLI wrapper to surface as warnings.
    assemble_run.skipped_algorithms = skipped_algo_names  # type: ignore[attr-defined]


# Initialize the side-band attribute so `getattr` in the CLI works on first call.
assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
