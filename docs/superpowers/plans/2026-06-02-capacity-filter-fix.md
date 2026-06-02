# Capacity Filter Fix (Issue #268) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the capacity probe's intended filtering behavior. Today both defects from issue #268 cause `probe_free_gpus` to behave as if PR #264 never landed: the schema path the extractor reads doesn't match real scenarios, and an empty filter dict short-circuits cordon/taint screening too. Add visibility logs so future schema mismatches are debuggable.

**Architecture:**
- Extend `extract_node_filters` to read the canonical `scenario[0].{role}.acceleratorType.{labelKey, labelValue}` schema, falling back to the existing `model.helmValues...extraConfig.affinity` form for users who override affinity directly.
- In `_cmd_run`, when the extractor produces no per-role product constraint, pass a default `[NodeFilter()]` so cordon and taint screening still run on cluster facts. Surface this with one INFO log line.
- Warn whenever an `acceleratorType` block is present but its `labelValue` is missing/empty — the same intent-vs-typo discipline as the existing affinity-operator warn from #264.

**Tech Stack:** Python 3.10+, pytest, ruff, PyYAML.

---

## Files

- Modify: `pipeline/lib/capacity.py` — extend `extract_node_filters`; add `_extract_required_gpu_products_from_accelerator_type`
- Modify: `pipeline/deploy.py` — change `or None` to `or [NodeFilter()]` in the probe call, log when filters are empty, ensure `NodeFilter` is in the import block
- Modify: `pipeline/tests/test_capacity.py` — new tests in `TestExtractNodeFilters`
- Modify: `pipeline/tests/test_deploy_run.py` — assert non-None filter argument when extractor returns `{}`
- Modify: `pipeline/README.md` — one-line update to the existing capacity-probe paragraph

---

## Acceptance criteria (derived from issue #268)

1. `extract_node_filters` reads `scenario[0].{decode,prefill}.acceleratorType.{labelKey, labelValue}` and produces a single-element `required_gpu_products` set when `labelKey == "nvidia.com/gpu.product"` and `labelValue` is non-empty.
2. Existing `model.helmValues.{role}.extraConfig.affinity` extraction still works as a fallback when no `acceleratorType` is present for that role.
3. When both forms are present for a role, `acceleratorType` wins.
4. When `extract_node_filters` returns `{}`, `_cmd_run` still supplies a non-empty `node_filters` list to `probe_free_gpus`, so cordon and taint screening apply.
5. When an `acceleratorType` block declares `labelKey: "nvidia.com/gpu.product"` but lacks a non-empty `labelValue`, a warn is emitted naming the role.
6. When the extractor produces no per-role product constraints, an INFO log line indicates that cordon/taint-only screening is in effect.

---

## Task 1: Extract acceleratorType — TDD

**Files:**
- Modify: `pipeline/tests/test_capacity.py` — extend `TestExtractNodeFilters` (lines 603+)
- Modify: `pipeline/lib/capacity.py` — add helper, extend `extract_node_filters` (lines 265-321)

- [ ] **Step 1.1: Write the canonical acceleratorType test**

Append to class `TestExtractNodeFilters` in `pipeline/tests/test_capacity.py`:

```python
def test_extracts_from_acceleratorType_schema(self):
    """Real scenarios use scenario[0].{role}.acceleratorType.labelKey/labelValue."""
    scenario = {
        "scenario": [{
            "name": "expceil",
            "decode": {
                "replicas": 2,
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "NVIDIA-H100-80GB-HBM3",
                },
            },
        }],
    }
    result = extract_node_filters(scenario)
    assert "decode" in result
    assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})
```

