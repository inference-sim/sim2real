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

from pipeline.lib import cluster_ops, layout, scope as _scope, slicer, translation_ref as _translation_ref
# ``AssembleError`` lives in ``pipeline.lib.errors`` so low-level modules
# (e.g. ``slicer``) can raise it without an import cycle. Re-exported below
# to preserve the existing ``assemble_run.AssembleError`` API.
from pipeline.lib.errors import AssembleError
from pipeline.lib.manifest import ManifestError, load_manifest
from pipeline.lib.tekton import (
    is_trace_workload,
    make_pipelinerun_scenario,
    validate_pipelinerun_name,
)
from pipeline.lib.values import deep_merge


# Framework repo root. Resolved centrally by ``layout.repo_root()`` (single
# source of truth) rather than re-deriving it here. Used to locate framework
# submodules (inference-sim, llm-d-benchmark), which always live in the
# framework repo — NOT in the experiment repo.
_REPO_ROOT: Path = layout.repo_root()


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


def _write_text_or_raise(path: Path, text: str) -> None:
    """Write *text* to *path*, normalizing ``OSError`` to :class:`AssembleError`.

    Every write in the assemble path goes through here so a disk-full,
    permissions, or read-only-filesystem failure surfaces as the CLI's own
    error shape rather than a raw traceback — matching how the two deletion
    steps already behave. This matters most for the writes that follow the
    results wipe: the operator needs a legible error naming the file, not a
    stack trace, to understand what state the run is in (issue #876).
    """
    try:
        path.write_text(text)
    except OSError as exc:
        raise AssembleError(f"failed to write {path}: {exc}") from exc


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
        try:
            merged = deep_merge(merged, _load_yaml(fragment))
        except ValueError as exc:
            # Flag-list tier rejection (non-`--` entry, or two entries colliding
            # on one flag key) — normalize so the CLI reports it as an error
            # rather than a traceback, and name the fragment at fault.
            raise AssembleError(f"defaults fragment {fragment.name}: {exc}") from exc
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
    _write_text_or_raise(out, text)
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
    _write_text_or_raise(
        out, json.dumps(meta, indent=2, sort_keys=True) + "\n"
    )
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


def _merge_layer(base: dict, overlay: dict, *, layer: str, sink: list | None) -> dict:
    """``deep_merge`` one layer, tagging any recorded conflict with the layer pair.

    ``values.deep_merge`` knows the key path but not which files are being merged
    — only this module knows that. Collecting per-merge and re-prefixing is what
    turns "scenario.router.proxy.args was replaced" into an operator-actionable
    "which two layers disagreed".

    ``local`` stays ``None`` when no sink was requested, so ``deep_merge`` keeps
    taking ``_record_scalar_list_replace``'s ``sink is None`` fast path rather
    than allocating and formatting messages nobody will read.

    A ``ValueError`` from the flag-list tier (a non-``--`` entry, or two entries
    colliding on one flag key) is normalized to ``AssembleError`` here, matching
    how ``make_pipelinerun_scenario``'s ValueError is handled below. Without it
    the CLI's ``except AssembleError`` in ``sim2real.py`` would miss it and the
    operator would get a raw traceback instead of ``error: ...`` and exit 2. The
    layer prefix is attached so the message names which two files disagreed.
    """
    local: list[str] | None = [] if sink is not None else None
    try:
        merged = deep_merge(base, overlay, sink=local)
    except ValueError as exc:
        raise AssembleError(f"{layer}: {exc}") from exc
    if sink is not None and local:
        sink.extend(f"{layer}: {msg}" for msg in local)
    return merged


