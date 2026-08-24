# parallelism.workers Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the bootstrap generators from writing `parallelism.workers = tensor_parallel_size`; emit `workers: 1` with provenance that states what the key actually means.

**Architecture:** `workers` is the LeaderWorkerSet group size (pods per replica), not a parallelism degree. Both bootstrap generators currently set it from `tensor_parallel_size`. Fix the constant in all four dict-construction sites, give `workers` its own provenance entry instead of borrowing the tensor source, and correct the two prose sites that encode the wrong belief.

**Tech Stack:** Python 3.10+, pytest, PyYAML

**Spec:** GitHub issue #831 (`gh issue view 831`)

## Global Constraints

- `workers: 1` is emitted **explicitly**, never omitted. The issue offers omission as an alternative; it is rejected — see "Rejected alternative" below.
- The two generators stay independent modules. No shared helper is introduced in this PR — see "Rejected alternative" below.
- Behaviour change is confined to `tp > 1`. At `tp == 1` the old code already emitted `workers: 1`; at `tp == 1, dp == 1` the `if tp > 1 or dp > 1` gate emits no `parallelism` block at all. Existing bundles with `tp == 1` must regenerate byte-identically.
- `is_unrecognized_replica_label` keeps its current behaviour. `workers` stays absent from `_COUNT_NOUN_TAIL_RE`; making it a recognized input field belongs to #843.
- No change to the `if tp > 1 or dp > 1` gates. Widening them for `workers > 1` belongs to #843.
- Provenance source strings follow the existing register in this file: short lowercase phrases, e.g. `config.md row "tensor_parallel_size"`, `default (not in config.md)`, `lookup: HARDWARE_LABELS["..."]`.

## Rejected alternatives (record for reviewers)

**Omitting the key.** Verified safe today but rejected. `render_plans.py:284-299` `deep_merge` recurses, so an absent `workers` inherits `1` from `defaults.yaml:845` (decode) / `:991` (prefill), both `*parallelism_single`. However `defaults.yaml:200,209,222,231` — the `large`/`xlarge` resource presets — carry `*parallelism_4gpu`/`*parallelism_8gpu` containing `workers: 4`/`workers: 8`, and `_apply_resource_preset` (`render_plans.py:302-325`) passes the preset as the *override* argument, so a preset wins over the scenario. `resourcePreset` is currently set nowhere (not in sim2real, not in any experiment repo, not in any `config/scenarios/` file), so omission is presently safe — but it makes the correct value depend on an upstream default staying put. Explicit `1` does not.

**Lifting a shared helper across the two generators.** Rejected for this PR. `generate_from_config.py` and `generate_scenarios.py` are independent entry-point scripts with no cross-imports and no shared module in the skill directory. The duplicated surface is a four-key dict literal and four f-string emit lines; extracting a module for a constant is not worth the structural change. #843 adds real logic here (a `pods_per_replica` input field, paired `multinode` emission, widened gates) — that is the right moment to lift a helper.

---

### Task 1: `generate_from_config.py` — the config.md input path

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/generate_from_config.py:775-781` (decode dict), `:822-829` (prefill dict), `:859-868` (provenance), `:917` and `:948` (emit lines), `:354-356` (rationale comment)
- Test: `.claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: provenance keys `decode.parallelism.workers` and `prefill.parallelism.workers`, both holding the string `single-node default (LWS pods per replica, not a parallelism degree)`. Task 2 mirrors this wording as an inline literal.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_generate_from_config_prefill.py`:

```python
# ---------------------------------------------------------------------------
# parallelism.workers (issue #831)
# ---------------------------------------------------------------------------

def test_workers_is_one_not_tensor_parallel_size():
    """`workers` is the LWS pods-per-replica count, not a parallelism degree.

    A single pod holding 4 GPUs at TP=4 is `tensor: 4, workers: 1`. Emitting
    `workers: 4` claims four pods per replica.
    """
    scenario, _ = build([row("Number of pods", "2"), row("tensor_parallel_size", "4")])
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 4
    assert p["workers"] == 1


