# Design: Named-Key List Merge for `_deep_merge`

## Context

`_deep_merge(base, overlay)` in `tools/transfer_cli.py:1780` replaces lists wholesale when both sides provide a list at the same path. This silently drops values from `base` that live inside list items.

**Concrete bug:** `env_defaults.yaml` has `decode.containers: [{modelCommand: vllmServe}]` and `algorithm_values.yaml` has `decode.containers: [{image: vllm/..., extraConfig: {...}}]`. After merge, `modelCommand` is gone — the entire containers list from env_defaults is discarded. This caused the deploy-model Tekton task to fail (missing `modelCommand`) and the wait-for-model step to skip pod readiness checks (missing `decode.create`).

**Systemic risk:** Kubernetes/Helm values are list-heavy (`env`, `args`, `volumeMounts`, `volumes`, `flags`, `tolerations`, etc.). As the pipeline config grows, any list contributed by both files will lose env_defaults fields silently.

## Design: Three-Tier List Merge Strategy

When both `base[key]` and `overlay[key]` are lists, apply these tiers in order:

### Tier 1: Scalar lists → replace (current behavior)
If either list contains non-dict items, overlay replaces base entirely.
- `["a", "b"]` + `["c"]` → `["c"]`
- `[1, {"x": 2}]` + `[3]` → `[3]` (mixed → replace)

### Tier 2: Named-key lists → merge by key
If all items in both lists are dicts and a common key field is detected, merge items matched by that key. Unmatched base items are preserved in position. Unmatched overlay items are appended.

Key detection candidates (in priority order): `name`, `mountPath`, `containerPort`.
A key is selected only if **every** dict in **both** lists contains it.

Example (flags with `name` key):
```yaml
base:    [{name: v, value: 1}, {name: debug, value: 0}]
overlay: [{name: v, value: 3}, {name: timeout, value: 30}]
result:  [{name: v, value: 3}, {name: debug, value: 0}, {name: timeout, value: 30}]
```

### Tier 3: Keyless dict lists → positional merge
If all items are dicts but no common key field is found, merge by index: `base[i]` deep-merged with `overlay[i]`. Surplus items from either side are appended.

Example (containers without `name` field):
```yaml
base:    [{modelCommand: vllmServe}]
overlay: [{image: vllm/vllm-openai:v0.11.0, extraConfig: {vllm: {gpuMem: 0.9}}}]
result:  [{modelCommand: vllmServe, image: vllm/vllm-openai:v0.11.0, extraConfig: {vllm: {gpuMem: 0.9}}}]
```

### Empty list semantics
- `overlay = []` → **replaces** base with `[]` (explicit empty means "clear this list")
- `base = []` → returns overlay

This matches JSON merge patch behavior. If someone explicitly sets `[]`, they intend to clear.

## Implementation

### New helpers (add above `_deep_merge` at ~line 1779)

```python
_LIST_KEY_CANDIDATES = ("name", "mountPath", "containerPort")

def _detect_list_key(base_list: list, overlay_list: list) -> str | None:
    """Return the first key from candidates present in ALL dicts in both lists."""
    all_dicts = base_list + overlay_list
    if not all_dicts:
        return None
    for candidate in _LIST_KEY_CANDIDATES:
        if all(candidate in d for d in all_dicts):
            return candidate
    return None

def _merge_lists(base_list: list, overlay_list: list) -> list:
    """Merge two lists using tiered strategy."""
    import copy

    # Empty overlay = explicit clear
    if not overlay_list:
        return []
    if not base_list:
        return copy.deepcopy(overlay_list)

    # Tier 1: if not both all-dicts, replace
    if not (all(isinstance(x, dict) for x in base_list)
            and all(isinstance(x, dict) for x in overlay_list)):
        return copy.deepcopy(overlay_list)

    # Tier 2: named-key merge
    key_field = _detect_list_key(base_list, overlay_list)
    if key_field is not None:
        overlay_by_key = {item[key_field]: item for item in overlay_list}
        result = []
        seen_keys = set()
        for bitem in base_list:
            k = bitem[key_field]
            seen_keys.add(k)
            if k in overlay_by_key:
                result.append(_deep_merge(bitem, overlay_by_key[k]))
            else:
                result.append(copy.deepcopy(bitem))
        for oitem in overlay_list:
            if oitem[key_field] not in seen_keys:
                result.append(copy.deepcopy(oitem))
        return result

    # Tier 3: positional merge
    result = []
    for i in range(max(len(base_list), len(overlay_list))):
        if i < len(base_list) and i < len(overlay_list):
            result.append(_deep_merge(base_list[i], overlay_list[i]))
        elif i < len(base_list):
            result.append(copy.deepcopy(base_list[i]))
        else:
            result.append(copy.deepcopy(overlay_list[i]))
    return result
```

