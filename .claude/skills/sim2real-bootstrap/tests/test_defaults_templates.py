"""Shape and merge-safety tests for the shipped framework-defaults fragments.

`templates/defaults/*.yaml` is copied verbatim into every bootstrapped experiment
repo and deep-merged under each baseline by `sim2real assemble`. Nothing else
validates their *content* — `test_byo.py` only checks that the emitted
`transfer.yaml` inventory matches the directory listing, which it derives by glob,
so a fragment with a malformed shape or an unmergeable key would ship silently.

These tests are deliberately glob-driven: adding a fragment requires no test edit,
but the new fragment must satisfy the same contract as the existing ones.

Issue #839 (two shipped fragments were wrong, five were missing) is what motivated
them. The scalar-list guard below is the narrow, statically-decidable slice of
#841 — it catches keys that provably cannot survive the merge chain, rather than
comparing resolved scenarios, which is what #841 tracks.

Note on scope since #851: `**.vllm.additionalFlags` no longer replaces — it merges
by flag name, so a value set from this layer does survive downstream layers. The
guard therefore no longer applies to that key (and no fragment sets it today). It
still applies to every other scalar list, which `_merge_lists` Tier 1 continues to
replace wholesale.
"""
import itertools
import sys
from pathlib import Path

import pytest
import yaml

_SKILL_DIR = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _SKILL_DIR / "templates" / "defaults"

# Repo root: .claude/skills/sim2real-bootstrap -> up three.
_REPO_ROOT = _SKILL_DIR.parents[2]

# Make `pipeline` importable regardless of cwd, matching what the `deep_merge`
# fixture below does — the import on the next line runs at collection time, so it
# cannot rely on that fixture.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Sourced from the merge implementation rather than re-listing the exempt paths
# here, so this guard cannot drift from the rule it is guarding (#851).
from pipeline.lib.values import _is_flag_list_path  # noqa: E402


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
# (`**.vllm.additionalFlags` is the one exception since #851 — it merges by flag
# name, so it needs no allowlist entry. Every key below still replaces.)
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
    # Linux capabilities to add to the vLLM container, per role. `capabilities.add`
    # has no key field to merge on — it is a bare list of capability names — so
    # Tier 1 applies and a baseline setting the same key takes the whole list. The
    # fragment is the right home anyway: the capability set is inseparable from the
    # SCC grant its header documents, and splitting it across two files would let
    # the two drift the way epponly's coupling did. Ships DISABLED, so it reaches a
    # rendered pod only when an operator has enabled it deliberately.
    # pod-capabilities.yaml's header says so.
    "pod-capabilities": {
        "decode.extraContainerConfig.securityContext.capabilities.add",
        "prefill.extraContainerConfig.securityContext.capabilities.add",
    },
}


def _scalar_list_paths(entry: dict) -> set:
    """Dotted key paths under ``entry`` holding a list with any non-dict item.

    Paths matching ``_FLAG_LIST_PATH_SUFFIXES`` are omitted: since #851 they
    merge by flag name, so a value set from the defaults layer DOES survive
    downstream layers and needs no allowlist entry. Every other scalar list is
    still replaced wholesale by any downstream layer that sets the same key.
    """
    found: set = set()

    def walk(node, trail):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, trail + [str(key)])
        elif isinstance(node, list) and node:
            if any(not isinstance(item, dict) for item in node):
                if _is_flag_list_path(tuple(trail)):
                    return
                found.add(".".join(trail))

    walk(entry, [])
    return found


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_fragment_sets_no_unacknowledged_scalar_list_key(path):
    """Scalar lists set from this layer are fragile, so each one must be deliberate.

    `_merge_lists` Tier 1 replaces (rather than appends) whenever either side holds
    a non-dict item. The defaults overlay is the FIRST layer in `resolve_baseline`'s
    chain — `deep_merge(defaults, bundle)` then `deep_merge(..., overlay)` — so a
    scalar list it sets is discarded the moment any downstream layer sets the same
    key. `vllm.additionalFlags` is the case that shipped inert for months (#839);
    it is no longer subject to this (Tier 0 merges it by flag name since #851), but
    every other scalar list still is. Since #851, `sim2real assemble` also warns at
    resolve time when two layers both set a still-replacing scalar list, so a
    fragment that slips past this static guard is caught at run time too.

    Rather than forbid scalar lists outright (which would rule out a fragment that
    legitimately owns a key nothing downstream touches), require that each one be
    listed in `_ALLOWED_SCALAR_LISTS` with a reason. A new fragment cannot add one
    by accident, and the allowlist is the inventory of keys to re-check whenever an
    experiment's baseline grows.
    """
    entry = yaml.safe_load(path.read_text())["scenario"][0]
    found = _scalar_list_paths(entry)
    allowed = _ALLOWED_SCALAR_LISTS.get(path.stem, set())

    unacknowledged = sorted(found - allowed)
    assert not unacknowledged, (
        f"{path.name}: sets scalar list(s) {unacknowledged} — at these key paths a "
        "list of non-dicts is REPLACED by any downstream layer (_merge_lists "
        "Tier 1), so a value set here cannot be relied on to reach the rendered "
        "output. Prefer a typed scalar/bool key, or a list of dicts carrying "
        "`name` (which merges by key). Paths matching "
        "_FLAG_LIST_PATH_SUFFIXES (`**.vllm.additionalFlags`) never reach this "
        "assertion — they merge by flag name since #851. If the key genuinely "
        "belongs in this layer, add it to _ALLOWED_SCALAR_LISTS with a reason and "
        "warn about it in the fragment's header comment."
    )

    stale = sorted(allowed - found)
    assert not stale, (
        f"{path.name}: _ALLOWED_SCALAR_LISTS still lists {stale}, but the fragment no "
        "longer sets it — drop the allowlist entry."
    )


