"""transfer.yaml translation/assembly slice partitioner.

Partitions a loaded v3 ``transfer.yaml`` dict into two slices:

- **Translation slice** — fields that determine the translation result
  (scenario, component, context, per-algorithm source). Hashed to detect
  when a re-translation is required.
- **Assembly slice** — everything else (workloads, baselines, per-algorithm
  defaults, framework defaults toggles, etc.). Affects how a benchmark run
  is built, not what gets translated.

Slice membership is design-locked here; future additive manifest fields
default to the assembly slice unless their stem is added to
``TRANSLATION_FIELDS``. Consumed by ``sim2real translate`` (hashes the
translation slice to key ``translations/<hash>/``) and ``sim2real
assemble`` (snapshots the assembly slice into
``runs/<R>/manifest.assembly.yaml``).

Two hashes are exported:

- ``translation_hash`` — SHA-256 over canonical (sorted-key,
  no-whitespace) JSON of the translation slice. Stable across YAML
  formatter / writer differences. No production consumer today; kept
  exported for tests and future slice-keyed callers. The BYO
  ``translation register`` path uses a separate
  ``_compute_translation_hash`` in ``pipeline/sim2real.py`` that folds
  image digest + config bytes + algorithm name.
- ``translation_hash_with_sources`` — folds each
  ``algorithms[i].source`` file's bytes (SHA-256) into the digest,
  so edits to algorithm source under a stable ``transfer.yaml`` still
  produce a new hash. Used by skill-driven ``sim2real translate``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

TRANSLATION_FIELDS: list[str] = [
    "scenario",
    "component",
    "context",
    "algorithms[*].source",
]
"""Design-locked list of translation-slice members.

The ``[*]`` suffix denotes per-element projection on a list-valued field.
``algorithms[*].config`` appears in the 3D proposal but does not exist in
v3 — it is intentionally omitted. If a Step 2+ consumer surfaces the need,
it gets added as an additive extension at that time.
"""

# Top-level keys whose values are projected into the translation slice.
_TRANSLATION_TOP_KEYS = ("scenario", "component", "context")

# Per-algorithm field projections. ``name`` is included on both sides as
# identity — without it, the projected entries cannot be tied back to the
# manifest's algorithms.
_ALGORITHM_TRANSLATION_KEYS = ("source",)
_ALGORITHM_ASSEMBLY_KEYS = ("defaults",)


def translation_slice(manifest: dict) -> dict:
    """Return the translation-slice projection of ``manifest``.

    Includes scenario, component, context, and per-algorithm
    ``{name, source}`` (sorted by name for stability). Omits fields that
    are absent in the input rather than emitting null defaults — keeps
    the slice (and its hash) tight against manifest reality.
    """
    out: dict[str, Any] = {}
    for key in _TRANSLATION_TOP_KEYS:
        if key in manifest:
            out[key] = manifest[key]
    algos = manifest.get("algorithms")
    if algos:
        out["algorithms"] = [
            _project_algorithm(a, _ALGORITHM_TRANSLATION_KEYS)
            for a in _sorted_by_name(algos)
        ]
    return out


def assembly_slice(manifest: dict) -> dict:
    """Return the assembly-slice projection of ``manifest``.

    Everything not in the translation slice: ``workloads``, ``baselines``,
    ``defaults``, ``kind``, ``version``, ``pipeline``, ``blis_observe``,
    any other top-level field, plus per-algorithm ``{name, defaults}``
    (sorted by name).
    """
    out: dict[str, Any] = {}
    for key, value in manifest.items():
        if key in _TRANSLATION_TOP_KEYS or key == "algorithms":
            continue
        out[key] = value
    algos = manifest.get("algorithms")
    if algos:
        out["algorithms"] = [
            _project_algorithm(a, _ALGORITHM_ASSEMBLY_KEYS)
            for a in _sorted_by_name(algos)
        ]
    return out


def translation_hash(manifest: dict) -> str:
    """SHA-256 over canonical JSON of ``translation_slice(manifest)``.

    Canonical = sorted keys at every depth, no whitespace separators.
    Stable across YAML re-serialization and dict key reordering.
    """
    canonical = json.dumps(
        translation_slice(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def translation_hash_with_sources(manifest: dict, experiment_root: Path) -> str:
    """SHA-256 over the translation slice folded with each algorithm's source bytes.

    Reads every ``algorithms[i].source`` file from ``experiment_root``,
    computes its SHA-256, and folds the per-algorithm digests (sorted by
    algorithm name) into a canonical envelope alongside
    ``translation_slice(manifest)``. Callers use this when the translation
    result depends on algorithm source contents — the skill-driven step-2
    ``translate`` path.

    Envelope shape (canonical JSON, sorted keys, no whitespace):

        {"slice": <translation_slice>,
         "sources": [{"name": <str>, "sha256": <hex>}, ...  # sorted by name
                    ]}

    Algorithm-order normalization: ``sources`` is sorted by ``name`` so
    ``[a, b]`` and ``[b, a]`` produce the same hash. Algorithms without a
    ``source`` field are skipped (they contribute nothing to the sources
    list — matches the projection semantics of ``translation_slice``).

    Raises:
        AssembleError: if any ``algorithms[i].source`` file does not exist
        under ``experiment_root``. Message format: ``source file not
        found: <path>``.
    """
    # Deferred import to avoid the ``assemble_run -> slicer`` import cycle.
    from pipeline.lib.assemble_run import AssembleError

    sources: list[dict[str, str]] = []
    algos = manifest.get("algorithms") or []
    for algo in _sorted_by_name(algos):
        if not isinstance(algo, dict):
            continue
        source_rel = algo.get("source")
        if source_rel is None:
            continue
        source_path = experiment_root / source_rel
        try:
            data = source_path.read_bytes()
        except FileNotFoundError as exc:
            raise AssembleError(f"source file not found: {source_path}") from exc
        sources.append(
            {
                "name": algo.get("name", ""),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    envelope = {"slice": translation_slice(manifest), "sources": sources}
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _project_algorithm(algo: dict, keys: tuple[str, ...]) -> dict:
    out: dict[str, Any] = {}
    if "name" in algo:
        out["name"] = algo["name"]
    for key in keys:
        if key in algo:
            out[key] = algo[key]
    return out


def _sorted_by_name(algos: list) -> list:
    return sorted(algos, key=lambda a: a.get("name", "") if isinstance(a, dict) else "")