def resolve_baseline(
    *,
    bundle_path: Path,
    overlay_path: Path | None,
    framework_defaults: dict,
    sink: list | None = None,
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

    ``sink``, when a list, collects operator-facing warnings about scalar lists
    that one layer replaced wholesale, each tagged with the layer pair that
    disagreed. ``**.vllm.additionalFlags`` merges by flag name and never appears
    there; see ``values._record_scalar_list_replace`` and issue #851.
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
    resolved = _merge_layer(
        aligned_defaults, bundle,
        layer="framework defaults -> baseline bundle", sink=sink,
    )
    resolved = _merge_layer(
        resolved, overlay,
        layer="baseline bundle -> registered overlay", sink=sink,
    )
    return resolved


def resolve_treatment(
    *,
    baseline_resolved: dict,
    diffs_path: Path | None,
    overlay_path: Path | None,
    sink: list | None = None,
) -> dict:
    """Return ``deep_merge(baseline_resolved, treatment_diffs, algo_overlay)``.

    Either or both of ``diffs_path`` / ``overlay_path`` may be ``None`` or
    point at non-existent files — the corresponding layer is treated as
    empty. Baseline is required (starts from an already-resolved dict).

    ``sink`` behaves as in ``resolve_baseline``: it collects scalar-list
    replacement warnings tagged with the layer pair that disagreed.
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
    resolved = _merge_layer(
        copy.deepcopy(baseline_resolved), diffs,
        layer="baseline -> treatment diffs", sink=sink,
    )
    resolved = _merge_layer(
        resolved, overlay,
        layer="treatment diffs -> algorithm overlay", sink=sink,
    )
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
        _write_text_or_raise(
            cluster_dir_ / f"{name}.yaml",
            yaml.dump(resolved, default_flow_style=False, allow_unicode=True),
        )
    return cluster_dir_


def pipelinerun_filename(workload: str, package: str, iteration: int) -> str:
    """Filename of the PipelineRun YAML for one (workload, package, iteration).

    Single source of truth for the ``_`` → ``-`` substitution applied to the
    workload segment: the ``|`` separators make the derived pair key match the
    canonical grammar in ``pipeline/lib/pairkey.py``, which does not admit
    underscores.

    The substitution is deliberately NOT applied to ``results/`` paths — those
    carry the raw workload name, because that is what the cluster-side
    collector writes. Anything comparing the two must go through
    ``_normalize_scope_name``.
    """
    return f"pipelinerun-{workload.replace('_', '-')}|{package}|i{iteration}.yaml"


def _normalize_scope_name(name: str) -> str:
    """Comparison form for a ``--workload`` / ``--package`` filter value.

    Workload names reach the operator in two spellings — raw (the workload
    YAML, ``results/``) and ``_``-substituted (``cluster/pipelinerun-*``, and
    therefore every pair key ``deploy`` prints). That substitution is slated
    for deprecation; until then, normalizing both sides means the operator can
    type either spelling instead of having to know which producer they are
    naming.
    """
    return name.replace("_", "-")


def _select_scope_names(
    valid: list[str], raw_filter: "list[str] | None", flag: str
) -> list[str]:
    """Apply one scope filter to *valid*, preserving *valid*'s declared order.

    Returns every name in *valid* when *raw_filter* is ``None``. Raises
    :class:`AssembleError` naming the valid values when any filter value —
    literal or glob — matches nothing, mirroring ``deploy``'s
    ``_report_filter_mismatch`` habit of printing the alternatives.
    """
    values = _scope.parse_name_list(raw_filter)
    if values is None:
        return list(valid)
    # Match on the normalized spelling but select the canonical names, so
    # downstream path construction always uses the producer's own spelling.
    #
    # Two names that normalize to the same string are refused rather than
    # silently collapsed: keeping only the first would make the second
    # unreachable AND make a filter value naming the second resolve to the
    # first, so the wrong pair would be regenerated and (absent --no-wipe) the
    # wrong pair's results deleted. Such a manifest is already broken — both
    # names produce the same ``cluster/pipelinerun-*.yaml`` filename — but a
    # scope filter must not be the thing that discovers it by destroying data.
    by_norm: dict[str, str] = {}
    for name in valid:
        norm = _normalize_scope_name(name)
        if norm in by_norm and by_norm[norm] != name:
            raise AssembleError(
                f"{flag}: '{by_norm[norm]}' and '{name}' are indistinguishable "
                "once '_' is normalized to '-', so no filter value can name one "
                "without the other. Rename one in transfer.yaml."
            )
        by_norm[norm] = name
    expanded, unknown = _scope.expand_glob_values(
        [_normalize_scope_name(v) for v in values], list(by_norm)
    )
    if unknown:
        raise AssembleError(
            f"{flag}: no match for {', '.join(sorted(set(unknown)))}. "
            f"Valid {flag} values: {', '.join(valid)}"
        )
    selected = {by_norm[n] for n in expanded}
    return [n for n in valid if n in selected]


def resolve_pair_scope(
    *,
    workload_names: list[str],
    package_names: list[str],
    workload_filter: "list[str] | None",
    package_filter: "list[str] | None",
) -> list[tuple[str, str]]:
    """Return the ``(workload, package)`` pairs in scope for this assemble.

    Scope is the cross product of *workload_names* × *package_names*, narrowed
    by the filters, in workload-major declared order. Both filters accept
    comma-separated values and shell globs, matching ``deploy``'s
    ``--workload`` / ``--package`` grammar — the parsing is literally shared,
    see ``pipeline/lib/scope.py``.

    Derived from the manifest rather than from ``deploy``'s ``_resolve_scope``,
    which resolves pairs from the ConfigMap-backed progress store and therefore
    needs a cluster namespace — unavailable and irrelevant at assemble time.
    """
    selected_wl = _select_scope_names(workload_names, workload_filter, "--workload")
    selected_pkg = _select_scope_names(package_names, package_filter, "--package")
    return [(wl, pkg) for wl in selected_wl for pkg in selected_pkg]


def _confirm_results_wipe(displays: list[str]) -> bool:
    """Ask before deleting collected results, returning True to proceed.

    Reached only on row 2 of issue #876's table — a pair whose PipelineRun is
    absent but whose results are present — where regeneration needs no flag and
    the wipe would therefore happen without the operator having authorized any
    data loss. Mirrors ``deploy wipe``'s pattern (enumerate the targets, ``[y/N]``,
    EOF declines) so the two commands behave the same way.

    This is the one place the module prints; tests monkeypatch it.
    """
    print(
        "The following collected results will be deleted so their pairs can be "
        "re-assembled:"
    )
    for d in displays:
        print(f"    {d}")
    try:
        answer = input(f"Wipe {len(displays)} result director(ies)? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() == "y"


def prune_orphan_cluster_files(
    run_dir: Path,
    *,
    expected_pipelineruns: set[str],
    package_names: list[str],
) -> list[str]:
    """Delete ``cluster/*.yaml`` files the current manifest no longer describes.

    Scoped to ``*.yaml`` deliberately — only the generated PipelineRun and
    per-package scenario YAMLs are assemble's to remove. Anything else an
    operator has left in ``cluster/`` is theirs and is never touched.

    Necessary because ``--force`` no longer ``rmtree``s the run directory
    (issue #876). Without pruning, a ``transfer.yaml`` edit that drops a
    workload or an algorithm would leave that pair's PipelineRun behind, and
    ``deploy`` discovers pairs by globbing ``cluster/pipelinerun-*.yaml`` — so
    the dropped pair would still be executed. The stale per-package scenario
    YAMLs matter too: ``deploy`` falls back to the first non-PipelineRun YAML in
    ``cluster/`` when ``baseline.yaml`` is absent, for GPU-config derivation.

    Only ever called for an unscoped assemble — a scoped invocation has no
    business judging pairs outside its scope. Collected ``results/`` for a
    pruned pair are deliberately left in place: they are measured data, and not
    destroying it is the point of this issue.

    Returns the deleted filenames in sorted order.
    """
    cluster_dir_ = run_dir / "cluster"
    if not cluster_dir_.is_dir():
        return []
    keep_scenarios = set(package_names)
    removed: list[str] = []
    for path in sorted(cluster_dir_.glob("*.yaml")):
        if path.name.startswith("pipelinerun-"):
            if path.name in expected_pipelineruns:
                continue
        elif path.stem in keep_scenarios:
            continue
        try:
            path.unlink()
        except OSError as exc:
            # Normalized to AssembleError rather than escaping as a raw
            # traceback: a stale PipelineRun left on disk is a correctness
            # hazard (``deploy`` globs ``cluster/pipelinerun-*.yaml`` and would
            # execute a pair the manifest no longer describes), so this has to
            # fail loudly and in the CLI's own error shape.
            raise AssembleError(
                f"failed to remove stale cluster/{path.name}, which the current "
                f"transfer.yaml no longer describes: {exc}. Leaving it in place "
                "would let deploy execute that pair; remove it by hand and "
                "re-run assemble."
            ) from exc
        removed.append(path.name)
    return removed


class PairPlan(NamedTuple):
    """What assemble will do to one ``(workload, package, iteration)`` triple.

    ``regenerate`` and ``wipe_results`` are the two orthogonal axes from issue
    #876: ``--force`` answers "this PipelineRun already exists — redo it?",
    ``--no-wipe`` answers "results exist for a pair being regenerated — keep
    them?". Because the second question is only ever asked about a pair that is
    actually being redone, ``wipe_results`` is never True when ``regenerate``
    is False.
    """

    workload: str
    package: str
    iteration: int
    pipelinerun_path: Path
    results_path: Path
    regenerate: bool
    wipe_results: bool

    @property
    def results_display(self) -> str:
        """Operator-facing form of ``results_path``, relative to the run dir."""
        return f"results/{self.package}/{self.workload}/i{self.iteration}/"


def plan_pairs(
    *,
    run_dir: Path,
    scope: list[tuple[str, str]],
    iterations: "range | list[int]",
    force: bool,
    no_wipe: bool,
) -> list[PairPlan]:
    """Decide, per triple in ``scope`` × ``iterations``, what assemble will do.

    Both predicates are plain ``Path.exists()`` checks — no new state, no
    content hashing, no ``run_metadata.json`` schema change:

    * PipelineRun: ``cluster/pipelinerun-<wl>|<pkg>|i<N>.yaml``
    * results:     ``results/<pkg>/<wl>/i<N>/``

    The two use different spellings of the workload name (``_``-substituted and
    raw respectively) because that is what their producers write — see
    ``pipelinerun_filename``.
    """
    cluster_dir_ = run_dir / "cluster"
    results_root = run_dir / "results"
    plans: list[PairPlan] = []
    for workload, package in scope:
        for iteration in iterations:
            pr_path = cluster_dir_ / pipelinerun_filename(workload, package, iteration)
            res_path = results_root / package / workload / f"i{iteration}"
            regenerate = force or not pr_path.exists()
            plans.append(
                PairPlan(
                    workload=workload,
                    package=package,
                    iteration=iteration,
                    pipelinerun_path=pr_path,
                    results_path=res_path,
                    regenerate=regenerate,
                    wipe_results=regenerate and not no_wipe and res_path.exists(),
                )
            )
    return plans


def build_pipelineruns(
    *,
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
    triples: "set[tuple[str, str, int]] | None" = None,
) -> list[tuple[str, dict]]:
    """Build ``(filename, PipelineRun dict)`` for each selected triple.

    Pure — touches no filesystem. Split out from ``write_pipelineruns`` so that
    everything that can *refuse* (notably ``validate_pipelinerun_name``'s
    253-char DNS-subdomain limit, raised through ``make_pipelinerun_scenario``)
    happens before ``assemble_run`` deletes anything. Wiping a pair's collected
    results and only then discovering its PipelineRun name is too long would
    strand that pair with neither results nor a manifest (issue #876).

    Filename shape is owned by ``pipelinerun_filename``:
    ``pipelinerun-<workload-safe>|<package>|i<N>.yaml``, where
    ``<workload-safe>`` is the workload name with ``_`` replaced by ``-``
    and ``N`` is each element of ``iterations``.

    When *triples* is given, only ``(workload_name, package, iteration)``
    members of that set are built; every other combination is skipped, so
    whatever is on disk for it is later left byte- and mtime-identical. That is
    what makes a scoped assemble non-destructive to out-of-scope pairs. When
    *triples* is ``None`` the full cross product of *packages* × *workloads* ×
    *iterations* is built.
    """
    namespaces = cluster_config.get("namespaces") or []
    namespace = namespaces[0] if namespaces else "default"
    ws_bindings = cluster_config.get("workspaces") or {}

    built: list[tuple[str, dict]] = []
    for pkg_name, resolved in packages:
        scenario_content = yaml.dump(
            resolved, default_flow_style=False, allow_unicode=True
        )
        for wl in workloads:
            wl_name = wl.get("name", wl.get("workload_name", "unknown"))
            for iteration in iterations:
                if triples is not None and (
                    wl_name, pkg_name, iteration
                ) not in triples:
                    continue
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
                built.append(
                    (pipelinerun_filename(wl_name, pkg_name, iteration), pr)
                )
    return built


def write_pipelineruns(run_dir: Path, built: list[tuple[str, dict]]) -> None:
    """Write each ``(filename, PipelineRun dict)`` from :func:`build_pipelineruns`
    into ``runs/<R>/cluster/``.
    """
    cluster_dir_ = run_dir / "cluster"
    cluster_dir_.mkdir(parents=True, exist_ok=True)
    for fname, pr in built:
        _write_text_or_raise(
            cluster_dir_ / fname,
            yaml.dump(pr, default_flow_style=False, allow_unicode=True),
        )


# Iteration that every grown replica is derived from. Fixed at 1 rather than
# "the highest existing" so a grow always reproduces the pair's FIRST iteration:
# that is what makes iN a replica of i1 rather than of whatever the previous
# grow happened to emit.
_GROW_SOURCE_ITERATION = 1


def _pipelinerun_pair_and_iteration(path: Path) -> "tuple[str, int] | None":
    """Split a PipelineRun filename into ``(pair-prefix, iteration)``.

    The pair-prefix is the filename stem up to but excluding the trailing
    ``|i<N>`` segment — everything that identifies the (workload, package) pair.
    Returns ``None`` for any name that does not parse, so hand-placed or
    duplicated files (``...|i1 copy.yaml``) are ignored rather than mistaken for
    a pair to grow.
    """
    parts = path.stem.split("|")
    if len(parts) != 3:
        return None
    iteration = parts[2]
    if not iteration.startswith("i") or not iteration[1:].isdigit():
        return None
    n = int(iteration[1:])
    if n < 1:
        return None
    return "|".join(parts[:2]), n


def _retarget_pipelinerun_iteration(
    pr: dict, *, source_iteration: int, iteration: int
) -> None:
    """Rewrite an in-memory PipelineRun from *source_iteration* to *iteration*.

    Exactly two fields carry the iteration (issue #877): the ``-i<N>`` suffix on
    ``metadata.name`` (``tekton.py``'s ``pr_name``) and the ``replica`` param.
    ``resultsDir`` threads ``i$(params.replica)``, so it follows from the second
    with no edit of its own.

    Structural on purpose. The document also holds several unrelated
    ``replicas:`` keys inside the inlined scenario — vLLM and deployment pod
    counts — and the run name may itself contain ``-i<digits>``, so any textual
    substitution would corrupt it.
    """
    meta = pr.get("metadata")
    if not isinstance(meta, dict) or not isinstance(meta.get("name"), str):
        raise AssembleError(
            "PipelineRun has no metadata.name string; cannot retarget it to "
            f"iteration {iteration}"
        )
    suffix = f"-i{source_iteration}"
    name = meta["name"]
    if not name.endswith(suffix):
        raise AssembleError(
            f"PipelineRun name '{name}' does not end in '{suffix}', so it is not "
            f"iteration {source_iteration} as its filename claims — refusing to "
            "derive a replica from it."
        )
    grown = name[: -len(suffix)] + f"-i{iteration}"
    # A grow can push a name that previously fit over the limit: i9 -> i10 adds
    # a character.
    try:
        validate_pipelinerun_name(grown)
    except ValueError as exc:
        raise AssembleError(str(exc)) from exc
    meta["name"] = grown

    params = (pr.get("spec") or {}).get("params")
    if not isinstance(params, list):
        raise AssembleError(
            f"PipelineRun '{name}' has no spec.params list; cannot retarget it "
            f"to iteration {iteration}"
        )
    for param in params:
        if isinstance(param, dict) and param.get("name") == "replica":
            param["value"] = str(iteration)
            return
    raise AssembleError(
        f"PipelineRun '{name}' has no 'replica' param; cannot retarget it to "
        f"iteration {iteration}"
    )


class GrowPlan(NamedTuple):
    """What :func:`build_grown_iterations` resolved, before anything is written.

    ``built`` is the ``(filename, PipelineRun dict)`` list to write; ``ignored``
    names the ``cluster/pipelinerun-*.yaml`` files whose names did not parse and
    were therefore skipped, so the caller can say so rather than leaving the
    operator to notice a pair silently never grew.
    """

    built: list[tuple[str, dict]]
    ignored: list[str]


def build_grown_iterations(
    run_dir: Path, *, prior_replicas: int, new_replicas: int
) -> GrowPlan:
    """Build ``i{prior_replicas+1}..i{new_replicas}`` for every pair on disk by
    copying that pair's own ``i1``. Touches no filesystem beyond reading.

    Split from :func:`write_pipelineruns` for the same reason
    :func:`build_pipelineruns` is: every refusal — an unparseable source
    document, a name that does not end in the expected iteration, a grown name
    over the 253-char limit, a missing ``replica`` param — has to happen before
    the first file is written. Interleaving them would let a refusal on a later
    pair leave earlier pairs' new iterations on disk while
    ``run_metadata.json`` still recorded the old replica count, and ``deploy``
    discovers iterations by globbing ``cluster/pipelinerun-*.yaml`` — so it
    would execute iterations the metadata never claimed.
    """
    cluster_dir_ = run_dir / "cluster"
    sources: dict[str, Path] = {}
    pairs: set[str] = set()
    ignored: list[str] = []
    for path in sorted(cluster_dir_.glob("pipelinerun-*.yaml")):
        parsed = _pipelinerun_pair_and_iteration(path)
        if parsed is None:
            ignored.append(path.name)
            continue
        pair, iteration = parsed
        pairs.add(pair)
        if iteration == _GROW_SOURCE_ITERATION:
            sources[pair] = path

    missing = sorted(pairs - set(sources))
    if missing:
        raise AssembleError(
            "cannot grow replicas: "
            + ", ".join(
                f"'{p}' has no i{_GROW_SOURCE_ITERATION}" for p in missing
            )
            + f". Every pair grows from its own i{_GROW_SOURCE_ITERATION}, so it "
            "must be present. Restore it, or re-assemble the run with --force."
        )

    built: list[tuple[str, dict]] = []
    for pair in sorted(sources):
        base = _load_yaml(sources[pair])
        for iteration in range(prior_replicas + 1, new_replicas + 1):
            grown = copy.deepcopy(base)
            _retarget_pipelinerun_iteration(
                grown,
                source_iteration=_GROW_SOURCE_ITERATION,
                iteration=iteration,
            )
            built.append((f"{pair}|i{iteration}.yaml", grown))
    return GrowPlan(built=built, ignored=ignored)


def grow_pair_iterations(
    run_dir: Path, *, prior_replicas: int, new_replicas: int
) -> GrowPlan:
    """Emit ``i{prior_replicas+1}..i{new_replicas}`` for every pair on disk by
    copying that pair's own ``i1``.

    This is the whole of issue #877's fix. The previous implementation
    re-resolved the manifest and overlays to build the new iterations, so an
    overlay edited between two assembles left ``i1`` and ``i2`` carrying
    different plugin configs, with nothing on disk recording why — replicas that
    were not replicas, and therefore a variance figure that measures nothing.
    Deriving each new iteration from the pair's own ``i1`` makes them replicas by
    construction, so no drift detection is needed.

    It also preserves legitimate per-pair differences for free: each pair grows
    from itself, so a run whose pairs carry deliberately different configs stays
    internally consistent.

    Pairs are discovered from the ``cluster/pipelinerun-*.yaml`` filenames rather
    than from the manifest. That is equivalent here — reaching the grow path
    requires the drift check to have passed, so the on-disk pair set is the
    manifest cross product — and it is what lets each pair be grown from its own
    source.

    A pair that has other iterations but no ``i1`` is refused rather than
    resolved: falling back would silently reintroduce the divergence this
    function exists to remove. Pre-existing divergence among ``i1..i_prior`` is
    NOT repaired — only the new iterations are made to match ``i1``.

    Build-then-write: :func:`build_grown_iterations` resolves and validates every
    grown document first, so a refusal on any pair leaves the run untouched
    rather than half-grown.

    Returns the :class:`GrowPlan`, whose ``built`` entries have all been written.
    """
    plan = build_grown_iterations(
        run_dir, prior_replicas=prior_replicas, new_replicas=new_replicas
    )
    write_pipelineruns(run_dir, plan.built)
    return plan


def _validate_workload(data: dict, wl_path: Path) -> None:
    """Validate a loaded workload's kind and (for trace workloads) shape.

    A workload is exactly one kind: TRACE (non-empty ``trace`` mapping) or
    GENERATIVE (``clients:``/``version:`` WorkloadSpec). Declaring both
    ``trace:`` and ``clients:`` is an error. When a workload is a trace
    workload, ``trace.source`` (non-empty str), ``trace.pool.concurrent_sessions``
    (int >= 1) and ``trace.pool.total_sessions`` (int >= 0) are required.
    Raises :class:`AssembleError` on any violation.
    """
    trace_mode = is_trace_workload(data)
    if trace_mode and "clients" in data:
        raise AssembleError(
            f"workload {wl_path} declares both 'trace' and 'clients'; a "
            f"workload must be exactly one kind (trace or generative)"
        )
    if not trace_mode:
        return
    trace = data["trace"]
    source = trace.get("source")
    if not isinstance(source, str) or not source:
        raise AssembleError(
            f"workload {wl_path}: trace.source must be a non-empty string"
        )
    pool = trace.get("pool")
    if not isinstance(pool, dict):
        raise AssembleError(f"workload {wl_path}: trace.pool is required")
    # bool is a subclass of int — reject it explicitly so `true`/`false` in
    # YAML don't slip through the int checks below.
    cs = pool.get("concurrent_sessions")
    if isinstance(cs, bool) or not isinstance(cs, int) or cs < 1:
        raise AssembleError(
            f"workload {wl_path}: trace.pool.concurrent_sessions must be an "
            f"int >= 1"
        )
    ts = pool.get("total_sessions")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts < 0:
        raise AssembleError(
            f"workload {wl_path}: trace.pool.total_sessions must be an int >= 0"
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
    _validate_workload(data, wl_path)
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
    #: Scalar lists that one layer replaced wholesale, discarding an earlier
    #: layer's values. Exposed for the CLI wrapper to surface as warnings — see
    #: ``values._record_scalar_list_replace`` and issue #851.
    scalar_list_conflicts: list[str]


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
    scalar_list_conflicts: list[str] = []
    for bl in manifest.get("baselines", []):
        bl_name = bl["name"]
        bundle_path = _resolve_scenario_path(
            exp_root, bl.get("scenario"), "baseline.yaml"
        )
        if bundle_path is None:
            raise AssembleError(f"baseline '{bl_name}' has no scenario file")
        # Per-baseline overlay layout: ``generated/baselines/<name>/baseline_config.yaml``.
        # The ``baselines/`` umbrella (issue #544) avoids the awkward
        # ``baseline_baseline/`` shape from the pre-#544 flat layout under
        # the standardized ``name: baseline`` identifier, and keeps
        # multi-baseline test cases (``baselines/base/``, ``baselines/alt/``)
        # readable.
        overlay_path = generated_root / "baselines" / bl_name / "baseline_config.yaml"
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
            sink=scalar_list_conflicts,
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
            sink=scalar_list_conflicts,
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
        scalar_list_conflicts=scalar_list_conflicts,
    )


def _additive_grow(
    run_dir: Path,
    manifest: dict,
    *,
    prior_replicas: int,
    new_replicas: int,
    now_iso: str,
) -> list[str]:
    """Grow an existing run's replica count from ``prior_replicas`` to
    ``new_replicas`` (``new_replicas > prior_replicas``).

    Preserves existing PipelineRun files (i1..i{prior_replicas}) byte-for-byte
    and by mtime. Emits new files for i{prior_replicas+1}..i{new_replicas} by
    copying each pair's own i1 — see :func:`grow_pair_iterations`. Rewrites
    ``manifest.assembly.yaml`` with the new replica count. Rewrites
    ``run_metadata.json`` with the new replica count and a new
    ``assembled_at`` timestamp; ``params_hash`` byte-identical to prior
    (drift check already ran and passed).

    Reads no overlay, baseline, defaults, or workload YAML (issue #877). It used
    to re-resolve all of them, which is precisely what let the new iterations
    carry a different config than the preserved ones: the caller's drift check
    hashes only ``slicer.assembly_slice(manifest)`` — transfer.yaml content — so
    an overlay could change between two assembles while the manifest hash still
    matched.

    Two consequences of no longer merging anything here:

    - The scalar-list conflicts that re-resolution used to surface on this path
      (#851) are no longer detected, because nothing is merged. Such a conflict
      is still reported by the next full assemble.
    - The input validation that came free with ``_resolve_packages`` is gone. The
      translation directory and ``translation_output.json`` are still checked in
      ``assemble_run``'s step 1, ahead of the decision tree, and the CLI's
      unbuilt-image check reads the latter — so a deleted translation still
      refuses. A deleted *scenario* file no longer does, which is correct: the
      grown iterations were never resolved from it. Note that
      ``translation_output.json`` being *parseable* is likewise no longer checked
      here: step 1 only checks that it exists, and the CLI's own unbuilt-image
      pre-check is what refuses a corrupt one. That makes it a CLI-level
      guarantee rather than a library-level one — a direct library caller that
      does not replicate that pre-check can grow a run whose translation record
      is unreadable.

    Returns the :class:`GrowPlan`.
    """
    plan = grow_pair_iterations(
        run_dir, prior_replicas=prior_replicas, new_replicas=new_replicas
    )

    # Rewrite manifest.assembly.yaml with new replicas count.
    write_manifest_assembly(run_dir, manifest, now_iso=now_iso, replicas=new_replicas)

    # Rewrite run_metadata.json. params_hash is preserved (drift check passed).
    # `scenario` is also refreshed from the manifest so legacy runs (assembled
    # before #551) get the field populated when they're grown, avoiding a
    # deploy-time scenario-missing failure downstream.
    rm_path = run_dir / "run_metadata.json"
    rm = json.loads(rm_path.read_text())
    rm["replicas"] = new_replicas
    rm["assembled_at"] = now_iso
    rm["scenario"] = manifest.get("scenario", "") or ""
    _write_text_or_raise(rm_path, json.dumps(rm, indent=2, sort_keys=True) + "\n")

    return plan


def assemble_run(
    *,
    translation_hash: str,
    translation_ref: str,
    cluster_id: str,
    run_name: str,
    experiment_root: Path,
    manifest_path: Path,
    force: bool,
    replicas: "int | None" = None,
    workload_filter: "list[str] | None" = None,
    package_filter: "list[str] | None" = None,
    no_wipe: bool = False,
    assume_yes: bool = False,
    now_iso: str,
) -> None:
    """Materialize ``workspace/runs/<run_name>/`` per the design.

    Steps (per design §Commands → sim2real assemble):
      1. Validate: translation dir + cluster_config exist. An existing run_dir
         is inspected, never deleted.
      2. Load manifest; filter algorithms to those in translation_output.json.
      3. Resolve baseline (framework_defaults → bundle → baseline_overlay) and
         each treatment (baseline_resolved → treatment diffs → per-algo overlay).
      4. Inject image_tag into treatment scenarios; inject huggingface.secretName
         into all scenarios.
      5. Resolve the pair scope and decide, per pair, whether to regenerate and
         whether to wipe its collected results.
      6. Build (and thereby validate) every regenerated PipelineRun in memory.
      7. Confirm any un-flagged results wipe.
      8. Prune cluster/ files the manifest no longer describes (unscoped only).
      9. Wipe the results of the pairs being regenerated.
     10. Snapshot assembly-slice → manifest.assembly.yaml; compute params_hash
         (unscoped only).
     11. Write cluster/{package}.yaml for the packages in scope.
     12. Write the PipelineRuns built in step 6.
     13. Write run_metadata.json (unscoped only).

    The ordering is what makes the destructive steps safe:

    - Everything that can refuse comes first. Step 6 is pure and is where the
      253-char PipelineRun-name limit is enforced, so a name that is too long
      aborts while the results are still on disk rather than after step 9 has
      deleted them.
    - Steps 8 and 9 are both deletions, ordered cheap-first: pruning removes
      regenerable YAML, so a read-only filesystem or a permissions problem
      aborts there, with the measured results still intact.
    - Every write in steps 10-13 goes through ``_write_text_or_raise``, so a
      disk-full or permissions failure after the wipe is reported as an
      ``AssembleError`` naming the file rather than a raw traceback.

    Raises AssembleError on any validation failure. Validation and the wipe
    confirmation both happen before anything is written — declining the prompt
    leaves the run exactly as it was.

    ``force`` no longer removes the run directory (issue #876). It is a per-pair
    predicate — "this PipelineRun already exists, redo it" — and the separate
    ``no_wipe`` axis decides whether a redone pair's collected ``results/``
    survive. Nothing outside the pairs in scope is deleted, except ``cluster/``
    files the current manifest no longer describes (unscoped only, see
    ``prune_orphan_cluster_files``). Because ``force`` is the long-documented
    way to discard a run's results, it does not prompt; row 2 of the table
    (PipelineRun absent, results present) regenerates without any flag and
    therefore does, unless ``assume_yes`` or ``no_wipe`` is set.

    Passing ``workload_filter`` or ``package_filter`` makes the invocation
    *scoped*. A scoped assemble requires an existing run, rejects an explicit
    ``replicas`` (it reuses the run's recorded count, so scoping cannot trip the
    shrink guard), refuses on manifest drift regardless of ``force``, and writes
    neither ``manifest.assembly.yaml`` nor ``run_metadata.json`` — so
    ``params_hash`` keeps meaning "the last state in which the whole run was
    consistent" and a later unscoped assemble still detects that drift.
    ``cluster/<package>.yaml`` *is* rewritten for the packages in scope.

    ``replicas`` of ``None`` means "not specified": 1 for an unscoped assemble
    (the historical default), and the run's recorded count when scoped.

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

    Scalar lists that one layer replaced wholesale, discarding an earlier
    layer's values, are recorded on ``assemble_run.scalar_list_conflicts`` for
    the same wrapper to surface (issue #851).
    """
    layout.set_experiment_root(experiment_root)
    # Reset side-band state each call — see docstring above.
    assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
    assemble_run.missing_submodules = []  # type: ignore[attr-defined]
    assemble_run.scalar_list_conflicts = []  # type: ignore[attr-defined]
    assemble_run.already_assembled = 0  # type: ignore[attr-defined]
    assemble_run.pruned_files = []  # type: ignore[attr-defined]
    assemble_run.wiped_results = []  # type: ignore[attr-defined]
    assemble_run.ignored_cluster_files = []  # type: ignore[attr-defined]

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
    #
    # Nothing here deletes the run directory. The six `shutil.rmtree(run_dir)`
    # calls this tree used to make were how `--force` destroyed collected
    # results (issue #876); regeneration is now decided per pair, in step 5.
    # What survives here is the set of conditions under which assemble must
    # refuse outright, plus resolving how many iterations a pair has.
    # Derive `scoped` from the SAME parse the filters actually go through.
    # ``parse_name_list`` collapses a degenerate value (all whitespace, bare
    # commas) to None, so testing the raw argv value here would take every
    # scoped branch — skipping run_metadata.json, manifest.assembly.yaml and
    # pruning — while ``resolve_pair_scope`` still returned the full cross
    # product, leaving the run's own bookkeeping silently stale.
    scoped = (
        _scope.parse_name_list(workload_filter) is not None
        or _scope.parse_name_list(package_filter) is not None
    )
    run_dir = layout.runs_dir() / run_name
    additive_grow_from: int | None = None
    # A scoped assemble cannot repair run-wide state, because it does not
    # rewrite manifest.assembly.yaml or run_metadata.json. Every structural
    # problem below therefore sends the operator to an unscoped assemble.
    _scoped_repair_hint = (
        "repair it with an unscoped `sim2real assemble --force` before scoping."
    )

    if not run_dir.exists():
        if scoped:
            raise AssembleError(
                "--workload/--package require an existing run: there is no "
                f"runs/{run_name}/ to re-assemble a subset of. Assemble the "
                "full run once first."
            )
        replicas_effective = 1 if replicas is None else replicas
    else:
        prior_ma_path = run_dir / "manifest.assembly.yaml"
        prior_rm_path = run_dir / "run_metadata.json"
        prior_ma: dict = {}
        prior_rm: dict = {}
        # `repairing` means the run's own metadata is unusable, so --force is
        # rebuilding it from scratch and there is no prior replica count to
        # honour or hash to compare against.
        repairing = False
        if not (prior_ma_path.exists() and prior_rm_path.exists()):
            if scoped:
                raise AssembleError(
                    f"run '{run_name}' is missing manifest.assembly.yaml or "
                    f"run_metadata.json — {_scoped_repair_hint}"
                )
            if not force:
                raise AssembleError(
                    f"run directory '{run_name}' is missing manifest.assembly.yaml or "
                    "run_metadata.json — pass --force to rebuild (add "
                    "--no-wipe to keep collected results)"
                )
            repairing = True
        else:
            try:
                prior_ma = yaml.safe_load(prior_ma_path.read_text()) or {}
                prior_rm = json.loads(prior_rm_path.read_text())
            except (yaml.YAMLError, json.JSONDecodeError, OSError) as exc:
                if scoped:
                    raise AssembleError(
                        f"run '{run_name}' has a corrupt manifest.assembly.yaml "
                        f"or run_metadata.json: {exc} — {_scoped_repair_hint}"
                    ) from exc
                if not force:
                    raise AssembleError(
                        f"run '{run_name}' has a corrupt manifest.assembly.yaml "
                        f"or run_metadata.json: {exc} — pass --force to "
                        "rebuild (add --no-wipe to keep collected results)"
                    ) from exc
                repairing = True

        prior_replicas = (
            prior_ma.get("replicas") if isinstance(prior_ma, dict) else None
        )
        if repairing or prior_replicas is None:
            if not repairing:
                # Legacy single-replica shape (pre-step-5).
                if scoped:
                    raise AssembleError(
                        f"run '{run_name}' is in legacy single-replica shape "
                        f"(pre-step-5) — {_scoped_repair_hint}"
                    )
                if not force:
                    raise AssembleError(
                        f"run '{run_name}' is in legacy single-replica shape "
                        "(pre-step-5); create a fresh run to use --replicas, "
                        "or pass --force to rebuild (add --no-wipe to keep "
                        "collected results)."
                    )
            replicas_effective = 1 if replicas is None else replicas
        else:
            if scoped and replicas is not None:
                raise AssembleError(
                    "--replicas cannot be combined with --workload/--package: "
                    "a scoped assemble reuses the run's recorded replica count "
                    f"({prior_replicas}). Grow the run with an unscoped "
                    "`sim2real assemble --replicas` first."
                )
            # Scoped reuses the recorded count so that merely narrowing the
            # scope cannot trip the shrink guard below.
            replicas_effective = (
                prior_replicas if scoped else (1 if replicas is None else replicas)
            )
            # Grow-only guard runs BEFORE the drift check because --force
            # bypasses drift but does NOT bypass grow-only.
            if replicas_effective < prior_replicas:
                raise AssembleError(
                    f"run '{run_name}' already has {prior_replicas} "
                    f"replicas; refusing to shrink to {replicas_effective}. "
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
                if scoped:
                    # A scoped assemble leaves manifest.assembly.yaml and
                    # params_hash alone, so it must not generate PipelineRuns
                    # from a manifest the recorded snapshot no longer describes.
                    raise AssembleError(
                        f"transfer.yaml changed since run '{run_name}' was "
                        "assembled; a scoped assemble does not record that. "
                        "Re-assemble unscoped (`sim2real assemble --force`) "
                        "first, then scope."
                    )
                if not force:
                    raise AssembleError(
                        f"manifest content changed since last assemble for "
                        f"run '{run_name}'; pass --force to overwrite."
                    )
            elif replicas_effective > prior_replicas and not force:
                # Additive grow. Unreachable when scoped: replicas_effective is
                # pinned to prior_replicas there.
                additive_grow_from = prior_replicas
        assemble_run.prior_assembled_at = (  # type: ignore[attr-defined]
            str(prior_rm.get("assembled_at") or "")
        )

    if additive_grow_from is not None:
        grow_plan = _additive_grow(
            run_dir,
            manifest,
            prior_replicas=additive_grow_from,
            new_replicas=replicas_effective,
            now_iso=now_iso,
        )
        # Files in cluster/ whose names did not parse were skipped, so their
        # pairs did not grow. Surfaced rather than dropped: otherwise the only
        # symptom is results missing later, with nothing saying why.
        assemble_run.ignored_cluster_files = grow_plan.ignored  # type: ignore[attr-defined]
        # All three side-band lists are empty on this path, and are reset rather
        # than left holding a prior call's values. Grow copies each pair's own i1
        # (issue #877) and merges nothing, so it cannot discover a skipped
        # algorithm, a missing submodule, or a scalar-list conflict. The next
        # full assemble, which does resolve, is what surfaces those.
        assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
        assemble_run.missing_submodules = []  # type: ignore[attr-defined]
        assemble_run.scalar_list_conflicts = []  # type: ignore[attr-defined]
        assemble_run.status = "written"  # type: ignore[attr-defined]
        assemble_run.prior_assembled_at = ""  # type: ignore[attr-defined]
        return

    # 4. Resolve packages (translation load, algorithm filter, baseline +
    # treatment resolution, image + HF-secret injection, workload load,
    # model-name derivation, submodule discovery). This is now the ONLY
    # resolution site: the additive-grow path used to share it, which is what
    # let a grown replica carry a different config than the one it replicates
    # (issue #877). Grow copies each pair's own i1 instead.
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
    assemble_run.scalar_list_conflicts = resolved.scalar_list_conflicts  # type: ignore[attr-defined]

    # 5. Resolve the pair scope and decide what to do per pair -------------
    package_names = [name for name, _ in packages]
    workload_names = [
        wl.get("name", wl.get("workload_name", "unknown"))
        for wl in resolved.workloads
    ]
    pair_scope = resolve_pair_scope(
        workload_names=workload_names,
        package_names=package_names,
        workload_filter=workload_filter,
        package_filter=package_filter,
    )
    iterations = list(range(1, replicas_effective + 1))
    plans = plan_pairs(
        run_dir=run_dir,
        scope=pair_scope,
        iterations=iterations,
        force=force,
        no_wipe=no_wipe,
    )
    regen = [p for p in plans if p.regenerate]
    if not regen:
        # Every pair in scope already has its PipelineRun and --force was not
        # given, so there is genuinely nothing to write. This replaces the old
        # params_hash-keyed no-op, whose message claimed the inputs were
        # unchanged — something params_hash cannot establish, since it covers
        # only transfer.yaml's own fields and not the overlays or workload
        # YAMLs that resolution reads.
        assemble_run.status = "noop"  # type: ignore[attr-defined]
        assemble_run.already_assembled = len(plans)  # type: ignore[attr-defined]
        return

    # 6. Build every regenerated PipelineRun BEFORE deleting anything -------
    # Pure, and it is where the 253-char PipelineRun-name limit is enforced.
    # Doing it here means a name that is too long refuses while the results are
    # still on disk; building after the wipe would strand those pairs with
    # neither results nor a manifest — the failure this whole change exists to
    # prevent.
    built_pipelineruns = build_pipelineruns(
        packages=packages,
        workloads=resolved.workloads,
        run_name=run_name,
        cluster_config=cluster_config,
        pipeline_name=(manifest.get("pipeline") or {}).get("name", "sim2real"),
        observe=manifest.get("blis_observe") or {},
        model_name=resolved.model_name,
        submodule_shas=resolved.submodule_shas,
        submodule_urls=resolved.submodule_urls,
        iterations=iterations,
        triples={(p.workload, p.package, p.iteration) for p in regen},
    )

    # 7. Confirm before destroying measured data ---------------------------
    # Only prompt when --force was NOT what authorized the regeneration. With
    # --force the wipe is the long-documented behavior and stays
    # non-interactive so scripted use keeps working (--no-wipe is the opt-out).
    # Without it, this is row 2 of the table — PipelineRun absent, results
    # present — where measured data would die on a pair the operator never
    # typed a flag for. The prompt precedes every write, so declining leaves
    # the run exactly as it was.
    to_wipe = [p for p in regen if p.wipe_results]
    if to_wipe and not force and not assume_yes:
        if not _confirm_results_wipe([p.results_display for p in to_wipe]):
            raise AssembleError(
                "aborted — pass --no-wipe to re-assemble while keeping the "
                "collected results, or --yes to confirm the wipe"
            )
    # 8. Prune cluster/ files the manifest no longer describes -------------
    # Unscoped only. Needed because the run dir is no longer cleared: a
    # transfer.yaml edit that drops a workload or algorithm would otherwise
    # leave its PipelineRun on disk for deploy's glob to find.
    #
    # Deliberately ordered BEFORE the results wipe and before any write. Both
    # are deletions, and this is the cheap one — regenerable YAML rather than
    # measured data — so letting it go first means a read-only filesystem or a
    # permissions problem aborts while the results are still on disk.
    if not scoped:
        assemble_run.pruned_files = prune_orphan_cluster_files(  # type: ignore[attr-defined]
            run_dir,
            expected_pipelineruns={
                pipelinerun_filename(wl, pkg, i)
                for wl in workload_names
                for pkg in package_names
                for i in iterations
            },
            package_names=package_names,
        )

    # 9. Wipe the results of pairs being regenerated -----------------------
    for done, p in enumerate(to_wipe):
        try:
            shutil.rmtree(p.results_path)
        except OSError as exc:
            # Normalized to AssembleError so the CLI reports it in its own
            # error shape. The count matters: the operator needs to know how
            # much is already gone, and that nothing has been regenerated to
            # replace it.
            raise AssembleError(
                f"failed to remove {p.results_display}: {exc}. "
                f"{done} of {len(to_wipe)} result director(ies) were already "
                "removed and no PipelineRun has been regenerated yet — re-run "
                "to finish, or add --no-wipe to keep the rest."
            ) from exc
    assemble_run.wiped_results = [  # type: ignore[attr-defined]
        p.results_display for p in to_wipe
    ]

    # 10. Snapshot assembly slice + params_hash ---------------------------
    # Skipped when scoped: params_hash must keep meaning "the last state in
    # which the WHOLE run was consistent", so a later unscoped assemble still
    # sees manifest drift rather than reporting nothing to do.
    run_dir.mkdir(parents=True, exist_ok=True)
    params_hash = ""
    if not scoped:
        manifest_assembly_path = write_manifest_assembly(
            run_dir, manifest, now_iso=now_iso, replicas=replicas_effective
        )
        params_hash = compute_params_hash(manifest_assembly_path)

    # 11. Write scenario YAMLs for the packages in scope -------------------
    # These are advisory copies — the authoritative scenario is inlined into
    # each PipelineRun — so rewriting them to the current overlay state is
    # safe, and out-of-scope packages keep theirs untouched.
    packages_in_scope = {pkg for _, pkg in pair_scope}
    write_resolved_scenarios(
        run_dir, [(name, res) for name, res in packages if name in packages_in_scope]
    )

    # 12. Write the PipelineRuns built in step 6 ---------------------------
    write_pipelineruns(run_dir, built_pipelineruns)

    # 13. Write run_metadata.json -----------------------------------------
    # Skipped when scoped, for the same reason as manifest.assembly.yaml: it
    # carries params_hash and the replica count, both of which describe the
    # whole run.
    if not scoped:
        # image_tag is a single-image summary field for backward-compat; use the
        # first *kept* algorithm's image_ref. Reading from kept_algos rather
        # than tout["algorithms"][0] guards against the multi-algo case where
        # translated_algos contains an algo that was filtered out of the run,
        # which would otherwise leak a null image_tag past the built-algo check.
        run_meta_image_tag = (
            translated_algos[kept_algos[0]["name"]]["image_ref"]
            if kept_algos else ""
        )
        # `scenario` is required by transfer.yaml (validated in sim2real.py
        # before this call) and is used at deploy time to scope the progress
        # ConfigMap per (scenario, run) so cross-experiment-root runs don't
        # collide (#551).
        write_run_metadata(
            run_dir,
            {
                "version": 1,
                "run_name": run_name,
                "translation_hash": translation_hash,
                "cluster_id": cluster_id,
                "params_hash": params_hash,
                "image_tag": run_meta_image_tag,
                "replicas": replicas_effective,
                "assembled_at": now_iso,
                "scenario": manifest.get("scenario", "") or "",
            },
        )
    # Skipped-algorithm list exposed for the CLI wrapper to surface as warnings.
    assemble_run.skipped_algorithms = resolved.skipped_algo_names  # type: ignore[attr-defined]
    assemble_run.status = "written"  # type: ignore[attr-defined]
    assemble_run.prior_assembled_at = ""  # type: ignore[attr-defined]


# Initialize side-band attributes so `getattr` in the CLI works on first call.
assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
assemble_run.missing_submodules = []  # type: ignore[attr-defined]
assemble_run.scalar_list_conflicts = []  # type: ignore[attr-defined]
assemble_run.status = "written"  # type: ignore[attr-defined]
assemble_run.prior_assembled_at = ""  # type: ignore[attr-defined]
assemble_run.already_assembled = 0  # type: ignore[attr-defined]
assemble_run.pruned_files = []  # type: ignore[attr-defined]
assemble_run.wiped_results = []  # type: ignore[attr-defined]
assemble_run.ignored_cluster_files = []  # type: ignore[attr-defined]