@pytest.mark.parametrize(
    "trail, exempt",
    [
        (["decode", "vllm", "additionalFlags"], True),
        (["prefill", "vllm", "additionalFlags"], True),
        (["router", "proxy", "args"], False),
        (
            ["decode", "extraContainerConfig", "securityContext",
             "capabilities", "add"],
            False,
        ),
        (["router", "additionalFlags"], False),
    ],
)
def test_guard_exempts_exactly_the_flag_merged_paths(trail, exempt):
    """The guard's message and comments promise `**.vllm.additionalFlags` is
    exempt; this pins that the walk actually implements it, and that nothing
    else is exempted by accident.

    Without this, the guard and its own failure text could disagree — it would
    demand an allowlist entry for a key while printing that the key needs none.
    """
    assert _is_flag_list_path(tuple(trail)) is exempt


def test_flag_merged_scalar_list_needs_no_allowlist_entry():
    """Exercises the guard's ACTUAL walk (`_scalar_list_paths`), not a copy of it.

    A fragment-shaped entry setting `decode.vllm.additionalFlags` must not be
    reported as unacknowledged, while `router.proxy.args` in the same entry must
    be. Pinning both directions is what keeps the guard honest with its own
    failure message.
    """
    entry = {
        "name": "s",
        "decode": {"vllm": {"additionalFlags": ["--enable-prefix-caching"]}},
        "prefill": {"vllm": {"additionalFlags": ["--max-num-seqs=256"]}},
        "router": {"proxy": {"args": ["--concurrency", "8"]}},
    }
    found = _scalar_list_paths(entry)
    assert "decode.vllm.additionalFlags" not in found
    assert "prefill.vllm.additionalFlags" not in found
    assert found == {"router.proxy.args"}


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


def test_no_fragment_contribution_is_lost_to_another_fragment(deep_merge):
    """A fragment's value must survive the merge with every other fragment.

    Merge order is filename-sorted (`load_defaults_overlay`), so when two fragments
    write the same leaf the later stem can silently displace the earlier one — the
    inert-fragment failure mode of #839, in its within-layer form.

    Sharing a leaf key is NOT sufficient for that to happen, which is why this
    asserts on the merged result rather than on key overlap. `_merge_lists` keeps
    both sides for a list of dicts with a common key field (Tier 2b, merge by key)
    and for Kubernetes manifest lists (Tier 2a, merge by identity); it discards the
    base only for scalar lists (Tier 1, wholesale replace) and for plain scalars
    (later dict value wins). `prefill.extraEnvVars` is the real case: nic-exclusion
    and vllm-keepalive both set it and all of their variables reach the pod, because
    the entries are keyed by `name`.

    So: merge each pair, then check that every leaf value each fragment contributed
    is still present. That tests the property the guard exists for — nothing goes
    inert — instead of a proxy for it that forbids a working combination.
    """
    def leaves(node, trail=()):
        """Flatten to {dotted path: value}, treating lists as leaf values."""
        out = {}
        if isinstance(node, dict):
            for key, value in node.items():
                out.update(leaves(value, trail + (str(key),)))
        else:
            out[".".join(trail)] = node
        return out

    def survives(contributed, merged_value):
        """Is `contributed` still recoverable from the merged value?"""
        if isinstance(contributed, list):
            # Every contributed element must still appear somewhere in the result.
            if not isinstance(merged_value, list):
                return False
            return all(item in merged_value for item in contributed)
        return contributed == merged_value

    entries = {
        path.stem: yaml.safe_load(path.read_text())["scenario"][0]
        for path in _fragments()
    }
    losses = []

    for a, b in itertools.combinations(sorted(entries), 2):
        body_a = {k: v for k, v in entries[a].items() if k != "name"}
        body_b = {k: v for k, v in entries[b].items() if k != "name"}
        shared = set(leaves(body_a)) & set(leaves(body_b))
        if not shared:
            continue
        # Filename-sorted order is what load_defaults_overlay applies.
        merged = deep_merge(deep_merge({}, body_a), body_b)
        merged_leaves = leaves(merged)
        for path_key in sorted(shared):
            for stem, body in ((a, body_a), (b, body_b)):
                contributed = leaves(body)[path_key]
                if not survives(contributed, merged_leaves.get(path_key)):
                    losses.append(
                        f"{path_key}: {stem}'s value is discarded when "
                        f"{a} and {b} merge"
                    )

    assert not losses, (
        "a fragment's contribution does not survive merging with another fragment, "
        "so it is inert whenever both are enabled: " + "; ".join(losses)
    )


