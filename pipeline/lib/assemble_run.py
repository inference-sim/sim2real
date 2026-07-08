"""Sim2real assemble command: pure logic behind `sim2real assemble`.

Reads a registered translation and an experiment repo's ``transfer.yaml``,
snapshots the assembly-slice into ``runs/<R>/manifest.assembly.yaml``,
deep-merges baseline + treatment scenarios (framework defaults → baseline
bundle → per-algorithm overlay), generates one PipelineRun per
(workload, package, iteration) tuple, and writes ``run_metadata.json`` with
a stable ``params_hash`` over the assembly-slice bytes (with the top-level
``replicas`` field excluded).

Pure module: no argparse, no print. Callers surface errors via the
``AssembleError`` exception.
"""

from __future__ import annotations

import configparser
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import yaml

from pipeline.lib import cluster_ops, layout, slicer, translation_ref as _translation_ref
from pipeline.lib.manifest import ManifestError, load_manifest
from pipeline.lib.tekton import make_pipelinerun_scenario
from pipeline.lib.values import deep_merge


# Framework repo root — three levels up from pipeline/lib/assemble_run.py.
# Mirrors pipeline/lib/cluster_ops.py:_REPO_ROOT. Used to locate framework
# submodules (inference-sim, llm-d-benchmark), which always live in the
# framework repo — NOT in the experiment repo.
_REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent


# Framework submodule pair — pinned. These names appear in the PipelineRun
# spec's benchmarkGit*/blisGit* params, and the cluster-side pipeline
# clones them by URL and checks out the recorded SHA. The component
# submodule (tracked by ``manifest["component"]["path"]``) is deliberately
# out of scope: the component image reference comes from the registered
# translation, not from a git ref.
_FRAMEWORK_SUBMODULE_NAMES: tuple[str, ...] = ("inference-sim", "llm-d-benchmark")