def test_prefill_workers_is_one():
    scenario, _ = build([
        row("Number of pods", "2"),
        row("Number of prefill pods", "1"),
        row("tensor_parallel_size", "4"),
    ])
    assert scenario["prefill"]["parallelism"]["tensor"] == 4
    assert scenario["prefill"]["parallelism"]["workers"] == 1


def test_workers_provenance_does_not_cite_tensor_parallel_size(tmp_path):
    """The emitted comment must not claim `workers` came from TP."""
    scenario, prov = build([row("Number of pods", "2"), row("tensor_parallel_size", "4")])
    text = emit(scenario, prov, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 1
    assert "tensor_parallel_size" not in workers_lines[0]
    assert "pods per replica" in workers_lines[0]
    assert yaml.safe_load(text)["scenario"][0]["decode"]["parallelism"]["workers"] == 1


def test_dp_only_still_emits_workers_one():
    """dp>1 with tp==1 already produced workers: 1; guard against regression."""
    scenario, _ = build([row("Number of pods", "2"), row("data_parallel_size", "2")])
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 1
    assert p["workers"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py -k workers -v`

Expected: `test_workers_is_one_not_tensor_parallel_size` FAILS with `assert 4 == 1`; `test_prefill_workers_is_one` FAILS the same way; `test_workers_provenance_does_not_cite_tensor_parallel_size` FAILS on `"tensor_parallel_size" not in workers_lines[0]`. `test_dp_only_still_emits_workers_one` PASSES already (it is the regression guard).

- [ ] **Step 3: Fix the two dict-construction sites**

At `:775-781`, change `"workers": tp` to `"workers": 1`:

```python
    if tp > 1 or dp > 1:
        decode["parallelism"] = {
            "data": dp,
            "dataLocal": dp,
            "tensor": tp,
            # LWS group size (pods per replica), NOT a parallelism degree. A
            # single pod holding `tensor` GPUs is workers: 1. Multi-pod model
            # instances need a config.md input that does not exist yet (#843).
            "workers": 1,
        }
```

At `:822-829`, the prefill block, make the identical change (no comment — the decode block above carries the explanation):

```python
        if tp > 1 or dp > 1:
            prefill["parallelism"] = {
                "data": dp,
                "dataLocal": dp,
                "tensor": tp,
                "workers": 1,
            }
```

- [ ] **Step 4: Add the provenance entries**

At `:859-868`, add a `workers` key alongside the existing `tensor` and `data` entries, for both roles:

```python
    if tp > 1 or dp > 1:
        provenance["decode.parallelism.tensor"] = tp_source
        provenance["decode.parallelism.data"] = dp_source
        provenance["decode.parallelism.workers"] = _WORKERS_PROVENANCE

    if "prefill" in scenario:
        provenance["prefill.replicas"] = fields["prefill_replicas"].source
        provenance["prefill.acceleratorType.labelValue"] = prefill_hw_source
        if tp > 1 or dp > 1:
            provenance["prefill.parallelism.tensor"] = tp_source
            provenance["prefill.parallelism.data"] = dp_source
            provenance["prefill.parallelism.workers"] = _WORKERS_PROVENANCE
```

Define the constant at module level, next to the other module constants (after `_RATIO_LABEL_RE` at `:340`):

```python
# `parallelism.workers` is the LeaderWorkerSet group size -- pods per replica --
# not a parallelism degree. Nothing in config.md states it today, so it is a
# stated default rather than a derived value (#831). A config.md input for
# multi-pod model instances is tracked by #843.
_WORKERS_PROVENANCE = "single-node default (LWS pods per replica, not a parallelism degree)"
```

- [ ] **Step 5: Point the emit lines at the new provenance key**

At `:917` (decode) change the provenance lookup:

```python
        lines.append(f"      workers: {p['workers']}  # {provenance['decode.parallelism.workers']}")
```

At `:948` (prefill):

```python
            lines.append(f"      workers: {pp['workers']}  # {provenance['prefill.parallelism.workers']}")
```

- [ ] **Step 6: Correct the rationale comment**

In the `is_unrecognized_replica_label` docstring at `:354-356`, replace the paragraph that states the wrong derivation. Behaviour is unchanged — `workers` stays out of `_COUNT_NOUN_TAIL_RE` — but the reason is now accurate:

```python
    "workers" is deliberately NOT in `_COUNT_NOUN_TAIL_RE`. It *is* a pod count --
    `parallelism.workers` is the LeaderWorkerSet group size -- but this generator
    has no input field for it, so a `workers` row cannot be resolved and the
    "use `number of pods`" advice this function offers would be wrong for it.
    #843 adds the input field; flagging the label belongs with that change.
```

- [ ] **Step 7: Fix the test docstring that repeats the wrong belief**

At `tests/test_generate_from_config_prefill.py:244-248`, the assertion stays; the docstring is corrected:

```python
def test_workers_label_is_left_alone():
    """`workers` is a pod count, but no input field resolves it yet (#843), so
    the "use `number of pods`" advice would be wrong -- left unflagged (#831).
    """
    assert gfc.is_unrecognized_replica_label("Prefill workers") is False
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/ -v`

Expected: all PASS. Pay attention to the two pre-existing assertions that must still hold — `test_prefill_inherits_shared_parallelism_and_flags` at `:143-148` asserts `prefill.parallelism == decode.parallelism` and `tensor == 4`; both roles now carry `workers: 1` so equality is preserved.

- [ ] **Step 9: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/generate_from_config.py .claude/skills/sim2real-bootstrap/tests/test_generate_from_config_prefill.py
git commit -m "fix(bootstrap): emit parallelism.workers=1, not tensor_parallel_size (#831)"
```

---

### Task 2: `generate_scenarios.py` — the JSON input path

**Files:**
- Modify: `.claude/skills/sim2real-bootstrap/generate_scenarios.py:227-233` (decode dict), `:264-270` (prefill dict), `:325` and `:367` (emit lines)
- Modify: `.claude/skills/sim2real-bootstrap/generate_scenarios.README.md:70` (mapping table row)
- Test: `.claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py`

**Interfaces:**
- Consumes: the provenance wording from Task 1, mirrored here as an inline literal (this module has no provenance dict — it writes source comments as f-string literals).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_generate_scenarios.py`:

```python
# ---------------------------------------------------------------------------
# parallelism.workers (issue #831)
# ---------------------------------------------------------------------------

def test_workers_is_one_not_tensor_parallel_size():
    """`workers` is the LWS pods-per-replica count, not a parallelism degree."""
    scenario = gs.build_scenario(entry(vllm_extra={"tensor_parallel_size": 4}), "cand")
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 4
    assert p["workers"] == 1


def test_prefill_workers_is_one():
    src = entry(vllm_extra={"prefill_instances": 1, "tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    assert scenario["prefill"]["parallelism"]["tensor"] == 4
    assert scenario["prefill"]["parallelism"]["workers"] == 1


def test_workers_comment_does_not_cite_tensor_parallel_size(tmp_path):
    src = entry(vllm_extra={"tensor_parallel_size": 4})
    scenario = gs.build_scenario(src, "cand")
    text = emit(scenario, src, tmp_path)
    workers_lines = [ln for ln in text.splitlines() if ln.strip().startswith("workers:")]
    assert len(workers_lines) == 1
    assert "tensor_parallel_size" not in workers_lines[0]
    assert "pods per replica" in workers_lines[0]
    assert yaml.safe_load(text)["scenario"][0]["decode"]["parallelism"]["workers"] == 1


def test_dp_only_still_emits_workers_one():
    scenario = gs.build_scenario(entry(vllm_extra={"data_parallel_size": 2}), "cand")
    p = scenario["decode"]["parallelism"]
    assert p["tensor"] == 1
    assert p["workers"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py -k workers -v`

Expected: the first three FAIL (`assert 4 == 1`, then the comment assertion); `test_dp_only_still_emits_workers_one` PASSES as a regression guard.

- [ ] **Step 3: Fix the two dict-construction sites**

At `:227-233`:

```python
    if tp > 1 or dp > 1:
        decode["parallelism"] = {
            "data": dp,
            "dataLocal": dp,
            "tensor": tp,
            # LWS group size (pods per replica), NOT a parallelism degree. A
            # single pod holding `tensor` GPUs is workers: 1. Multi-pod model
            # instances need an input field that does not exist yet (#843).
            "workers": 1,
        }
```

At `:264-270`, the prefill block:

```python
        if tp > 1 or dp > 1:
            prefill["parallelism"] = {
                "data": dp,
                "dataLocal": dp,
                "tensor": tp,
                "workers": 1,
            }
```

- [ ] **Step 4: Fix the two emit-line source comments**

This module writes source comments as inline literals rather than looking them up in a provenance dict. Define the constant at module level alongside the other module-level constants:

```python
# See #831: `workers` is the LeaderWorkerSet group size (pods per replica), not a
# parallelism degree, and no input field states it -- so it is a stated default.
_WORKERS_COMMENT = "single-node default (LWS pods per replica, not a parallelism degree)"
```

At `:325` (decode):

```python
        lines.append(f"      workers: {p['workers']}  # {_WORKERS_COMMENT}")
```

At `:367` (prefill):

```python
            lines.append(f"      workers: {pp['workers']}  # {_WORKERS_COMMENT}")
```

- [ ] **Step 5: Fix the stale mapping row in the README**

`generate_scenarios.README.md:70` currently reads:

```markdown
| `vllm_args.tensor_parallel_size` | `decode.parallelism.tensor`, `decode.parallelism.workers` |
```

Replace with a row that no longer claims TP feeds `workers`, and add a row recording where `workers` does come from:

```markdown
| `vllm_args.tensor_parallel_size` | `decode.parallelism.tensor` |
| _(no input)_ | `decode.parallelism.workers` — always `1`; LWS pods per replica, not a parallelism degree (#831). Multi-pod instances tracked by #843 |
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest .claude/skills/sim2real-bootstrap/tests/ -v`

Expected: all PASS. `test_prefill_inherits_parallelism_and_flags` at `:97-100` asserts the two roles' parallelism dicts are equal — preserved, both are `workers: 1`.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/sim2real-bootstrap/generate_scenarios.py .claude/skills/sim2real-bootstrap/tests/test_generate_scenarios.py .claude/skills/sim2real-bootstrap/generate_scenarios.README.md
git commit -m "fix(bootstrap): emit parallelism.workers=1 on the JSON input path (#831)"
```

---

### Task 3: Full-suite verification and stale-reference sweep

**Files:** none modified unless the sweep finds a hit.

**Interfaces:**
- Consumes: the completed Task 1 and Task 2 changes.
- Produces: the verification evidence quoted in the PR body.

- [ ] **Step 1: Run the CI lint gate**

Run: `ruff check pipeline/ .claude/skills/ --select F`

Expected: clean. The new module-level constants must be referenced, or F401/F841 will fire.

- [ ] **Step 2: Run the full CI test command**

Run:

```bash
python -m pytest pipeline/ \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: all pass, coverage gate satisfied. Coverage is measured on `pipeline/` only and this change touches neither, so the percentage should be unchanged from `main`.

- [ ] **Step 3: Sweep for stale references**

The changed surface is: the literal value of `parallelism.workers` in generated YAML, and the source-comment text attached to it. No file paths, public symbols, or function signatures changed, so a path-grep is not the right instrument — grep for the *claim*:

```bash
grep -rn "workers" --include="*.md" . | grep -v "^./llm-d-benchmark" | grep -v "^./docs/superpowers/plans"
grep -rn "parallelism" --include="*.md" . | grep -v "^./llm-d-benchmark"
```

Expected hits and their disposition:
- `.claude/skills/sim2real-bootstrap/generate_scenarios.README.md` — updated in Task 2.
- `docs/superpowers/plans/2026-08-24-issue-831-parallelism-workers.md` — this plan; leave.
- Anything under `llm-d-benchmark/` — vendored submodule, out of scope.

Record any hit not in that list and decide stale / accurate / unrelated.

- [ ] **Step 4: Confirm no leak into the parent repo**

Run:

```bash
git status --porcelain
git -C ../../.. status --porcelain
```

Expected: the worktree lists only the intended files. The parent repo must show no new modifications beyond the pre-existing untracked entries recorded at session start (`.gitmodules`, `inference-sim`, `docs/blog/`, `docs/proposals/`, `graphify-out/`, `scratch/`).

- [ ] **Step 5: Verify the acceptance criterion end-to-end**

The issue's reproduction is: author a `config.md` with `| tensor_parallel_size | 4 |`, run the generator, inspect `baselines/baseline.yaml`. Reproduce it directly:

```bash
python - <<'PY'
import sys; sys.path.insert(0, ".claude/skills/sim2real-bootstrap")
import generate_from_config as gfc
rows = [
    {"Parameter": "Model", "Value": "Qwen/Qwen3-14B", "Notes": ""},
    {"Parameter": "GPU", "Value": "H100_SXM_80GB", "Notes": ""},
    {"Parameter": "Number of pods", "Value": "2", "Notes": ""},
    {"Parameter": "tensor_parallel_size", "Value": "4", "Notes": ""},
]
t = gfc.TableSection(heading="vLLM Pod Configuration", rows=rows, line_number=0)
sc, pv = gfc.build_scenario(gfc.extract_fields(t), "repro")
gfc.write_provenance_yaml(sc, pv, "/tmp/repro-831.yaml")
PY
grep -A5 "parallelism:" /tmp/repro-831.yaml
```

Expected: `tensor: 4` with `workers: 1`, and the `workers` comment naming pods-per-replica rather than `tensor_parallel_size`.

- [ ] **Step 6: Commit any sweep fixes**

Only if Step 3 found a stale reference. Otherwise skip.

---

## Self-Review

**1. Spec coverage.** Issue #831 asks for: (a) `workers: 1` instead of `tp` — Task 1 Step 3, Task 2 Step 3; (b) the comment at `:354` corrected — Task 1 Step 6. The vet added: (c) the JSON-input path, which the issue omits — Task 2; (d) the provenance plumbing, since `workers` borrowing `tp_source` is the same false claim in machine-readable form — Task 1 Steps 4-5, Task 2 Step 4; (e) the stale README mapping row — Task 2 Step 5; (f) the test docstring repeating the belief — Task 1 Step 7. Explicitly deferred to #843 and recorded in Global Constraints: the `_COUNT_NOUN_TAIL_RE` membership, the `if tp > 1 or dp > 1` gates, `multinode` emission, and any `pods_per_replica` input field.

**2. Placeholder scan.** No TBDs. Every code step carries the literal text to write; every run step carries the command and its expected result.

**3. Type consistency.** `_WORKERS_PROVENANCE` (Task 1, used via the `provenance` dict) and `_WORKERS_COMMENT` (Task 2, used inline) hold the identical string `single-node default (LWS pods per replica, not a parallelism degree)`. They are deliberately separate module-level constants in two independent scripts, per the rejected-alternative note. Both satisfy the `"pods per replica" in workers_lines[0]` assertion the tests in each task make.