### Modified `_deep_merge` (line 1780)

Add one `elif` branch:

```python
def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep-merge overlay onto base. Dict keys merged recursively.
    Lists of dicts are merged by named key or positional index.
    Lists of scalars are replaced entirely. Returns a new dict (deep copy).
    """
    import copy
    result = copy.deepcopy(base)
    for key, oval in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(oval, dict):
            result[key] = _deep_merge(result[key], oval)
        elif key in result and isinstance(result[key], list) and isinstance(oval, list):
            result[key] = _merge_lists(result[key], oval)
        else:
            result[key] = copy.deepcopy(oval)
    return result
```

## Callers affected

1. `cmd_merge_values` (line 1871): `_deep_merge(env_data, alg_data)` — primary merge. The `containers` list will now be positionally merged instead of replaced.
2. `_flatten_gaie_shared` (line 1821): `_deep_merge(shared_helm, phase_helm)` — merges shared into baseline/treatment. The `flags` list will now be name-key merged if both sides provide it.

Both callers benefit from the new behavior with no code changes.

## Files to modify

| File | Change |
|------|--------|
| `tools/transfer_cli.py` | Add `_LIST_KEY_CANDIDATES`, `_detect_list_key`, `_merge_lists`; modify `_deep_merge` |
| `tools/test_transfer_cli.py` | Update `test_list_replacement`; add 6 new tests |
| `CLAUDE.md` | Update "Lists are replaced" note in Two-Layer Config section |

## Test plan

### Update existing

**`test_list_replacement`** → rename to `test_list_of_dicts_positional_merge`:
- Same setup: base has `[{image: old, modelCommand: vllmServe}]`, overlay has `[{image: new}]`
- **New assertion**: `containers == [{image: new, modelCommand: vllmServe}]` (positionally merged)
- `modelCommand` is now **preserved**, not lost

### Add new tests

| Test | Scenario | Expected |
|------|----------|----------|
| `test_scalar_list_replacement` | `{x: [a, b]}` + `{x: [c]}` | `{x: [c]}` — scalars still replaced |
| `test_named_key_list_merge` | flags with `name` key, overlap + new | matched merged by `name`, unmatched appended |
| `test_positional_merge_keyless_dicts` | containers without `name`, both have index 0 | index-0 items deep-merged |
| `test_positional_merge_unequal_lengths` | base has 2 items, overlay has 1 (and reverse) | merged at overlap, surplus preserved |
| `test_empty_overlay_clears_list` | base has `[{a: 1}]`, overlay has `[]` | `[]` — explicit clear |
| `test_mixed_list_falls_back_to_replace` | base has `[{a: 1}, "scalar"]`, overlay has `[{b: 2}]` | `[{b: 2}]` — mixed → replace |
| `test_gaie_flags_named_key_merge` | shared flags + phase flags, both keyed by `name` | merged by `name` field |

### Run

```bash
.venv/bin/python -m pytest tools/test_transfer_cli.py -v -k "merge or list or scalar or positional or empty or mixed or flags"
```

### Integration verification

After implementation, re-run merge-values with real files and confirm `modelCommand: vllmServe` survives:

```bash
.venv/bin/python tools/transfer_cli.py merge-values \
  --env config/env_defaults.yaml \
  --algorithm workspace/tekton/algorithm_values.yaml \
  --out /tmp/test_values.yaml
grep modelCommand /tmp/test_values.yaml  # should show vllmServe
```

Then remove the `modelCommand: vllmServe` workaround from `algorithm_values.yaml` (line 13) and verify it still survives via the merge from env_defaults.

## Rollback of workarounds

After this change lands, remove the `modelCommand: vllmServe` line that was manually added to `workspace/tekton/algorithm_values.yaml` as a workaround. It should come from `env_defaults.yaml` naturally via the merge.