def discover_framework_submodules(
    repo_root: Path,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Read framework submodule state from ``repo_root``.

    Returns ``(shas, urls, missing)``:

    - ``shas``: ``{name: sha}`` for each framework submodule. Value is
      ``"unknown"`` when the directory is absent or the SHA lookup fails.
      Callers pass this through to the PipelineRun spec verbatim; the
      cluster-side clone step fails visibly on ``"unknown"``, which is
      the intended posture — assemble succeeds locally so the operator
      can inspect the run, cluster fails at the right step.
    - ``urls``: ``{name: url}`` for every framework submodule, sourced
      from ``<repo_root>/.gitmodules``. Value is ``""`` when
      ``.gitmodules`` is absent or has no entry for that name. URL
      discovery is declarative and does not depend on the submodule
      directory being populated.
    - ``missing``: sorted list of framework submodule names whose
      directory does not exist under ``repo_root``. The CLI wrapper
      surfaces this as an operator warning via the side-band
      ``missing_submodules`` attr.

    ``repo_root`` is the framework repo root, not the experiment root.
    """
    shas: dict[str, str] = {}
    missing: list[str] = []
    for name in _FRAMEWORK_SUBMODULE_NAMES:
        sub = repo_root / name
        if not sub.exists() or not (sub / ".git").exists():
            missing.append(name)
            shas[name] = "unknown"
            continue
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=sub,
                capture_output=True,
                text=True,
                check=True,
            )
            shas[name] = result.stdout.strip() or "unknown"
        except (subprocess.CalledProcessError, OSError):
            shas[name] = "unknown"

    urls: dict[str, str] = {name: "" for name in _FRAMEWORK_SUBMODULE_NAMES}
    gitmodules_path = repo_root / ".gitmodules"
    if gitmodules_path.exists():
        parser = configparser.ConfigParser()
        try:
            parser.read(gitmodules_path)
        except configparser.Error:
            # Corrupt .gitmodules — leave urls empty; missing already
            # reflects any absent-on-disk submodules.
            return shas, urls, sorted(missing)
        for section in parser.sections():
            # Sections look like: submodule "<name>"
            if not (section.startswith('submodule "') and section.endswith('"')):
                continue
            name = section[len('submodule "'):-1]
            if name not in _FRAMEWORK_SUBMODULE_NAMES:
                continue
            urls[name] = parser.get(section, "url", fallback="")

    return shas, urls, sorted(missing)


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
    """Inject BYO image into every scenario entry's ``router.epp.image``.

    Splits a ``registry/repo:tag`` ref on the last colon into a
    full-path repository and tag, then splits the repository at the
    last ``/`` into ``registry`` and bare-repository fields so the
    llm-d-router chart can render ``{registry}/{repository}:{tag}``
    directly. Digest refs (``registry/repo@sha256:...``) are split at
    the last ``/`` the same way as tag refs — the ``@sha256:...``
    suffix stays attached to the bare repository component — but
    ``tag`` is always ``""`` for digests. Refs with no ``/`` (bare
    image names) yield ``registry=""`` and ``repository=<full-ref>``.
    ``pullPolicy`` is always set to ``Always`` — mirrors the
    semantics of ``pipeline/lib/epp.py:inject_epp_image`` so
    downstream benchmark charts see a familiar shape.
    """
    scenario_list = scenario_dict.get("scenario")
    if not scenario_list:
        raise AssembleError(
            "cannot inject image_tag: scenario dict has no 'scenario' entries"
        )
    if "@sha256:" in image_ref:
        full_repository, tag = image_ref, ""
    else:
        # rsplit on the last "/" isolates the registry:port/path portion so
        # only a trailing "repo:tag" colon splits — never a registry-port colon.
        if ":" in image_ref.rsplit("/", 1)[-1]:
            full_repository, tag = image_ref.rsplit(":", 1)
        else:
            full_repository, tag = image_ref, ""
    if "/" in full_repository:
        registry, bare_repository = full_repository.rsplit("/", 1)
    else:
        registry, bare_repository = "", full_repository
    for entry in scenario_list:
        entry.setdefault("router", {}).setdefault("epp", {})["image"] = {
            "registry": registry,
            "repository": bare_repository,
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
    run_dir: Path, manifest: dict, *, now_iso: str, replicas: int = 1,
) -> Path:
    """Serialize ``slicer.assembly_slice(manifest)`` + ``replicas: N`` to
    ``manifest.assembly.yaml``.

    Prepends a one-line comment header naming the tool and timestamp.
    Returns the written path.
    """
    slice_ = slicer.assembly_slice(manifest)
    # Emit replicas at the top of the file for human readability, before the
    # rest of the assembly slice.
    out_dict = {"replicas": replicas, **slice_}
    body = yaml.dump(
        out_dict, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    text = f"# generated by sim2real assemble at {now_iso}; do not edit\n" + body
    out = run_dir / "manifest.assembly.yaml"
    out.write_text(text)
    return out


def compute_params_hash(manifest_assembly_path: Path) -> str:
    """SHA-256 over the canonical assembly slice, with ``replicas`` excluded.

    Excluding ``replicas`` is deliberate: bumping ``--replicas N`` must not
    trip drift detection on re-assemble. Canonical form uses
    ``sort_keys=True`` so the hash is deterministic across YAML formatter
    ordering differences.
    """
    data = yaml.safe_load(manifest_assembly_path.read_text()) or {}
    if isinstance(data, dict):
        data.pop("replicas", None)
    canonical = yaml.dump(
        data, sort_keys=True, default_flow_style=False, allow_unicode=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_run_metadata(run_dir: Path, meta: dict) -> Path:
    """Write ``runs/<R>/run_metadata.json`` from ``meta`` (v1 schema).

    Caller supplies all fields — this function only serializes. Deterministic
    key order (``sort_keys=True``) so re-runs against unchanged inputs
    produce byte-identical files.
    """
    out = run_dir / "run_metadata.json"
    out.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return out


def _align_overlay_name(base: dict, overlay: dict) -> dict:
    """Realign overlay's ``scenario[0].name`` to match base to prevent list-merge duplication.

    The ``deep_merge`` list strategy in ``pipeline/lib/values.py`` picks ``name``
    as the merge key when every entry on both sides carries it, then merges list
    items by identity on that key. Framework-default fragments under
    ``baselines/defaults/*.yaml`` are authored as ``scenario: - name: defaults``
    (a placeholder — see each fragment's top-of-file comment "scenario[0].name
    is realigned to the experiment baseline at merge time"). If the baseline
    entry is named anything other than ``defaults`` (i.e. every real experiment),
    a naive merge would append the defaults content as a second, unwanted
    scenario entry — and llm-d-benchmark's templater would then render it as a
    separate deployment carrying the framework-default model
    (``facebook/opt-125m``) instead of the intended model. See issue #516.

    Copies the overlay's first scenario name from the base's first scenario name
    when both are non-empty and differ. Mutates and returns ``overlay``.

    Ported verbatim from ``pipeline/lib/assemble.py:_align_overlay_name`` on
    ``main`` — dropped by step-2's ``assemble.py`` → ``assemble_run.py`` rename.
    """
    base_scenarios = base.get("scenario", [])
    overlay_scenarios = overlay.get("scenario", [])
    if base_scenarios and overlay_scenarios:
        base_name = base_scenarios[0].get("name", "")
        if base_name and overlay_scenarios[0].get("name", "") != base_name:
            overlay_scenarios[0]["name"] = base_name
    return overlay


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

    Before the merge, ``framework_defaults[scenario][0].name`` is realigned
    to the bundle's ``scenario[0].name`` so both sides collapse into a single
    scenario entry (see ``_align_overlay_name``). Without this, a defaults
    overlay named ``defaults`` and a baseline named anything else produce two
    scenario entries — an intended one plus a phantom one that inherits the
    llm-d-benchmark framework-default model (issue #516).
    """
    if not bundle_path.exists():
        raise AssembleError(f"baseline scenario not found: {bundle_path}")
    bundle = _load_yaml(bundle_path)
    overlay = (
        _load_yaml(overlay_path)
        if overlay_path is not None and overlay_path.exists()
        else {}
    )
    aligned_defaults = _align_overlay_name(bundle, copy.deepcopy(framework_defaults))
    resolved = deep_merge(aligned_defaults, bundle)
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
    iterations: "range | list[int]" = range(1, 2),
) -> None:
    """Emit one PipelineRun YAML per (workload, package, iteration) tuple
    under ``cluster/``.

    Filename shape: ``pipelinerun-<workload-safe>|<package>|i<N>.yaml``,
    where ``<workload-safe>`` is the workload name with ``_`` replaced by
    ``-`` and ``N`` is each element of ``iterations``. The ``|`` separators
    make the derived pair key match the canonical grammar in
    ``pipeline/lib/pairkey.py``.
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
            for iteration in iterations:
                try:
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
                        iteration=iteration,
                    )
                except ValueError as exc:
                    raise AssembleError(str(exc)) from exc
                fname = f"pipelinerun-{safe_wl}|{pkg_name}|i{iteration}.yaml"
                (cluster_dir_ / fname).write_text(
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


class _ResolvedPackages(NamedTuple):
    """Return value of ``_resolve_packages``.

    Holds everything both the fresh-assemble path and the additive-grow path
    need to build PipelineRun files and update run metadata. The fields
    match the local names both callers previously computed inline.
    """
    packages: list[tuple[str, dict]]
    resolved_baselines: dict[str, dict]
    kept_algos: list[dict]
    skipped_algo_names: list[str]
    translated_algos: dict[str, dict]
    workloads: list[dict]
    model_name: str
    submodule_shas: dict[str, str]
    submodule_urls: dict[str, str]
    missing_submodules: list[str]


def _resolve_packages(
    manifest: dict,
    *,
    exp_root: Path,
    translation_dir: Path,
    tout_path: Path,
    cluster_config: dict,
    translation_ref: str,
) -> _ResolvedPackages:
    """Shared resolution pipeline for both fresh assemble and additive grow.

    Loads framework defaults, reads translation output (wrapped so parse
    errors surface as ``AssembleError``), filters algorithms against the
    translation, resolves baseline + treatment scenarios (with the
    ``bundle_path is None`` and ``base_name not in resolved_baselines``
    guards that the fresh path historically owned), refuses if any kept
    algorithm is unbuilt, injects image tags + HF secret, loads workloads,
    derives the model name, and discovers framework submodules.

    Every failure mode raises ``AssembleError``. Callers get a fully
    populated ``_ResolvedPackages`` on success.
    """
    defaults_dir = exp_root / "baselines" / "defaults"
    framework_defaults = load_defaults_overlay(
        defaults_dir if defaults_dir.exists() else None,
        disable=(manifest.get("defaults") or {}).get("disable") or [],
    )

    try:
        tout = _translation_ref.read_translation_output(tout_path)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AssembleError(
            f"translation_output.json is not valid JSON: {tout_path}: {exc}"
        ) from exc

    translated_algos = {
        a.get("name"): a for a in tout.get("algorithms", []) or []
    }
    translated_names = set(translated_algos.keys())
    kept_algos, skipped_algo_names = filter_algorithms(
        manifest.get("algorithms", []) or [],
        translated_names=translated_names,
    )

    # Incomplete-translation check: refuse before we would call
    # ``inject_image_tag`` with a null image_ref (which would crash the
    # helper with TypeError, escaping the AssembleError boundary).
    unbuilt = [
        a["name"] for a in kept_algos
        if not translated_algos[a["name"]].get("image_ref")
    ]
    if unbuilt:
        raise AssembleError(
            f"translation {translation_ref} not built for algorithms: "
            f"{', '.join(unbuilt)} — run 'sim2real build --translation "
            f"{translation_ref}' first"
        )

    generated_root = translation_dir / "generated"

    packages: list[tuple[str, dict]] = []
    resolved_baselines: dict[str, dict] = {}
    for bl in manifest.get("baselines", []):
        bl_name = bl["name"]
        bundle_path = _resolve_scenario_path(
            exp_root, bl.get("scenario"), "baseline.yaml"
        )
        if bundle_path is None:
            raise AssembleError(f"baseline '{bl_name}' has no scenario file")
        overlay_path = generated_root / f"baseline_{bl_name}" / "baseline_config.yaml"
        if not overlay_path.exists():
            # BYO ``translation register`` writes the shared step-1
            # ``generated/baseline_config.yaml`` at the generated root.
            # Fall back to that layout when the per-baseline dir is
            # absent so BYO translations remain resolvable.
            legacy_overlay = generated_root / "baseline_config.yaml"
            overlay_path = legacy_overlay if legacy_overlay.exists() else None
        resolved = resolve_baseline(
            bundle_path=bundle_path,
            overlay_path=overlay_path,
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
        algo_image_ref = translated_algos[algo_name]["image_ref"]
        inject_image_tag(resolved, algo_image_ref)
        packages.append((algo_name, resolved))

    hf_secret = (cluster_config.get("secret_names") or {}).get(
        "hf_token", "hf-secret"
    )
    for _, resolved in packages:
        inject_hf_secret_name(resolved, hf_secret)

    workloads = [_load_workload(exp_root, wl) for wl in manifest.get("workloads", [])]
    first_baseline = next(
        (resolved for name, resolved in packages if name in resolved_baselines),
        packages[0][1] if packages else {},
    )
    scenarios_list = first_baseline.get("scenario", [])
    model_name = (
        scenarios_list[0].get("model", {}).get("name", "") if scenarios_list else ""
    )
    submodule_shas, submodule_urls, missing_submodules = (
        discover_framework_submodules(_REPO_ROOT)
    )

    return _ResolvedPackages(
        packages=packages,
        resolved_baselines=resolved_baselines,
        kept_algos=kept_algos,
        skipped_algo_names=skipped_algo_names,
        translated_algos=translated_algos,
        workloads=workloads,
        model_name=model_name,
        submodule_shas=submodule_shas,
        submodule_urls=submodule_urls,
        missing_submodules=missing_submodules,
    )


def _additive_grow(
    run_dir: Path,
    manifest: dict,
    *,
    prior_replicas: int,
    new_replicas: int,
    now_iso: str,
    experiment_root: Path,
    translation_hash: str,
    translation_ref: str,
    translation_dir: Path,
    cluster_config: dict,
) -> None:
    """Grow an existing run's replica count from ``prior_replicas`` to
    ``new_replicas`` (``new_replicas > prior_replicas``).

    Preserves existing PipelineRun files (i1..i{prior_replicas}) byte-for-byte
    and by mtime. Emits new files for i{prior_replicas+1}..i{new_replicas}.
    Rewrites ``manifest.assembly.yaml`` with the new replica count. Rewrites
    ``run_metadata.json`` with the new replica count and a new
    ``assembled_at`` timestamp; ``params_hash`` byte-identical to prior
    (drift check already ran and passed).
    """
    # Re-run scenario resolution to obtain workloads + packages + submodule
    # discovery. Safe because drift check has already established that the
    # manifest slice is unchanged from the prior assemble; ``_resolve_packages``
    # still validates every referenced file and refuses cleanly (as
    # ``AssembleError``) if a scenario file or the translation output has been
    # deleted or corrupted since.
    layout.set_experiment_root(experiment_root)
    exp_root = layout.experiment_root()
    tout_path = layout.translation_output_path(translation_hash)
    resolved = _resolve_packages(
        manifest,
        exp_root=exp_root,
        translation_dir=translation_dir,
        tout_path=tout_path,
        cluster_config=cluster_config,
        translation_ref=translation_ref,
    )

    generate_pipelineruns(
        run_dir=run_dir,
        packages=resolved.packages,
        workloads=resolved.workloads,
        run_name=run_dir.name,
        cluster_config=cluster_config,
        pipeline_name=(manifest.get("pipeline") or {}).get("name", "sim2real"),
        observe=manifest.get("blis_observe") or {},
        model_name=resolved.model_name,
        submodule_shas=resolved.submodule_shas,
        submodule_urls=resolved.submodule_urls,
        iterations=range(prior_replicas + 1, new_replicas + 1),
    )

    # Rewrite manifest.assembly.yaml with new replicas count.
    write_manifest_assembly(run_dir, manifest, now_iso=now_iso, replicas=new_replicas)

    # Rewrite run_metadata.json. params_hash is preserved (drift check passed).
    rm_path = run_dir / "run_metadata.json"
    rm = json.loads(rm_path.read_text())
    rm["replicas"] = new_replicas
    rm["assembled_at"] = now_iso
    rm_path.write_text(json.dumps(rm, indent=2, sort_keys=True) + "\n")


def assemble_run(
    *,
    translation_hash: str,
    translation_ref: str,
    cluster_id: str,
    run_name: str,
    experiment_root: Path,
    manifest_path: Path,
    force: bool,
    replicas: int = 1,
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

    ``translation_ref`` is the user-facing ref (alias/prefix/hash) as typed at
    the CLI. Used only in error messages — internal logic uses
    ``translation_hash``.

    The list of algorithms present in the manifest but absent from the
    registered translation is stored on ``assemble_run.skipped_algorithms``
    for the CLI wrapper to surface as warnings.

    Framework submodules (``inference-sim``, ``llm-d-benchmark``) whose
    directory is not initialized are similarly recorded on
    ``assemble_run.missing_submodules``. The four PipelineRun params
    (``benchmarkGit*``, ``blisGit*``) fall back to ``"unknown"`` in
    that case so the run assembles locally; the cluster-side clone
    step then fails visibly at the right point.
    """
    layout.set_experiment_root(experiment_root)
    # Reset side-band state each call — see docstring above.
    assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
    assemble_run.missing_submodules = []  # type: ignore[attr-defined]

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

    # 2. Load manifest early (needed for drift comparison on re-assemble)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        raise AssembleError(f"cannot load manifest {manifest_path}: {exc}") from exc

    # 3. Existing-run decision tree (uses `manifest` for drift check) ------
    run_dir = layout.runs_dir() / run_name
    additive_grow_from: int | None = None
    if run_dir.exists():
        prior_ma_path = run_dir / "manifest.assembly.yaml"
        prior_rm_path = run_dir / "run_metadata.json"
        if not (prior_ma_path.exists() and prior_rm_path.exists()):
            if not force:
                raise AssembleError(
                    f"run directory '{run_name}' is missing manifest.assembly.yaml or "
                    f"run_metadata.json — pass --force to rebuild"
                )
            shutil.rmtree(run_dir)
        else:
            try:
                prior_ma = yaml.safe_load(prior_ma_path.read_text()) or {}
                prior_rm = json.loads(prior_rm_path.read_text())
            except (yaml.YAMLError, json.JSONDecodeError, OSError) as exc:
                if not force:
                    raise AssembleError(
                        f"run '{run_name}' has a corrupt manifest.assembly.yaml "
                        f"or run_metadata.json: {exc} — pass --force to rebuild"
                    ) from exc
                shutil.rmtree(run_dir)
            else:
                prior_replicas = (
                    prior_ma.get("replicas") if isinstance(prior_ma, dict) else None
                )
                if prior_replicas is None:
                    if not force:
                        raise AssembleError(
                            f"run '{run_name}' is in legacy single-replica shape "
                            "(pre-step-5); create a fresh run to use --replicas, "
                            "or pass --force to rebuild."
                        )
                    shutil.rmtree(run_dir)
                else:
                    # Grow-only guard runs BEFORE the drift check because
                    # --force bypasses drift but does NOT bypass grow-only.
                    if replicas < prior_replicas:
                        raise AssembleError(
                            f"run '{run_name}' already has {prior_replicas} "
                            f"replicas; refusing to shrink to {replicas}. "
                            "Replica shrink is tracked in #506."
                        )
                    new_slice = slicer.assembly_slice(manifest)
                    new_canonical = yaml.dump(
                        new_slice, sort_keys=True, default_flow_style=False,
                        allow_unicode=True,
                    ).encode("utf-8")
                    new_content_hash = hashlib.sha256(new_canonical).hexdigest()
                    prior_params_hash = prior_rm.get("params_hash", "")
                    if new_content_hash != prior_params_hash:
                        if not force:
                            raise AssembleError(
                                f"manifest content changed since last assemble for "
                                f"run '{run_name}'; pass --force to overwrite."
                            )
                        shutil.rmtree(run_dir)
                    else:
                        if replicas == prior_replicas:
                            if force:
                                # --force overrides the true-no-op path so
                                # operators can rebuild when the assembler
                                # code has changed even though inputs did not.
                                shutil.rmtree(run_dir)
                            else:
                                # True no-op: reset side-band attrs and return.
                                assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
                                assemble_run.missing_submodules = []  # type: ignore[attr-defined]
                                return
                        elif force:
                            # replicas > prior_replicas with --force: full
                            # rebuild rather than additive-grow. --force is
                            # the "nuke and start over" escape hatch and
                            # applies uniformly to every branch inside the
                            # existing-run decision tree except shrink,
                            # which is guarded above.
                            shutil.rmtree(run_dir)
                        else:
                            # replicas > prior_replicas: additive grow.
                            additive_grow_from = prior_replicas

    if additive_grow_from is not None:
        _additive_grow(
            run_dir,
            manifest,
            prior_replicas=additive_grow_from,
            new_replicas=replicas,
            now_iso=now_iso,
            experiment_root=experiment_root,
            translation_hash=translation_hash,
            translation_ref=translation_ref,
            translation_dir=layout.translation_dir(translation_hash),
            cluster_config=cluster_config,
        )
        # skipped_algorithms/missing_submodules unchanged from prior assemble.
        assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
        assemble_run.missing_submodules = []  # type: ignore[attr-defined]
        return

    # 4. Resolve packages (translation load, algorithm filter, baseline +
    # treatment resolution, image + HF-secret injection, workload load,
    # model-name derivation, submodule discovery). Shared with the additive
    # -grow path via ``_resolve_packages`` so the AssembleError guards stay
    # in one place.
    exp_root = layout.experiment_root()
    resolved = _resolve_packages(
        manifest,
        exp_root=exp_root,
        translation_dir=tdir,
        tout_path=tout_path,
        cluster_config=cluster_config,
        translation_ref=translation_ref,
    )
    packages = resolved.packages
    kept_algos = resolved.kept_algos
    translated_algos = resolved.translated_algos
    assemble_run.missing_submodules = resolved.missing_submodules  # type: ignore[attr-defined]

    # 5. Snapshot assembly slice + params_hash ----------------------------
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_assembly_path = write_manifest_assembly(
        run_dir, manifest, now_iso=now_iso, replicas=replicas
    )
    params_hash = compute_params_hash(manifest_assembly_path)

    # 6. Write scenario YAMLs ---------------------------------------------
    write_resolved_scenarios(run_dir, packages)

    # 7. Generate PipelineRuns --------------------------------------------
    generate_pipelineruns(
        run_dir=run_dir,
        packages=packages,
        workloads=resolved.workloads,
        run_name=run_name,
        cluster_config=cluster_config,
        pipeline_name=(manifest.get("pipeline") or {}).get("name", "sim2real"),
        observe=manifest.get("blis_observe") or {},
        model_name=resolved.model_name,
        submodule_shas=resolved.submodule_shas,
        submodule_urls=resolved.submodule_urls,
        iterations=range(1, replicas + 1),
    )

    # 8. Write run_metadata.json ------------------------------------------
    # image_tag is a single-image summary field for backward-compat; use the
    # first *kept* algorithm's image_ref. Reading from kept_algos rather
    # than tout["algorithms"][0] guards against the multi-algo case where
    # translated_algos contains an algo that was filtered out of the run,
    # which would otherwise leak a null image_tag past the built-algo check.
    run_meta_image_tag = (
        translated_algos[kept_algos[0]["name"]]["image_ref"]
        if kept_algos else ""
    )
    write_run_metadata(
        run_dir,
        {
            "version": 1,
            "run_name": run_name,
            "translation_hash": translation_hash,
            "cluster_id": cluster_id,
            "params_hash": params_hash,
            "image_tag": run_meta_image_tag,
            "replicas": replicas,
            "assembled_at": now_iso,
        },
    )
    # Skipped-algorithm list exposed for the CLI wrapper to surface as warnings.
    assemble_run.skipped_algorithms = resolved.skipped_algo_names  # type: ignore[attr-defined]


# Initialize side-band attributes so `getattr` in the CLI works on first call.
assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
assemble_run.missing_submodules = []  # type: ignore[attr-defined]