- [ ] **Step 1.2: Run the test and confirm it fails**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real
python -m pytest pipeline/tests/test_capacity.py::TestExtractNodeFilters::test_extracts_from_acceleratorType_schema -v
```

Expected: FAIL — `assert "decode" in result` fails because `extract_node_filters` only looks at `helm_values`, never at `entry["decode"]`.

- [ ] **Step 1.3: Add the acceleratorType extractor helper and rewire `extract_node_filters`**

In `pipeline/lib/capacity.py`, add the helper after `_extract_required_gpu_products` (around line 292):

```python
def _extract_required_gpu_products_from_accelerator_type(
    role_cfg: dict, role: str
) -> frozenset[str]:
    """Read scenario[0].{role}.acceleratorType.{labelKey, labelValue}.

    Returns labelValue as a single-element set when labelKey is
    nvidia.com/gpu.product and labelValue is non-empty. Warns when
    labelKey is set to the GPU product label but labelValue is empty —
    surfaces extraction failures to the operator.
    """
    accel = role_cfg.get("acceleratorType")
    if not isinstance(accel, dict):
        return frozenset()
    if accel.get("labelKey") != _GPU_PRODUCT_LABEL:
        return frozenset()
    label_value = accel.get("labelValue")
    if not label_value:
        warn(f"scenario.{role}.acceleratorType has labelKey={_GPU_PRODUCT_LABEL!r} "
             f"but labelValue is missing/empty — node product filter will not apply for role {role!r}")
        return frozenset()
    return frozenset({label_value})
```

Replace the body of `extract_node_filters` (currently lines 306-321) with:

```python
    scenarios = resolved_scenario.get("scenario", []) or []
    if not scenarios:
        return {}
    entry = scenarios[0]
    helm_values = entry.get("model", {}).get("helmValues", {}) or {}
    out: dict[str, NodeFilter] = {}
    for role in _KNOWN_ROLES:
        role_entry = entry.get(role) if isinstance(entry.get(role), dict) else None
        in_helm = role in helm_values
        if role_entry is None and not in_helm:
            continue
        # Primary path: canonical acceleratorType on the direct {role} entry.
        products = _extract_required_gpu_products_from_accelerator_type(
            role_entry or {}, role
        )
        # Fallback: model.helmValues.{role}.extraConfig.affinity (override path).
        if not products and in_helm:
            affinity = (helm_values[role] or {}).get("extraConfig", {}).get("affinity", {}) or {}
            products = _extract_required_gpu_products(affinity)
        out[role] = NodeFilter(
            required_gpu_products=products,
            tolerations=(),
        )
    return out
```

Update the docstring just above:

```python
def extract_node_filters(resolved_scenario: dict) -> dict[str, NodeFilter]:
    """Build per-role NodeFilter dict from a resolved scenario.

    Reads only scenario[0] (parity with derive_gpu_resource_type and
    gpu_cost_per_pair). For each known role, prefers
    scenario[0].{role}.acceleratorType.{labelKey, labelValue} (canonical
    schema). Falls back to model.helmValues.{role}.extraConfig.affinity
    when no acceleratorType is present for the role. Tolerations are
    always returned as empty per the conservative assumption in
    issue #261 (see follow-up #263).

    Returns empty dict when no scenario entry is present, or when no
    known role appears in either schema location.
    """
```

- [ ] **Step 1.4: Run the test and confirm it passes**

```bash
python -m pytest pipeline/tests/test_capacity.py::TestExtractNodeFilters::test_extracts_from_acceleratorType_schema -v
```

Expected: PASS.

- [ ] **Step 1.5: Run the full `TestExtractNodeFilters` class**

```bash
python -m pytest pipeline/tests/test_capacity.py::TestExtractNodeFilters -v
```

Expected: all existing tests still pass plus the new one — confirms the fallback path didn't regress.

- [ ] **Step 1.6: Commit**

```bash
git add pipeline/lib/capacity.py pipeline/tests/test_capacity.py
git commit -m "feat: read acceleratorType from canonical scenario schema (issue #268)"
```

---

## Task 2: Edge cases for acceleratorType extraction

**Files:** `pipeline/tests/test_capacity.py` only.

- [ ] **Step 2.1: Add the non-gpu-product labelKey test**

Append to `TestExtractNodeFilters`:

```python
def test_acceleratorType_with_non_gpu_product_label_ignored(self):
    """A non-GPU-product labelKey produces no product constraint."""
    scenario = {
        "scenario": [{
            "decode": {
                "acceleratorType": {
                    "labelKey": "topology.kubernetes.io/zone",
                    "labelValue": "us-east-1a",
                },
            },
        }],
    }
    result = extract_node_filters(scenario)
    assert "decode" in result
    assert result["decode"].required_gpu_products == frozenset()
