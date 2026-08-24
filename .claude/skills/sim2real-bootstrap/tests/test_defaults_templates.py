"""Shape and merge-safety tests for the shipped framework-defaults fragments.

`templates/defaults/*.yaml` is copied verbatim into every bootstrapped experiment
repo and deep-merged under each baseline by `sim2real assemble`. Nothing else
validates their *content* — `test_byo.py` only checks that the emitted
`transfer.yaml` inventory matches the directory listing, which it derives by glob,
so a fragment with a malformed shape or an unmergeable key would ship silently.

These tests are deliberately glob-driven: adding a fragment requires no test edit,
but the new fragment must satisfy the same contract as the existing ones.

Issue #839 (two shipped fragments were wrong, five were missing) is what motivated
them. The `additionalFlags` guard below is the narrow, statically-decidable slice of
#841 — it catches keys that provably cannot survive the merge chain, rather than
comparing resolved scenarios, which is what #841 tracks.
"""
from pathlib import Path

import pytest
import yaml

_SKILL_DIR = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _SKILL_DIR / "templates" / "defaults"

# Repo root: .claude/skills/sim2real-bootstrap -> up three.
_REPO_ROOT = _SKILL_DIR.parents[2]


def _fragments():
    return sorted(_TEMPLATE_DIR.glob("*.yaml"))


def _fragment_ids():
    return [p.stem for p in _fragments()]


@pytest.fixture(scope="module")
def deep_merge():
    """Import the real merge used by assemble, so these tests track its behavior."""
    import sys

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from pipeline.lib.values import deep_merge as _dm

    return _dm


def test_template_dir_is_not_empty():
    assert _fragments(), f"no fragments found under {_TEMPLATE_DIR}"


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_fragment_parses_as_mapping(path):
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), (
        f"{path.name}: must be a YAML mapping, got {type(data).__name__}"
    )


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_fragment_has_only_scenario_at_top_level(path):
    """render_plans merges "defaults -> shared -> stack", so only `scenario:` and
    `shared:` are read from a scenario file's top level. Any other top-level key is
    silently ignored — an inert fragment by construction."""
    data = yaml.safe_load(path.read_text())
    assert set(data) <= {"scenario", "shared"}, (
        f"{path.name}: unexpected top-level key(s) "
        f"{sorted(set(data) - {'scenario', 'shared'})} — only `scenario` and `shared` "
        "are read from a scenario file's top level; anything else is silently ignored"
    )
    assert "scenario" in data, f"{path.name}: missing top-level `scenario`"


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_fragment_scenario_is_single_entry_named_defaults(path):
    """`_align_overlay_name` realigns scenario[0].name to the bundle's name so both
    sides collapse into one entry. A fragment with more than one entry, or a name
    other than `defaults`, produces a phantom scenario (issue #516)."""
    scenario = yaml.safe_load(path.read_text())["scenario"]
    assert isinstance(scenario, list), f"{path.name}: `scenario` must be a list"
    assert len(scenario) == 1, (
        f"{path.name}: `scenario` must have exactly one entry, got {len(scenario)}"
    )
    assert scenario[0].get("name") == "defaults", (
        f"{path.name}: scenario[0].name must be 'defaults', got "
        f"{scenario[0].get('name')!r}"
    )


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_fragment_sets_something_beyond_its_name(path):
    """A fragment whose only key is `name` merges to nothing."""
    entry = yaml.safe_load(path.read_text())["scenario"][0]
    assert set(entry) - {"name"}, (
        f"{path.name}: scenario[0] sets nothing beyond `name` — the fragment is inert"
    )