# ---------------------------------------------------------------------------
# Fragments that ship disabled (issue #853)
# ---------------------------------------------------------------------------
# Four fragments are copied into baselines/defaults/ but listed in transfer.yaml's
# `defaults.disable`, because none can be a correct always-on default. The list
# lives in SKILL.md's Task 5 template (BLIS mode writes transfer.yaml from it), and
# each fragment declares its own status with a `DISABLED BY DEFAULT` header line.
#
# Two places holding the same fact is exactly how #550 happened -- SKILL.md
# documented an `llm-d-rbac.yaml` fragment that templates/defaults/ never shipped.
# These tests pin the two against each other so neither can drift alone.

_DISABLED_MARKER = "DISABLED BY DEFAULT"
_SKILL_MD = _SKILL_DIR / "SKILL.md"


def _fragments_marked_disabled() -> set[str]:
    return {p.stem for p in _fragments() if _DISABLED_MARKER in p.read_text()}


def _skill_md_disable_list() -> list[str]:
    """Read the `disable:` list items out of SKILL.md's Task 5 transfer.yaml block.

    Line-oriented rather than a YAML parse because the surrounding template holds
    placeholders (`<derived summary>`) that are not valid YAML values.
    """
    lines = _SKILL_MD.read_text().splitlines()
    stems: list[str] = []
    for i, line in enumerate(lines):
        if line.rstrip() != "  disable:":
            continue
        for follow in lines[i + 1:]:
            stripped = follow.strip()
            if stripped.startswith("- "):
                stems.append(stripped[2:].strip())
            elif stripped.startswith("#"):
                continue  # the "Available fragments" comment block
            else:
                break
        break
    return stems


def test_skill_md_disable_list_matches_the_marked_fragments():
    """SKILL.md's `defaults.disable` must name exactly the self-declared ones."""
    in_skill_md = set(_skill_md_disable_list())
    marked = _fragments_marked_disabled()
    assert in_skill_md == marked, (
        "SKILL.md's defaults.disable and the fragments' own "
        f"'{_DISABLED_MARKER}' headers disagree — "
        f"only in SKILL.md: {sorted(in_skill_md - marked)}; "
        f"only in fragment headers: {sorted(marked - in_skill_md)}"
    )


def test_skill_md_disable_list_is_non_empty_and_sorted():
    """Sorted so a new entry lands deterministically rather than wherever."""
    stems = _skill_md_disable_list()
    assert stems, "SKILL.md's defaults.disable is empty — the #853 list is missing"
    assert stems == sorted(stems), f"defaults.disable is not sorted: {stems}"


def test_every_disable_entry_names_a_real_fragment():
    """A stem that matches no file is a silent no-op in load_defaults_overlay."""
    all_stems = {p.stem for p in _fragments()}
    unknown = [s for s in _skill_md_disable_list() if s not in all_stems]
    assert not unknown, (
        f"defaults.disable names stems with no fragment file: {unknown} "
        "(load_defaults_overlay skips by stem, so these disable nothing)"
    )


@pytest.mark.parametrize("path", _fragments(), ids=_fragment_ids())
def test_disabled_fragment_says_why_in_its_header(path):
    """A disabled default is only useful if the operator can tell when to enable it."""
    text = path.read_text()
    if _DISABLED_MARKER not in text:
        pytest.skip("fragment ships enabled")
    header = "\n".join(
        ln for ln in text.splitlines() if ln.startswith("#")
    )
    assert "WHY OFF BY DEFAULT" in header, (
        f"{path.name}: marked disabled but its header never says why, so an "
        "operator cannot judge whether their cluster is the exception"
    )