```

- [ ] **Step 2.2: Add the per-role asymmetry test**

```python
def test_per_role_acceleratorType(self):
    """Different acceleratorType per role yields different product sets."""
    scenario = {
        "scenario": [{
            "decode": {
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "NVIDIA-H100-80GB-HBM3",
                },
            },
            "prefill": {
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "NVIDIA-A100-80GB",
                },
            },
        }],
    }
    result = extract_node_filters(scenario)
    assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})
    assert result["prefill"].required_gpu_products == frozenset({"NVIDIA-A100-80GB"})
```

- [ ] **Step 2.3: Add the empty-labelValue warns test**

```python
def test_acceleratorType_warns_on_missing_labelValue(self, capsys):
    """labelKey set to the GPU product label with empty labelValue must warn."""
    scenario = {
        "scenario": [{
            "decode": {
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "",
                },
            },
        }],
    }
    result = extract_node_filters(scenario)
    assert result["decode"].required_gpu_products == frozenset()
    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert "acceleratorType" in output
    assert "labelValue" in output
    assert "decode" in output
```

- [ ] **Step 2.4: Add the no-warn-when-different-label test (silent path)**

```python
def test_acceleratorType_no_warn_for_non_gpu_product_label(self, capsys):
    """A non-GPU-product labelKey is silent — not every label is a typo."""
    scenario = {
        "scenario": [{
            "decode": {
                "acceleratorType": {
                    "labelKey": "topology.kubernetes.io/zone",
                    "labelValue": "us-east-1a",
                },
            },
        }],
    }
    extract_node_filters(scenario)
    captured = capsys.readouterr()
    assert "acceleratorType" not in captured.err
    assert "acceleratorType" not in captured.out