# Scalar-list keys a fragment is knowingly allowed to set, as {stem: {dotted key}}.
#
# A scalar list set from the defaults layer only survives if NO downstream layer
# sets the same key, because `_merge_lists` Tier 1 replaces rather than appends.
# That makes every entry here a standing fragility, not an endorsement: an
# experiment whose `baselines/<name>.yaml` sets the same key silently wins, and the
# fragment's value never reaches the rendered output. Each entry must be justified
# below and warned about in the fragment's own header comment.
_ALLOWED_SCALAR_LISTS = {
    # Envoy sidecar CLI args. Kept here rather than in `baselines/<name>.yaml`
    # because `--concurrency 8` is only meaningful next to the `limits.cpu: "8"`
    # the same fragment sets, and splitting them across two files is what let them
    # drift apart before. The tradeoff: a baseline that sets `router.proxy.args`
    # itself takes the whole list and keeps the fragment's CPU limit, breaking the
    # coupling silently. epponly.yaml's header says so.
    "epponly": {"router.proxy.args"},
}


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_fragment_sets_no_unacknowledged_scalar_list_key(path):
    """Scalar lists set from this layer are fragile, so each one must be deliberate.

    `_merge_lists` Tier 1 replaces (rather than appends) whenever either side holds
    a non-dict item. The defaults overlay is the FIRST layer in `resolve_baseline`'s
    chain — `deep_merge(defaults, bundle)` then `deep_merge(..., overlay)` — so a
    scalar list it sets is discarded the moment any downstream layer sets the same
    key. `vllm.additionalFlags` is the case that shipped inert for months (#839).

    Rather than forbid scalar lists outright (which would rule out a fragment that
    legitimately owns a key nothing downstream touches), require that each one be
    listed in `_ALLOWED_SCALAR_LISTS` with a reason. A new fragment cannot add one
    by accident, and the allowlist is the inventory of keys to re-check whenever an
    experiment's baseline grows.
    """
    entry = yaml.safe_load(path.read_text())["scenario"][0]
    found = set()

    def walk(node, trail):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, trail + [str(key)])
        elif isinstance(node, list) and node:
            if any(not isinstance(item, dict) for item in node):
                found.add(".".join(trail))

    walk(entry, [])
    allowed = _ALLOWED_SCALAR_LISTS.get(path.stem, set())

    unacknowledged = sorted(found - allowed)
    assert not unacknowledged, (
        f"{path.name}: sets scalar list(s) {unacknowledged} — a list of non-dicts is "
        "REPLACED by any downstream layer (_merge_lists Tier 1), so a value set here "
        "cannot be relied on to reach the rendered output. Prefer a typed "
        "scalar/bool key, or a list of dicts carrying `name` (which merges by key). "
        "If the key genuinely belongs in this layer, add it to "
        "_ALLOWED_SCALAR_LISTS with a reason and warn about it in the fragment's "
        "header comment."
    )

    stale = sorted(allowed - found)
    assert not stale, (
        f"{path.name}: _ALLOWED_SCALAR_LISTS still lists {stale}, but the fragment no "
        "longer sets it — drop the allowlist entry."
    )


def test_scalar_list_allowlist_has_no_entries_for_missing_fragments():
    """An allowlist entry for a deleted fragment would silently permit a future
    fragment that happens to reuse the stem."""
    stems = set(_fragment_ids())
    orphans = sorted(set(_ALLOWED_SCALAR_LISTS) - stems)
    assert not orphans, (
        f"_ALLOWED_SCALAR_LISTS names fragment(s) that no longer exist: {orphans}"
    )


def test_all_fragments_merge_without_error(deep_merge):
    """Merging every fragment in filename-sorted order must not raise.

    Mirrors what `load_defaults_overlay` does with nothing disabled. `_merge_lists`
    raises ValueError on duplicate Kubernetes object identities and on positionally
    folding manifests with differing markers — so two fragments contributing
    conflicting `extraObjects` would fail assemble for every bootstrapped
    experiment. Catch it here instead.
    """
    merged: dict = {}
    for path in _fragments():
        fragment = yaml.safe_load(path.read_text())
        try:
            merged = deep_merge(merged, fragment)
        except ValueError as exc:  # pragma: no cover - only on a real conflict
            pytest.fail(f"{path.name} conflicts with an earlier fragment: {exc}")
    assert merged["scenario"][0]["name"] == "defaults"


def test_no_two_fragments_set_the_same_leaf_key(deep_merge):
    """Two fragments writing the same leaf make one of them silently order-dependent.

    Merge order is filename-sorted (`load_defaults_overlay`), so the later stem wins
    and the earlier fragment's value never reaches the output — the inert-fragment
    failure mode of #839, in its within-layer form. Distinct leaves are fine; this
    only fires on a genuine collision.
    """
    seen: dict[str, str] = {}
    collisions = []

    def walk(node, trail, stem):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, trail + [str(key)], stem)
            return
        path_key = ".".join(trail)
        if path_key in seen and seen[path_key] != stem:
            collisions.append(f"{path_key} (set by {seen[path_key]} and {stem})")
        else:
            seen[path_key] = stem

    for path in _fragments():
        entry = yaml.safe_load(path.read_text())["scenario"][0]
        walk({k: v for k, v in entry.items() if k != "name"}, [], path.stem)

    assert not collisions, (
        "fragments set overlapping leaf keys; filename-sorted merge order decides "
        f"the winner and the loser is inert: {collisions}"
    )
