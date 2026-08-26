# Flag-Name List Merge (issue #851) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `vllm.additionalFlags` losing every layer's flags but the last, by merging that list by flag name instead of replacing it wholesale.

**Architecture:** Thread an immutable key path (tuple of segments, list indices elided) through `deep_merge` / `_merge_lists` as a keyword-only parameter defaulting to `()`. Add a path-scoped "Tier 0" that merges lists matching a registered path **suffix** by flag name. Every other list keeps its current tier behavior. Separately, scalar lists that still replace record a side-band conflict message so the remaining silent-loss cases become visible.

**Tech Stack:** Python >= 3.10, pytest, PyYAML.

**Spec:** GitHub issue [#851](https://github.com/inference-sim/sim2real/issues/851), plus the two decision comments on it (scoping decisions `issuecomment-5430716546`, vet correction `issuecomment-5430738080`).

## Global Constraints

- Scope is `vllm.additionalFlags` only. No other scalar list changes merge semantics.
- Migration of existing bundles is explicitly OUT of scope.
- `--no-X` and `--X` canonicalize to ONE key. A later layer stating either form overrides the earlier. Emitting both is the failure this closes.
- No separate removal sentinel. `--no-X` is the removal verb for boolean flags; `--key=value` flags are override-only.
- Path matching is by **suffix** with list indices elided, so `decode` and `prefill` are one rule and both merge roots (`assemble_run` merges whole documents, `capacity` merges a single scenario entry) hit it identically.
- Ordering follows the existing Tier 2b convention: base entries first in base order, overlay-only appended.
- A list entry under a flag-merged path that is not a `--` string is refused.
- Two entries in one list collapsing to the same key are refused.
- All 32 existing tests in `pipeline/tests/test_values.py` call `deep_merge(base, overlay)` / `_merge_lists(base, overlay)` positionally with two args. New parameters MUST be keyword-only with defaults so those calls keep working unchanged.
- CI gates: `ruff check pipeline/ .claude/skills/ --select F` and `python -m pytest` with `--cov=pipeline --cov-fail-under=90`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pipeline/lib/values.py` | Modify. Path threading, flag-merge tier, scalar-replace conflict recording. |
| `pipeline/tests/test_values.py` | Modify. New test classes for flag keys, flag merge, path threading, conflict sink. |
| `pipeline/lib/assemble_run.py` | Modify. `_merge_layer` helper, sink through `resolve_baseline` / `resolve_treatment`, new `_ResolvedPackages` field, side-band attr. |
| `pipeline/tests/test_assemble_run.py` | Modify. End-to-end: a `config.md` flag survives an overlay that does not restate it. |
| `pipeline/sim2real.py` | Modify. Print the collected conflicts as warnings next to `skipped_algorithms`. |
| `.claude/skills/sim2real-bootstrap/templates/defaults/vllm-logging.yaml` | Modify. Correct the now-false "additionalFlags is replaced" rationale. |
| `docs/troubleshooting.md` | Modify. Same correction at line 157. |
| `.claude/skills/sim2real-bootstrap/tests/test_defaults_templates.py` | Modify. Assertion message + allowlist rationale note the flag-merged exception. |
| `pipeline/README.md`, `CLAUDE.md` | Modify. Document the new merge behavior per the project's documentation rule. |

---

### Task 1: Flag key + list indexing helpers

**Files:**
- Modify: `pipeline/lib/values.py` (new section after the `_detect_list_key` function)
- Test: `pipeline/tests/test_values.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_FLAG_LIST_PATH_SUFFIXES: tuple[tuple[str, ...], ...]`, `_is_flag_list_path(path: tuple[str, ...]) -> bool`, `_flag_key(entry: str) -> str`, `_index_flags(entries: list, path: tuple[str, ...], side: str) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `pipeline/tests/test_values.py`:

```python
from pipeline.lib.values import (
    deep_merge,
    _merge_lists,
    _k8s_identity,
    _flag_key,
    _index_flags,
    _is_flag_list_path,
)
```

Then append these test classes:

```python
class TestFlagKey:
    def test_valued_flag_keys_on_name(self):
        assert _flag_key("--max-num-seqs=256") == "--max-num-seqs"

    def test_bare_flag_is_its_own_key(self):
        assert _flag_key("--enable-chunked-prefill") == "--enable-chunked-prefill"

    def test_negation_collapses_to_positive_key(self):
        assert _flag_key("--no-enable-prefix-caching") == "--enable-prefix-caching"
        assert _flag_key("--enable-prefix-caching") == "--enable-prefix-caching"

    def test_negation_of_disable_flag_collapses(self):
        # Real llm-d-benchmark pair: --no-disable-uvicorn-access-log is the
        # negation of --disable-uvicorn-access-log.
        assert _flag_key("--no-disable-uvicorn-access-log") == (
            "--disable-uvicorn-access-log"
        )

    def test_value_containing_equals_splits_on_first_only(self):
        assert _flag_key('--kv-transfer-config={"a":"b=c"}') == "--kv-transfer-config"


class TestIsFlagListPath:
    def test_document_rooted_path_matches(self):
        assert _is_flag_list_path(("scenario", "decode", "vllm", "additionalFlags"))

    def test_scenario_entry_rooted_path_matches(self):
        # capacity.py merges scenarios[0] directly, so the path has no leading
        # "scenario" segment. Suffix matching covers both roots.
        assert _is_flag_list_path(("decode", "vllm", "additionalFlags"))

    def test_prefill_role_matches_same_rule(self):
        assert _is_flag_list_path(("scenario", "prefill", "vllm", "additionalFlags"))

    def test_unrelated_scalar_list_does_not_match(self):
        assert not _is_flag_list_path(("scenario", "router", "proxy", "args"))

    def test_shorter_than_suffix_does_not_match(self):
        assert not _is_flag_list_path(("additionalFlags",))
        assert not _is_flag_list_path(())

    def test_additionalflags_under_wrong_parent_does_not_match(self):
        assert not _is_flag_list_path(("scenario", "router", "additionalFlags"))


class TestIndexFlags:
    def test_preserves_order_and_maps_key_to_literal(self):
        out = _index_flags(["--a=1", "--b"], ("vllm", "additionalFlags"), "base")
        assert list(out) == ["--a", "--b"]
        assert out["--a"] == "--a=1"

    def test_non_string_entry_refused(self):
        with pytest.raises(ValueError, match="not a string"):
            _index_flags([256], ("vllm", "additionalFlags"), "base")

    def test_non_flag_entry_refused(self):
        with pytest.raises(ValueError, match="does not start with"):
            _index_flags(["envoy-sidecar"], ("vllm", "additionalFlags"), "overlay")

    def test_duplicate_key_refused(self):
        with pytest.raises(ValueError, match="twice"):
            _index_flags(
                ["--max-num-seqs=1", "--max-num-seqs=2"],
                ("vllm", "additionalFlags"),
                "base",
            )

    def test_negation_pair_in_one_list_is_a_duplicate(self):
        with pytest.raises(ValueError, match="twice"):
            _index_flags(
                ["--enable-prefix-caching", "--no-enable-prefix-caching"],
                ("vllm", "additionalFlags"),
                "base",
            )

    def test_error_message_names_the_path_and_side(self):
        with pytest.raises(ValueError, match=r"decode\.vllm\.additionalFlags: overlay"):
            _index_flags(["nope"], ("decode", "vllm", "additionalFlags"), "overlay")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_values.py -k "FlagKey or IsFlagListPath or IndexFlags" -v`
Expected: FAIL with `ImportError: cannot import name '_flag_key'`

- [ ] **Step 3: Implement**

Insert into `pipeline/lib/values.py` immediately after the `_detect_list_key` function:

```python
# ── Flag-list merge (path-scoped) ─────────────────────────────────────────────

#: Key-path suffixes whose scalar lists merge by flag name rather than being
#: replaced wholesale. Matched as a SUFFIX of the merge path, with list indices
#: elided from the path, so one entry covers both the `decode` and `prefill`
#: roles AND both merge roots in use today: `assemble_run` merges whole
#: documents (`scenario.decode.vllm.additionalFlags`) while `capacity` merges a
#: single scenario entry (`decode.vllm.additionalFlags`). An absolute path
#: anchored at the document root would match the first and silently miss the
#: second. See issue #851.
_FLAG_LIST_PATH_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("vllm", "additionalFlags"),
)


def _is_flag_list_path(path: tuple) -> bool:
    """True when `path` ends with a suffix registered in _FLAG_LIST_PATH_SUFFIXES."""
    return any(
        len(path) >= len(suffix) and tuple(path[-len(suffix):]) == suffix
        for suffix in _FLAG_LIST_PATH_SUFFIXES
    )


def _flag_key(entry: str) -> str:
    """Return the merge key for one CLI-flag list entry.

    `--max-num-seqs=256` keys on `--max-num-seqs`; a bare
    `--enable-chunked-prefill` is its own key. A leading `--no-` is stripped so
    that a flag and its negation collapse to a single key:
    `--enable-prefix-caching` and `--no-enable-prefix-caching` express one
    decision, so a later layer stating either form must override the earlier
    rather than emit both. Emitting both would leave the outcome to vLLM's
    argparse ordering — the silent conflict issue #851 exists to avoid, and the
    reason it rejected simple list concatenation.
    """
    name = entry.split("=", 1)[0]
    if name.startswith("--no-"):
        name = "--" + name[len("--no-"):]
    return name


def _index_flags(entries: list, path: tuple, side: str) -> dict:
    """Return `{flag key: literal entry}` for one flag list, preserving order.

    Raises ValueError on a non-string entry, an entry that is not a `--` flag,
    or two entries collapsing to the same key. Each would otherwise produce a
    silently wrong flag set, which is the failure class this tier closes.
    """
    where = ".".join(path) or "<root>"
    indexed: dict = {}
    for entry in entries:
        if not isinstance(entry, str):
            raise ValueError(
                f"{where}: {side} flag list entry is "
                f"{type(entry).__name__}, not a string: {entry!r} — this path "
                "merges by flag name and cannot key a non-string entry"
            )
        if not entry.startswith("--"):
            raise ValueError(
                f"{where}: {side} flag list entry does not start with '--': "
                f"{entry!r} — this path merges by flag name, so a bare value "
                "(as used in space-separated arg lists like router.proxy.args) "
                "would be mis-keyed as a flag name"
            )
        key = _flag_key(entry)
        if key in indexed:
            raise ValueError(
                f"{where}: {side} flag list states '{key}' twice "
                f"({indexed[key]!r} and {entry!r}) — merging by flag name would "
                "silently drop one. State the flag once; note that '--no-X' and "
                "'--X' are the same key."
            )
        indexed[key] = entry
    return indexed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_values.py -k "FlagKey or IsFlagListPath or IndexFlags" -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/values.py pipeline/tests/test_values.py
git commit -m "feat(values): add flag-name key + flag-list indexing helpers (#851)"
```

---

### Task 2: Thread a key path through deep_merge and _merge_lists

**Files:**
- Modify: `pipeline/lib/values.py` — `deep_merge`, `_merge_lists`, `_merge_by_keyfn`, `_merge_k8s_objects`
- Test: `pipeline/tests/test_values.py`

**Interfaces:**
- Consumes: nothing from Task 1 (independent; Task 3 joins them).
- Produces: `deep_merge(base, overlay, *, path=(), sink=None)`, `_merge_lists(base_list, overlay_list, *, path=(), sink=None)`, `_merge_by_keyfn(base_list, overlay_list, keyfn, *, path=(), sink=None)`, `_merge_k8s_objects(base_list, overlay_list, *, path=(), sink=None)`. `sink` is accepted and threaded but unused until Task 4.

This task is pure plumbing: no observable behavior change. Its test is that the path arriving at a leaf list is correct, and that all 32 pre-existing tests still pass untouched.

- [ ] **Step 1: Write the failing test**

```python
class TestPathThreading:
    def test_path_reaches_nested_list(self, monkeypatch):
        import pipeline.lib.values as values_mod

        seen = []
        real = values_mod._merge_lists

        def spy(base_list, overlay_list, *, path=(), sink=None):
            seen.append(path)
            return real(base_list, overlay_list, path=path, sink=sink)

        monkeypatch.setattr(values_mod, "_merge_lists", spy)
        base = {
            "scenario": [
                {"name": "s", "decode": {"vllm": {"additionalFlags": ["--a"]}}}
            ]
        }
        overlay = {
            "scenario": [
                {"name": "s", "decode": {"vllm": {"additionalFlags": ["--b"]}}}
            ]
        }
        values_mod.deep_merge(base, overlay)
        # List index is elided: the scenario entry's children keep the
        # ("scenario",) prefix rather than gaining ("scenario", 0).
        assert ("scenario",) in seen
        assert ("scenario", "decode", "vllm", "additionalFlags") in seen

    def test_default_path_is_empty_tuple(self, monkeypatch):
        import pipeline.lib.values as values_mod

        seen = []
        real = values_mod._merge_lists

        def spy(base_list, overlay_list, *, path=(), sink=None):
            seen.append(path)
            return real(base_list, overlay_list, path=path, sink=sink)

        monkeypatch.setattr(values_mod, "_merge_lists", spy)
        values_mod.deep_merge({"a": [1]}, {"a": [2]})
        assert seen == [("a",)]

    def test_positional_two_arg_calls_still_work(self):
        # The 32 pre-existing tests call these positionally; guard that contract.
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
        assert _merge_lists(["a"], ["b"]) == ["b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/tests/test_values.py -k TestPathThreading -v`
Expected: FAIL — `_merge_lists() got an unexpected keyword argument 'path'`

- [ ] **Step 3: Implement**

Change four signatures and every internal recursion.

`_merge_by_keyfn`:

```python
def _merge_by_keyfn(base_list: list, overlay_list: list, keyfn, *,
                    path: tuple = (), sink: list = None) -> list:
```

Its recursive call becomes `deep_merge(bitem, overlay_by_key[k], path=path, sink=sink)` — `path` unchanged, because the list index is elided.

`_merge_k8s_objects`:

```python
def _merge_k8s_objects(base_list: list, overlay_list: list, *,
                       path: tuple = (), sink: list = None) -> list:
```

with its recursion `deep_merge(bitem, overlay_by_key[k], path=path, sink=sink)`.

`_merge_lists`:

```python
def _merge_lists(base_list: list, overlay_list: list, *,
                 path: tuple = (), sink: list = None) -> list:
```

with `_merge_k8s_objects(base_list, overlay_list, path=path, sink=sink)`,
`_merge_by_keyfn(base_list, overlay_list, lambda d: d[key_field], path=path, sink=sink)`,
and in the Tier 3 loop `deep_merge(base_list[i], overlay_list[i], path=path, sink=sink)`.

`deep_merge`:

```python
def deep_merge(base: dict, overlay: dict, *,
               path: tuple = (), sink: list = None) -> dict:
    """Deep-merge overlay onto base. Dict keys merged recursively.

    Lists of dicts are merged by Kubernetes identity, named key, or positional
    index (see `_merge_lists`). Lists of scalars are replaced entirely, EXCEPT
    at paths registered in `_FLAG_LIST_PATH_SUFFIXES`, which merge by flag name.
    Returns a new dict (deep copy).

    `path` is the dotted key path of `base` within the document being merged, as
    a tuple of segments with list indices elided. It exists so `_merge_lists`
    can scope a merge strategy to a key path; callers merging a whole document
    leave it at its default. `sink`, when a list, collects operator-facing
    warnings about scalar lists that were replaced wholesale.
    """
    result = copy.deepcopy(base)
    for key, oval in overlay.items():
        child = path + (str(key),)
        if key in result and isinstance(result[key], dict) and isinstance(oval, dict):
            result[key] = deep_merge(result[key], oval, path=child, sink=sink)
        elif key in result and isinstance(result[key], list) and isinstance(oval, list):
            result[key] = _merge_lists(result[key], oval, path=child, sink=sink)
        else:
            result[key] = copy.deepcopy(oval)
    return result
```

- [ ] **Step 4: Run the full values suite**

Run: `python -m pytest pipeline/tests/test_values.py -v`
Expected: PASS — all pre-existing tests plus the new ones. Zero pre-existing tests modified.

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/values.py pipeline/tests/test_values.py
git commit -m "refactor(values): thread key path + warning sink through deep_merge (#851)"
```

---

### Task 3: The flag-merge tier

**Files:**
- Modify: `pipeline/lib/values.py` — add `_merge_flag_lists`, wire into `_merge_lists`
- Test: `pipeline/tests/test_values.py`

**Interfaces:**
- Consumes: `_is_flag_list_path`, `_index_flags` (Task 1); `path` parameter (Task 2).
- Produces: `_merge_flag_lists(base_list, overlay_list, path) -> list`.

- [ ] **Step 1: Write the failing tests**

```python
_FP = ("scenario", "decode", "vllm", "additionalFlags")


class TestFlagListMerge:
    def test_base_flag_survives_overlay_that_omits_it(self):
        """The #851 bug: this returned ['--enable-force-include-usage'] before."""
        base = ["--max-num-seqs=256", "--enable-prefix-caching"]
        overlay = ["--enable-force-include-usage"]
        assert _merge_lists(base, overlay, path=_FP) == [
            "--max-num-seqs=256",
            "--enable-prefix-caching",
            "--enable-force-include-usage",
        ]

    def test_overlay_overrides_same_flag_without_duplicating(self):
        base = ["--max-num-seqs=256", "--enable-prefix-caching"]
        overlay = ["--max-num-seqs=512"]
        assert _merge_lists(base, overlay, path=_FP) == [
            "--max-num-seqs=512",
            "--enable-prefix-caching",
        ]

    def test_negation_overrides_positive_and_emits_one_flag(self):
        assert _merge_lists(
            ["--enable-prefix-caching"], ["--no-enable-prefix-caching"], path=_FP
        ) == ["--no-enable-prefix-caching"]

    def test_positive_overrides_negation(self):
        assert _merge_lists(
            ["--no-enable-prefix-caching"], ["--enable-prefix-caching"], path=_FP
        ) == ["--enable-prefix-caching"]

    def test_base_order_preserved_overlay_only_appended(self):
        base = ["--a=1", "--b=2", "--c"]
        overlay = ["--z", "--b=99"]
        assert _merge_lists(base, overlay, path=_FP) == [
            "--a=1",
            "--b=99",
            "--c",
            "--z",
        ]

    def test_prefill_path_merges_too(self):
        p = ("scenario", "prefill", "vllm", "additionalFlags")
        assert _merge_lists(["--a=1"], ["--b"], path=p) == ["--a=1", "--b"]

    def test_scenario_entry_rooted_path_merges(self):
        p = ("decode", "vllm", "additionalFlags")
        assert _merge_lists(["--a=1"], ["--b"], path=p) == ["--a=1", "--b"]

    def test_router_proxy_args_still_replaces(self):
        """The constraint from #851: space-separated arg lists must NOT flag-merge."""
        base = ["--service-node", "envoy-sidecar", "--concurrency", "8"]
        overlay = ["--log-level", "warn"]
        p = ("scenario", "router", "proxy", "args")
        assert _merge_lists(base, overlay, path=p) == ["--log-level", "warn"]

    def test_no_path_means_no_flag_merge(self):
        assert _merge_lists(["--a=1"], ["--b"]) == ["--b"]

    def test_empty_overlay_still_clears(self):
        assert _merge_lists(["--a=1"], [], path=_FP) == []

    def test_empty_base_returns_overlay(self):
        assert _merge_lists([], ["--a=1"], path=_FP) == ["--a=1"]

    def test_single_layer_bad_entry_not_rejected(self):
        """Guards protect the merge, so a lone contributor is never newly refused."""
        assert _merge_lists([], ["not-a-flag"], path=_FP) == ["not-a-flag"]

    def test_bad_entry_refused_when_both_layers_contribute(self):
        with pytest.raises(ValueError, match="does not start with"):
            _merge_lists(["--a=1"], ["oops"], path=_FP)

    def test_does_not_mutate_inputs(self):
        base = ["--a=1"]
        overlay = ["--b"]
        _merge_lists(base, overlay, path=_FP)
        assert base == ["--a=1"] and overlay == ["--b"]

    def test_end_to_end_through_deep_merge(self):
        base = {
            "scenario": [
                {
                    "name": "s",
                    "decode": {
                        "vllm": {
                            "additionalFlags": [
                                "--max-num-seqs=256",
                                "--enable-prefix-caching",
                            ]
                        }
                    },
                }
            ]
        }
        overlay = {
            "scenario": [
                {
                    "name": "s",
                    "decode": {
                        "vllm": {"additionalFlags": ["--enable-force-include-usage"]}
                    },
                }
            ]
        }
        out = deep_merge(base, overlay)
        assert out["scenario"][0]["decode"]["vllm"]["additionalFlags"] == [
            "--max-num-seqs=256",
            "--enable-prefix-caching",
            "--enable-force-include-usage",
        ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_values.py -k TestFlagListMerge -v`
Expected: FAIL — base flags dropped (the bug), e.g. `assert ['--enable-force-include-usage'] == ['--max-num-seqs=256', ...]`

- [ ] **Step 3: Implement**

Add after `_index_flags`:

```python
def _merge_flag_lists(base_list: list, overlay_list: list, path: tuple) -> list:
    """Merge two CLI-flag lists by flag name.

    Base entries are emitted first in base order — an overlay entry sharing a
    key substitutes its own literal spelling, so `--no-X` can override `--X` —
    and overlay-only entries are appended in overlay order. This is the same
    base-first-then-append convention Tier 2b already uses for keyed dict lists.
    """
    base_idx = _index_flags(base_list, path, "base")
    overlay_idx = _index_flags(overlay_list, path, "overlay")
    result = [overlay_idx.get(key, entry) for key, entry in base_idx.items()]
    result.extend(entry for key, entry in overlay_idx.items() if key not in base_idx)
    return result
```

Then in `_merge_lists`, insert immediately after the two empty-list guards and before Tier 1:

```python
    # Tier 0: path-scoped CLI-flag lists — merge by flag name (#851).
    # Deliberately placed AFTER the empty-list guards: with only one layer
    # contributing there is nothing to merge and nothing to lose, so a
    # single-layer bundle is never newly refused by _index_flags' entry guards.
    # The guards protect the merge, not the schema.
    if _is_flag_list_path(path):
        return _merge_flag_lists(base_list, overlay_list, path)
```

Update the `_merge_lists` docstring tier list to lead with:

```
    Tier 0:  path matches _FLAG_LIST_PATH_SUFFIXES → merge scalar entries by
             flag name (`--max-num-seqs=256` keys on `--max-num-seqs`; `--no-X`
             and `--X` are one key). Only reached when BOTH lists are non-empty.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_values.py -v`
Expected: PASS, all tests including the 32 pre-existing.

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/values.py pipeline/tests/test_values.py
git commit -m "fix(values): merge vllm.additionalFlags by flag name, not replace (closes #851)"
```

---

### Task 4: Record conflicts for scalar lists that still replace

**Files:**
- Modify: `pipeline/lib/values.py` — add `_record_scalar_list_replace`, call from Tier 1
- Test: `pipeline/tests/test_values.py`

**Interfaces:**
- Consumes: `sink` parameter (Task 2).
- Produces: `_record_scalar_list_replace(base_list, overlay_list, path, sink) -> None`.

This is issue #851's Stage 1, narrowed: it fires only for scalar lists that STILL replace, since flag-merged paths no longer lose anything.

- [ ] **Step 1: Write the failing tests**

```python
class TestScalarReplaceConflictSink:
    def test_records_conflict_when_both_layers_set_scalar_list(self):
        sink = []
        p = ("scenario", "router", "proxy", "args")
        _merge_lists(["--concurrency", "8"], ["--log-level", "warn"], path=p, sink=sink)
        assert len(sink) == 1
        assert "scenario.router.proxy.args" in sink[0]
        assert "--concurrency" in sink[0]

    def test_no_sink_is_silent(self):
        # capacity.py merges without a sink; must not raise.
        p = ("scenario", "router", "proxy", "args")
        assert _merge_lists(["--a"], ["--b"], path=p) == ["--b"]

    def test_identical_lists_record_nothing(self):
        sink = []
        p = ("scenario", "router", "proxy", "args")
        _merge_lists(["--a"], ["--a"], path=p, sink=sink)
        assert sink == []

    def test_flag_merged_path_records_nothing(self):
        sink = []
        _merge_lists(["--a=1"], ["--b"], path=_FP, sink=sink)
        assert sink == []

    def test_empty_base_records_nothing(self):
        sink = []
        p = ("scenario", "router", "proxy", "args")
        _merge_lists([], ["--b"], path=p, sink=sink)
        assert sink == []

    def test_dict_list_records_nothing(self):
        sink = []
        _merge_lists(
            [{"name": "x"}], [{"name": "x", "v": 1}], path=("containers",), sink=sink
        )
        assert sink == []

    def test_sink_propagates_through_deep_merge(self):
        sink = []
        base = {"scenario": [{"name": "s", "router": {"proxy": {"args": ["--a"]}}}]}
        overlay = {"scenario": [{"name": "s", "router": {"proxy": {"args": ["--b"]}}}]}
        deep_merge(base, overlay, sink=sink)
        assert len(sink) == 1
        assert "scenario.router.proxy.args" in sink[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_values.py -k TestScalarReplaceConflictSink -v`
Expected: FAIL — `assert 0 == 1` (sink never populated)

- [ ] **Step 3: Implement**

Add after `_merge_flag_lists`:

```python
def _record_scalar_list_replace(base_list: list, overlay_list: list,
                                path: tuple, sink: list) -> None:
    """Record that a scalar list was replaced wholesale, discarding base values.

    Stage 1 of issue #851, narrowed to the lists that still replace now that the
    flag-merge tier exists — today `router.proxy.args` and
    `{decode,prefill}.extraContainerConfig.securityContext.capabilities.add`
    (both allowlisted in the bootstrap skill's
    `tests/test_defaults_templates.py:_ALLOWED_SCALAR_LISTS`). Paths that merge
    by flag name never reach here, so this is signal rather than noise.

    Identical lists record nothing: restating the same values loses no
    information, and warning about it would train operators to ignore the
    warning. `sink is None` — every consumer except the assemble resolution
    chain — is silent.
    """
    if sink is None or base_list == overlay_list:
        return
    sink.append(
        f"{'.'.join(path) or '<root>'}: overlay replaces the whole list, "
        f"discarding {len(base_list)} value(s) set by an earlier layer: "
        f"{base_list!r} (kept: {overlay_list!r})"
    )
```

Then in `_merge_lists`' Tier 1 branch:

```python
    # Tier 1: any non-dict item → replace
    if not (all(isinstance(x, dict) for x in base_list)
            and all(isinstance(x, dict) for x in overlay_list)):
        _record_scalar_list_replace(base_list, overlay_list, path, sink)
        return copy.deepcopy(overlay_list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest pipeline/tests/test_values.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/values.py pipeline/tests/test_values.py
git commit -m "feat(values): record scalar-list replacements that discard base values (#851)"
```

---

### Task 5: Surface the conflicts through assemble to the CLI

**Files:**
- Modify: `pipeline/lib/assemble_run.py` — new `_merge_layer` helper; `resolve_baseline` / `resolve_treatment` gain `sink`; `_ResolvedPackages` gains `scalar_list_conflicts`; side-band attr
- Modify: `pipeline/sim2real.py` — print the warnings (near line 2556)
- Test: `pipeline/tests/test_assemble_run.py`

**Interfaces:**
- Consumes: `deep_merge(..., sink=...)` (Task 2), conflict recording (Task 4).
- Produces: `resolve_baseline(*, bundle_path, overlay_path, framework_defaults, sink=None)`, `resolve_treatment(*, baseline_resolved, diffs_path, overlay_path, sink=None)`, `_ResolvedPackages.scalar_list_conflicts: list[str]`, `assemble_run.scalar_list_conflicts` side-band attr.

- [ ] **Step 1: Write the failing tests**

```python
def test_config_md_flag_survives_overlay_that_omits_it(tmp_path):
    """#851 end-to-end: baseline flags reach the resolved scenario even when the
    generated overlay contributes only its own flag."""
    bundle = tmp_path / "baseline.yaml"
    bundle.write_text(yaml.dump({"scenario": [{
        "name": "b",
        "decode": {"vllm": {"additionalFlags": [
            "--max-num-seqs=256", "--enable-prefix-caching"]}},
    }]}))
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(yaml.dump({"scenario": [{
        "name": "b",
        "decode": {"vllm": {"additionalFlags": ["--enable-force-include-usage"]}},
    }]}))
    resolved = resolve_baseline(
        bundle_path=bundle, overlay_path=overlay, framework_defaults={})
    flags = resolved["scenario"][0]["decode"]["vllm"]["additionalFlags"]
    assert flags == [
        "--max-num-seqs=256",
        "--enable-prefix-caching",
        "--enable-force-include-usage",
    ]


def test_resolve_baseline_records_scalar_conflict_with_layer_names(tmp_path):
    bundle = tmp_path / "baseline.yaml"
    bundle.write_text(yaml.dump({"scenario": [{
        "name": "b", "router": {"proxy": {"args": ["--concurrency", "8"]}}}]}))
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(yaml.dump({"scenario": [{
        "name": "b", "router": {"proxy": {"args": ["--log-level", "warn"]}}}]}))
    sink = []
    resolve_baseline(bundle_path=bundle, overlay_path=overlay,
                     framework_defaults={}, sink=sink)
    assert len(sink) == 1
    assert "baseline bundle -> registered overlay" in sink[0]
    assert "scenario.router.proxy.args" in sink[0]


def test_resolve_baseline_sink_optional(tmp_path):
    bundle = tmp_path / "baseline.yaml"
    bundle.write_text(yaml.dump({"scenario": [{"name": "b"}]}))
    resolved = resolve_baseline(bundle_path=bundle, overlay_path=None,
                                framework_defaults={})
    assert resolved["scenario"][0]["name"] == "b"


def test_resolve_treatment_records_conflict_with_layer_names(tmp_path):
    overlay = tmp_path / "algo.yaml"
    overlay.write_text(yaml.dump({"scenario": [{
        "name": "b", "router": {"proxy": {"args": ["--log-level", "warn"]}}}]}))
    baseline = {"scenario": [{
        "name": "b", "router": {"proxy": {"args": ["--concurrency", "8"]}}}]}
    sink = []
    resolve_treatment(baseline_resolved=baseline, diffs_path=None,
                      overlay_path=overlay, sink=sink)
    assert len(sink) == 1
    assert "treatment diffs -> algorithm overlay" in sink[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pipeline/tests/test_assemble_run.py -k "flag_survives or scalar_conflict or sink_optional or layer_names" -v`
Expected: FAIL — the first with base flags dropped, the others with `unexpected keyword argument 'sink'`

- [ ] **Step 3: Implement**

Add near the other module-level helpers in `assemble_run.py`, above `resolve_baseline`:

```python
def _merge_layer(base: dict, overlay: dict, *, layer: str, sink) -> dict:
    """`deep_merge` one layer, tagging any recorded conflict with the layer pair.

    `values.deep_merge` knows the key path but not which files are being merged;
    only this module knows that. Collecting per-merge and re-prefixing is what
    turns "scenario.router.proxy.args was replaced" into an operator-actionable
    "which two layers disagreed".
    """
    local = [] if sink is not None else None
    merged = deep_merge(base, overlay, sink=local)
    if sink is not None:
        sink.extend(f"{layer}: {msg}" for msg in local)
    return merged
```

In `resolve_baseline`, add `sink: list = None` to the keyword-only signature and replace the two merges:

```python
    resolved = _merge_layer(aligned_defaults, bundle,
                           layer="framework defaults -> baseline bundle", sink=sink)
    resolved = _merge_layer(resolved, overlay,
                           layer="baseline bundle -> registered overlay", sink=sink)
```

In `resolve_treatment`, add `sink: list = None` and:

```python
    resolved = _merge_layer(copy.deepcopy(baseline_resolved), diffs,
                           layer="baseline -> treatment diffs", sink=sink)
    resolved = _merge_layer(resolved, overlay,
                           layer="treatment diffs -> algorithm overlay", sink=sink)
```

Document `sink` in both docstrings.

In `_ResolvedPackages`, append the field last (NamedTuple field order matters):

```python
    scalar_list_conflicts: list[str]
```

In `_resolve_packages`, create `scalar_list_conflicts: list[str] = []` before the baseline loop, pass `sink=scalar_list_conflicts` to both `resolve_baseline` (~line 639) and `resolve_treatment` (~line 659), and include it in the `_ResolvedPackages(...)` construction (~line 687).

In `assemble_run`, alongside each `assemble_run.skipped_algorithms = ...` assignment, add:

```python
    assemble_run.scalar_list_conflicts = resolved.scalar_list_conflicts  # type: ignore[attr-defined]
```

Grep for every `assemble_run.skipped_algorithms =` assignment and mirror each — the fresh-assemble and additive-grow paths both need it. Add the initializer next to the others at module bottom:

```python
assemble_run.scalar_list_conflicts = []  # type: ignore[attr-defined]
```

In `pipeline/sim2real.py`, after the `skipped_algorithms` loop (~line 2556):

```python
    for msg in getattr(_assemble_run_lib.assemble_run, "scalar_list_conflicts", []):
        print(
            f"warning: {msg} — this list is replaced rather than merged, so the "
            "earlier layer's values do not reach the cluster. State them in the "
            "later layer, or move them to a typed key. See issue #851.",
            file=sys.stderr,
        )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest pipeline/tests/test_assemble_run.py pipeline/tests/test_values.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/sim2real.py pipeline/tests/test_assemble_run.py
git commit -m "feat(assemble): surface scalar-list replacement conflicts as warnings (#851)"
```

---

### Task 6: Correct the docs that say additionalFlags is replaced

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/templates/defaults/vllm-logging.yaml` (~lines 21-25)
- Modify: `docs/troubleshooting.md` (~line 157)
- Modify: `.claude/skills/sim2real-bootstrap/tests/test_defaults_templates.py` (module docstring ~13, `_ALLOWED_SCALAR_LISTS` preamble ~100, test docstring ~140, assertion message ~169)
- Modify: `pipeline/README.md`, `CLAUDE.md`
- Create: `pipeline/tests/test_docs_flag_merge.py`

Three places actively instruct authors to avoid `additionalFlags` *because it is replaced*. That rationale is now false for that key. The recommendation to prefer a typed key can stand where it has an independent reason (a typed key applies to both roles at once), but it must not rest on a reason that no longer holds.

- [ ] **Step 1: Write the failing test**

Create `pipeline/tests/test_docs_flag_merge.py`:

```python
"""Guard that docs do not describe vllm.additionalFlags as replace-semantics.

Since #851 that list merges by flag name. Three files previously told authors to
avoid it *because* it was replaced; if that rationale creeps back, this fails.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

_FILES = (
    "docs/troubleshooting.md",
    ".claude/skills/sim2real-bootstrap/templates/defaults/vllm-logging.yaml",
)


def test_flag_merged_paths_not_described_as_replaced():
    offenders = []
    for rel in _FILES:
        text = (_REPO / rel).read_text()
        for para in re.split(r"\n\s*\n", text):
            if (
                "additionalFlags" in para
                and re.search(r"\breplace(s|d)?\b", para)
                and "#851" not in para
            ):
                offenders.append(f"{rel}: {para[:120]}")
    assert not offenders, (
        "these passages tie additionalFlags to replace semantics without "
        f"acknowledging #851: {offenders}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pipeline/tests/test_docs_flag_merge.py -v`
Expected: FAIL, listing both files.

- [ ] **Step 3: Update the prose**

`docs/troubleshooting.md:157` — replace the "`additionalFlags` is a list of scalars, so … each later layer *replace* it" clause with: as of #851, `**.vllm.additionalFlags` merges by flag name across layers, so a flag set in one layer survives another that does not restate it. The typed key is still preferred here because it applies to both roles at once, not because the list is replaced. Keep the existing note that `decode.vllm.loggingLevel: INFO` was a no-op.

`vllm-logging.yaml:21-25` — same correction in the header comment. Keep "USE THE TYPED KEY"; change the reason from "additionalFlags is a list of scalars, so `_merge_lists` replaces it" to "the typed key sets both roles at once, whereas additionalFlags is per-role", and note that as of #851 the list itself merges by flag name rather than being replaced.

`test_defaults_templates.py` — in the module docstring, the `_ALLOWED_SCALAR_LISTS` preamble, the test docstring, and the assertion message: state that Tier 1 replace still governs the allowlisted keys (`router.proxy.args`, `capabilities.add`) but NOT `**.vllm.additionalFlags`, which merges by flag name since #851, and that assemble now emits a warning when a still-replacing scalar list is set by two layers. Do not change the test's logic or the allowlist contents — no fragment sets `additionalFlags` today, so behavior is unchanged.

`pipeline/README.md` — in the assembly / values section, document Tier 0, the suffix scoping, `--no-` canonicalization, removal via `--no-X` or an explicit empty list, the refuse-on-non-`--`-entry and refuse-on-duplicate guards, and the new assemble warning for still-replacing scalar lists.

`CLAUDE.md` — update the `values.py` row of the Pipeline Library table from "Deep-merge utility (`deep_merge`) used by `assemble_run.py`" to also note the path-scoped flag-name merge for `**.vllm.additionalFlags`.

- [ ] **Step 4: Run the docs test plus the bootstrap suite**

Run: `python -m pytest pipeline/tests/test_docs_flag_merge.py .claude/skills/sim2real-bootstrap/tests/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/ .claude/skills/sim2real-bootstrap/ pipeline/README.md CLAUDE.md pipeline/tests/test_docs_flag_merge.py
git commit -m "docs: correct additionalFlags replace-semantics guidance after #851"
```

---

### Task 7: Full verification

- [ ] **Step 1: Lint**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: no output

- [ ] **Step 2: Full suite with coverage gate**

```bash
python -m pytest pipeline/ \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: PASS, coverage >= 90%.

- [ ] **Step 3: Confirm no leak into the parent repo**

```bash
git status
git -C ../../.. status --short
```

Expected: changes only in the worktree.

- [ ] **Step 4: Stale-reference sweep**

Grep `**/*.md`, `docs/`, `.claude/skills/`, `README*` for `additionalFlags`, `_merge_lists`, `deep_merge`, `Tier 1`, `_ALLOWED_SCALAR_LISTS`. Classify each hit stale / accurate / unrelated and fix the stale ones.

---

## Self-Review

**Spec coverage.** Stage 2 flag-name merge → Tasks 1+3. Path-scoping constraint (`router.proxy.args` must not flag-merge) → Task 3 `test_router_proxy_args_still_replaces`. Path threading, named in the issue as the main cost → Task 2. Negation decision → Tasks 1+3. No-sentinel removal decision → covered by `--no-` override plus `test_empty_overlay_still_clears`. Repeated-flag decision (refuse loudly) → Task 1 `test_duplicate_key_refused`. Stage 1 warning narrowed to still-replacing lists → Tasks 4+5. Ordering convention → Task 3 `test_base_order_preserved_overlay_only_appended`. Non-`--` guard → Tasks 1+3. Doc corrections → Task 6. Migration → deliberately absent per the decision comment.

**Placeholder scan.** No TBDs; every code step carries real code. The only prose-only step is Task 6 Step 3, where the deliverable *is* prose and the exact required content is stated.

**Type consistency.** `path` is a tuple of `str` everywhere; `sink` is `list` or `None` everywhere. `_merge_flag_lists(base, overlay, path)` takes path positionally (internal, always called with it); the four threaded functions take `path`/`sink` keyword-only with defaults. `_index_flags` returns an ordered `dict` consumed as such by `_merge_flag_lists`. `_ResolvedPackages.scalar_list_conflicts` is `list[str]`, matching the `sink` element type and the CLI's iteration.

**Known risk.** Task 5's `_merge_layer` sets `local = [] if sink is not None else None`, so `deep_merge(sink=None)` stays on the silent path when no sink was requested — do not simplify to an unconditional `[]`, or every consumer starts allocating and `_record_scalar_list_replace`'s `sink is None` fast path stops firing.