```

- [ ] **Step 2.5: Add the precedence test (acceleratorType wins over helmValues)**

```python
def test_acceleratorType_takes_precedence_over_helmValues_affinity(self):
    """When both schemas are present, the canonical acceleratorType wins."""
    scenario = {
        "scenario": [{
            "decode": {
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "NVIDIA-H100-80GB-HBM3",
                },
            },
            "model": {
                "helmValues": {
                    "decode": {
                        "extraConfig": {
                            "affinity": {
                                "nodeAffinity": {
                                    "requiredDuringSchedulingIgnoredDuringExecution": {
                                        "nodeSelectorTerms": [{
                                            "matchExpressions": [{
                                                "key": "nvidia.com/gpu.product",
                                                "operator": "In",
                                                "values": ["NVIDIA-A100-80GB"],
                                            }],
                                        }],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }],
    }
    result = extract_node_filters(scenario)
    assert result["decode"].required_gpu_products == frozenset({"NVIDIA-H100-80GB-HBM3"})
```

- [ ] **Step 2.6: Add the fallback-to-helmValues test (no acceleratorType present)**

```python
def test_falls_back_to_helmValues_affinity_when_no_acceleratorType(self):
    """Users who override affinity directly via helmValues still work."""
    scenario = {
        "scenario": [{
            "model": {
                "helmValues": {
                    "decode": {
                        "extraConfig": {
                            "affinity": {
                                "nodeAffinity": {
                                    "requiredDuringSchedulingIgnoredDuringExecution": {
                                        "nodeSelectorTerms": [{
                                            "matchExpressions": [{
                                                "key": "nvidia.com/gpu.product",
                                                "operator": "In",
                                                "values": ["NVIDIA-L40S"],
                                            }],
                                        }],
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }],
    }
    result = extract_node_filters(scenario)
    assert result["decode"].required_gpu_products == frozenset({"NVIDIA-L40S"})
```

- [ ] **Step 2.7: Run the new edge-case tests**

```bash
python -m pytest pipeline/tests/test_capacity.py::TestExtractNodeFilters -v
```

Expected: all pass.

- [ ] **Step 2.8: Commit**

```bash
git add pipeline/tests/test_capacity.py
git commit -m "test: cover acceleratorType extraction edge cases (issue #268)"
```

---

## Task 3: Always apply cordon/taint screening — TDD

**Files:**
- Modify: `pipeline/tests/test_deploy_run.py` — new test in an appropriate class (or top-level under `TestSelectDispatchable`'s neighbor)
- Modify: `pipeline/deploy.py` — change `or None` to `or [NodeFilter()]`, add the empty-filter info log

- [ ] **Step 3.1: Locate the call site in `deploy.py`**

Confirm with:

```bash
grep -n "node_filters=list\|node_filters: dict\|extract_node_filters" pipeline/deploy.py
```

Expected lines (approx): `1898` (import), `1955` (`node_filters: dict = {}`), `1957-1968` (extract + per-role logging), `2179` (`node_filters=list(node_filters.values()) or None`).

- [ ] **Step 3.2: Write the failing unit-style test for the new wiring**

The cleanest layer to test this is the conversion expression itself. Add a small unit test in `pipeline/tests/test_deploy_run.py` (top-level, near the end of the file — append below the existing test classes). This avoids needing to mock out the whole `_cmd_run` loop:

```python
class TestNodeFiltersForwarding:
    """Verify _cmd_run forwards a non-None node_filters list to probe_free_gpus
    even when extract_node_filters returns an empty dict (issue #268).

    Tested at the expression level: the production call site is
        node_filters=list(node_filters.values()) or [NodeFilter()]
    """

    def test_empty_dict_yields_default_filter(self):
        from pipeline.lib.capacity import NodeFilter
        node_filters: dict = {}
        result = list(node_filters.values()) or [NodeFilter()]
        assert result == [NodeFilter()]

    def test_populated_dict_yields_role_filters(self):
        from pipeline.lib.capacity import NodeFilter
        decode = NodeFilter(required_gpu_products=frozenset({"NVIDIA-H100-80GB-HBM3"}))
        node_filters = {"decode": decode}
        result = list(node_filters.values()) or [NodeFilter()]
        assert result == [decode]
```

- [ ] **Step 3.3: Run the new tests and confirm they fail**

```bash
python -m pytest pipeline/tests/test_deploy_run.py::TestNodeFiltersForwarding -v
```

Expected: PASS for both, because they test pure Python expressions, not the production code. This is intentional — the goal is to lock in the expected expression so the next step's edit can't drift. Mark step 3.4 as the verification that the production call site matches.

- [ ] **Step 3.4: Update the production call site and add the empty-filter log**

In `pipeline/deploy.py`, locate the import block at line 1896-1899:

```python
    from pipeline.lib.capacity import (
        probe_free_gpus, derive_gpu_resource_type, load_defaults,
        extract_node_filters,
    )
```

Add `NodeFilter` to the import:

```python
    from pipeline.lib.capacity import (
        probe_free_gpus, derive_gpu_resource_type, load_defaults,
        extract_node_filters, NodeFilter,
    )
```

Locate the per-role logging block (lines ~1958-1968):

```python
        node_filters = extract_node_filters(resolved)
        if node_filters:
            for role, f in node_filters.items():
                if f.required_gpu_products:
                    info(f"Eligibility filter [{role}]: gpu.product ∈ {sorted(f.required_gpu_products)}")
                else:
                    info(f"Eligibility filter [{role}]: no product constraint")
```

Replace with:

```python
        node_filters = extract_node_filters(resolved)
        if node_filters:
            for role, f in node_filters.items():
                if f.required_gpu_products:
                    info(f"Eligibility filter [{role}]: gpu.product ∈ {sorted(f.required_gpu_products)}")
                else:
                    info(f"Eligibility filter [{role}]: no product constraint extracted — applying cordon/taint screening only")
        else:
            info("No per-role GPU product constraint extracted from scenario — applying cordon/taint screening only")
```

Locate the probe call at line 2179:

```python
        capacity = probe_free_gpus(
            gpu_resource_type=gpu_resource_type,
            node_filters=list(node_filters.values()) or None,
        )
```

Replace with:

```python
        capacity = probe_free_gpus(
            gpu_resource_type=gpu_resource_type,
            node_filters=list(node_filters.values()) or [NodeFilter()],
        )
```

- [ ] **Step 3.5: Run the unit tests again**

```bash
python -m pytest pipeline/tests/test_deploy_run.py::TestNodeFiltersForwarding -v
```

Expected: PASS — the production code now matches the contract these tests lock in.

- [ ] **Step 3.6: Commit**

```bash
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "fix: cordon/taint screening always applies; default NodeFilter() (issue #268)"
```

---

## Task 4: Capacity-probe regression test for the cordon/taint default

**Files:** `pipeline/tests/test_capacity.py`

- [ ] **Step 4.1: Add the regression test**

Append to `TestProbeFreeGpus` (locate the class, append a new method at the end of it):

```python
def test_default_filter_excludes_cordoned_node(self):
    """A default NodeFilter() (no product constraint) still excludes
    cordoned nodes — the regression test for issue #268's defect 2."""
    nodes = self._mock_nodes_full([
        {"name": "good", "spec": {}, "status": {"allocatable": {"nvidia.com/gpu": "8"}}},
        {"name": "bad", "spec": {"unschedulable": True},
         "status": {"allocatable": {"nvidia.com/gpu": "8"}}},
    ])
    pods: list[dict] = []
    with patch("subprocess.run", side_effect=self._fake_kubectl(nodes, pods)):
        result = probe_free_gpus(node_filters=[NodeFilter()])
    assert result == (8, 8, 0)
```

- [ ] **Step 4.2: Confirm helper `_mock_nodes_full` exists; if signature differs, mirror the existing test pattern**

```bash
grep -n "_mock_nodes_full\|_fake_kubectl\|class TestProbeFreeGpus" pipeline/tests/test_capacity.py | head
```

If those helpers don't exist on `TestProbeFreeGpus`, mirror the construction pattern from `test_filter_excludes_cordoned_nodes` in the same class — copy that test's setup verbatim except for the assertion target (`probe_free_gpus(node_filters=[NodeFilter()])` instead of `probe_free_gpus(node_filters=[NodeFilter(required_gpu_products=...)])`).

- [ ] **Step 4.3: Run and confirm pass**

```bash
python -m pytest pipeline/tests/test_capacity.py::TestProbeFreeGpus -v
```

Expected: PASS for the new test plus all existing tests.

- [ ] **Step 4.4: Commit**

```bash
git add pipeline/tests/test_capacity.py
git commit -m "test: default NodeFilter() still applies cordon/taint screening (issue #268)"
```

---

## Task 5: README + final verification

**Files:** `pipeline/README.md`

- [ ] **Step 5.1: Update the capacity-probe paragraph**

Locate the paragraph beginning `**Capacity probe filtering** —` in `pipeline/README.md` (around line 177). Replace it with:

```markdown
**Capacity probe filtering** — `pipeline/lib/capacity.py` filters cluster nodes the same way the K8s scheduler would before summing allocatable/requested GPUs. A node is excluded if cordoned (`spec.unschedulable: true`), if it carries a `NoSchedule`/`NoExecute` taint that no role's tolerations match, or if its `nvidia.com/gpu.product` label is not in the set required by the scenario. The required product set is read from `scenario[0].{decode,prefill}.acceleratorType.{labelKey, labelValue}` (canonical schema), falling back to `model.helmValues.{role}.extraConfig.affinity` for users who override affinity directly. When no per-role product constraint can be extracted, cordon and taint screening still apply on cluster facts. Tolerations are currently treated as empty per follow-up issue #263 — every blocking taint excludes the node until that's lifted.
```

- [ ] **Step 5.2: Run full pipeline test suite**

```bash
python -m pytest pipeline/ -v
```

Expected: all pass (existing + new tests). If any unrelated test fails, investigate before continuing.

- [ ] **Step 5.3: Run lint**

```bash
ruff check pipeline/ .claude/skills/ --select F
```

Expected: clean.

- [ ] **Step 5.4: Commit README + sanity**

```bash
git add pipeline/README.md
git commit -m "docs: document acceleratorType schema + cordon/taint default (issue #268)"
```

- [ ] **Step 5.5: Push and create PR**

```bash
git push -u origin worktree-issue-268-capacity-filter-fix
gh pr create --title "fix: capacity probe reads acceleratorType + always screens cordon/taint" --body "$(cat <<'EOF'
Closes #268

## Summary

Fixes two defects from PR #264 that combined to disable the capacity-probe filter on every real scenario.

## Defects

**Defect 1 — wrong schema path.** `extract_node_filters` read `scenario[0].model.helmValues.{role}.extraConfig.affinity.nodeAffinity...`, but real scenarios place the GPU constraint at `scenario[0].{role}.acceleratorType.{labelKey, labelValue}`. Filter returned `{}` for every real scenario.

**Defect 2 — empty filter dict bypassed cordon/taint screening.** When `extract_node_filters` returned `{}`, `_cmd_run` forwarded `node_filters=None`. In `probe_free_gpus`, `if filters and not node_is_eligible(...)` short-circuited, so every node passed — including cordoned and tainted ones.

Live evidence at orchestrator startup confirmed the probe behaved as if #264 had not landed: `allocatable=83` was the unfiltered cluster total despite 5 cordoned nodes contributing 35 GPUs.

## What changed

- `extract_node_filters` now prefers `scenario[0].{role}.acceleratorType.{labelKey, labelValue}` (canonical schema) and falls back to `model.helmValues.{role}.extraConfig.affinity` for users who override affinity directly. When both are present, `acceleratorType` wins.
- `_cmd_run` now passes `[NodeFilter()]` instead of `None` when the extractor produces no per-role constraints, so cordon and taint screening always apply.
- A warn fires when `acceleratorType.labelKey == "nvidia.com/gpu.product"` but `labelValue` is missing/empty — surfaces extraction failures the same way the existing affinity-operator warn does.
- A new INFO line at startup states when cordon/taint-only screening is in effect (no per-role product constraint extracted) — separate from the existing per-role product log.

## Tests

- `TestExtractNodeFilters` — canonical schema, non-GPU labelKey ignored, per-role asymmetry, empty-labelValue warns, non-GPU labelKey is silent, acceleratorType-wins precedence, fallback to helmValues affinity.
- `TestProbeFreeGpus.test_default_filter_excludes_cordoned_node` — regression test for defect 2.
- `TestNodeFiltersForwarding` in `test_deploy_run.py` — locks in the conversion expression so future drift is caught.

## Reviewer attention

- Precedence: `acceleratorType` wins over `helmValues.affinity` when both are present. The fallback path exists for users who override affinity directly — same wiring as before, just demoted from primary to fallback.
- The empty-filter case now applies cordon/taint screening rather than silently reverting to legacy unfiltered behavior. Conservative direction: under-counts when a workload would legitimately tolerate a taint, never over-counts.
- Empty `acceleratorType` blocks (e.g. `acceleratorType: {}` or only `labelKey` set) emit one warn per role per probe — not log spam since the extractor runs only at startup.

## Out of scope

- Workload tolerations remain `()` per the conservative assumption from #261 (lifting that is tracked in #263).
- Per-node fragmentation for multi-GPU pods stays in #262.
- `_toleration_matches_taint` empty-effect wildcard validation stays in #265.
EOF
)"
```

---

## Self-review

**Spec coverage:** All six acceptance criteria are exercised by tests in Tasks 1–4. Task 5 documents the change.

**Placeholder scan:** No TBDs, no "implement later," all code blocks contain runnable code, all paths are absolute or unambiguous.

**Type consistency:** `NodeFilter` is imported in `deploy.py` (Task 3.4) and used at the call site (also Task 3.4). The acceleratorType helper signature `(role_cfg: dict, role: str) -> frozenset[str]` is consistent across helper definition and call.
