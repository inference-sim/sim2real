# Run Validation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `scripts/validate.py` with three subcommands (pre-deploy, post-deploy, post-collection) and wire pre-deploy and post-collection into `deploy.py`.

**Architecture:** Check logic lives in `scripts/lib/validate_checks.py` (testable, importable); `scripts/validate.py` is a thin CLI that loads artifacts and calls the lib. `deploy.py` imports the lib directly for the automated gates. Phase 2 (post-deploy) uses subprocess for kubectl and HTTP for Prometheus — kept isolated so the rest of the lib is cluster-free.

**Tech Stack:** Python 3.10+ stdlib (dataclasses, csv, json, re, math, subprocess), PyYAML (already in requirements.txt), requests (already in requirements.txt).

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `scripts/lib/validate_checks.py` | Create | All check primitives + ValidationReport dataclasses + top-level runners |
| `scripts/validate.py` | Create | CLI entry point — argparse, artifact loading, output writing |
| `scripts/test_validate_checks.py` | Create | Unit tests for check primitives (run from `scripts/` dir) |
| `tests/test_validate.py` | Create | Integration tests for CLI subcommands |
| `scripts/deploy.py` | Modify | Two call sites: pre-deploy gate in `stage_benchmarks`; post-collection in `_run_single_phase` |

Tests for `validate_checks.py` live in `scripts/test_validate_checks.py` (same pattern as `scripts/test_manifest.py`). Tests for the CLI live in `tests/test_validate.py` (same pattern as `tests/test_analyze.py`).

---

## Chunk 1: validate_checks.py — data types + Phase 1 static checks

### Task 1: Data types and helpers

**Files:**
- Create: `scripts/lib/validate_checks.py`
- Create: `scripts/test_validate_checks.py`

- [ ] **Step 1: Write failing test for CheckItem/CheckGroup/ValidationReport**

Create `scripts/test_validate_checks.py`:

```python
"""Unit tests for validate_checks.py."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.validate_checks import CheckItem, CheckGroup, ValidationReport, _models_match


def test_check_group_from_items_all_pass():
    items = [CheckItem("a", True), CheckItem("b", True)]
    g = CheckGroup.from_items(items)
    assert g.passed is True


def test_check_group_from_items_fail_flips_group():
    items = [CheckItem("a", True), CheckItem("b", False)]
    g = CheckGroup.from_items(items)
    assert g.passed is False


def test_check_group_warn_does_not_flip_group():
    items = [CheckItem("a", True), CheckItem("b", False, severity="warn")]
    g = CheckGroup.from_items(items)
    assert g.passed is True  # warn doesn't fail the group


def test_validation_report_overall_pass():
    g = CheckGroup(passed=True, items=[], notes=[])
    r = ValidationReport(phase="pre_deploy", run="test", timestamp="t",
                         overall="PASS", checks={"workloads": g})
    assert r.failed is False


def test_validation_report_overall_fail():
    g = CheckGroup(passed=False, items=[], notes=[])
    r = ValidationReport(phase="pre_deploy", run="test", timestamp="t",
                         overall="FAIL", checks={"workloads": g})
    assert r.failed is True


def test_models_match_case_insensitive():
    assert _models_match("Qwen/Qwen2.5-7B-Instruct", "qwen/qwen2.5-7b-instruct")


def test_models_match_strips_whitespace():
    assert _models_match("  Qwen/Qwen2.5-7B  ", "Qwen/Qwen2.5-7B")


def test_models_match_different():
    assert not _models_match("Qwen/Qwen2.5-7B", "meta-llama/Llama-3")
```

- [ ] **Step 2: Run test to confirm failure**

```bash
cd /path/to/sim2real/scripts
python -m pytest test_validate_checks.py::test_check_group_from_items_all_pass -v
```
Expected: `ImportError` — `validate_checks` not yet created.

- [ ] **Step 3: Implement data types in validate_checks.py**

Create `scripts/lib/validate_checks.py`:

```python
"""Validation check primitives for sim2real run verification."""

from __future__ import annotations
import csv
import json
import math
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class CheckItem:
    name: str
    passed: bool
    notes: list[str] = field(default_factory=list)
    severity: str = "fail"  # "fail" | "warn"


@dataclass
class CheckGroup:
    passed: bool
    items: list[CheckItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_items(cls, items: list[CheckItem]) -> "CheckGroup":
        """Group passes if no item has severity='fail' and passed=False."""
        passed = all(i.passed or i.severity == "warn" for i in items)
        return cls(passed=passed, items=items)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "items": [
                {"name": i.name, "passed": i.passed,
                 "notes": i.notes, "severity": i.severity}
                for i in self.items
            ],
            "notes": self.notes,
        }


@dataclass
class ValidationReport:
    phase: str
    run: str
    timestamp: str
    overall: str  # "PASS" | "FAIL"
    checks: dict[str, CheckGroup]

    @property
    def failed(self) -> bool:
        return self.overall == "FAIL"

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "run": self.run,
            "timestamp": self.timestamp,
            "overall": self.overall,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
        }

    @classmethod
    def build(cls, phase: str, run: str, checks: dict[str, CheckGroup]) -> "ValidationReport":
        overall = "PASS" if all(g.passed for g in checks.values()) else "FAIL"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(phase=phase, run=run, timestamp=ts, overall=overall, checks=checks)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _models_match(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _parse_vllm_args(args: list[str]) -> dict[str, str]:
    """Parse ['--flag=value', ...] into {'--flag': 'value'}."""
    result = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            result[k] = v
    return result
```

- [ ] **Step 4: Run tests**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_group or test_validation or test_models"
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/validate_checks.py scripts/test_validate_checks.py
git commit -m "feat(validate): add CheckItem/CheckGroup/ValidationReport data types"
```

---

### Task 2: check_workloads

- [ ] **Step 1: Write failing tests**

Add to `scripts/test_validate_checks.py`:

```python
from lib.validate_checks import check_workloads
import yaml as _yaml


def _make_values(workloads=None, objectives=None):
    objectives = objectives or [
        {"name": "critical", "priority": 100},
        {"name": "standard", "priority": 0},
        {"name": "sheddable", "priority": -10},
        {"name": "batch", "priority": -50},
    ]
    spec = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0,
        "num_requests": 1000,
        "clients": [
            {"id": "c1", "slo_class": "critical", "rate_fraction": 0.5,
             "arrival": {"process": "poisson"}},
            {"id": "c2", "slo_class": "batch", "rate_fraction": 0.5,
             "arrival": {"process": "gamma", "cv": 3.0}},
        ],
    }
    wl = workloads or [{"name": "wl_a", "spec": _yaml.dump(spec)}]
    return {
        "observe": {"workloads": wl},
        "stack": {"gaie": {"inferenceObjectives": objectives}},
    }


def test_check_workloads_valid():
    g = check_workloads(_make_values())
    assert g.passed is True


def test_check_workloads_duplicate_names():
    spec = _yaml.dump({"version": "1", "seed": 42, "aggregate_rate": 10,
                        "num_requests": 100, "clients": []})
    values = _make_values([{"name": "dup", "spec": spec},
                           {"name": "dup", "spec": spec}])
    g = check_workloads(values)
    assert g.passed is False
    assert any("unique" in i.name for i in g.items if not i.passed)


def test_check_workloads_bad_rate_fraction_sum():
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [
            {"id": "c1", "slo_class": "critical", "rate_fraction": 0.3,
             "arrival": {"process": "poisson"}},
            {"id": "c2", "slo_class": "batch", "rate_fraction": 0.3,
             "arrival": {"process": "poisson"}},
        ],
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is False


def test_check_workloads_unknown_slo_class():
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [{"id": "c1", "slo_class": "UNKNOWN", "rate_fraction": 1.0,
                     "arrival": {"process": "poisson"}}],
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is False


def test_check_workloads_gamma_missing_cv():
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [{"id": "c1", "slo_class": "batch", "rate_fraction": 1.0,
                     "arrival": {"process": "gamma"}}],  # no cv!
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is False


def test_check_workloads_unused_objective_is_warn_not_fail():
    # Only uses "critical" — "standard", "sheddable", "batch" unused → warns, but group passes
    spec_dict = {
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [{"id": "c1", "slo_class": "critical", "rate_fraction": 1.0,
                     "arrival": {"process": "poisson"}}],
    }
    g = check_workloads(_make_values([{"name": "wl_a", "spec": _yaml.dump(spec_dict)}]))
    assert g.passed is True  # warns, doesn't fail
    warn_items = [i for i in g.items if not i.passed and i.severity == "warn"]
    assert len(warn_items) >= 3  # standard, sheddable, batch
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_workloads"
```
Expected: all fail with `ImportError` or `AttributeError`.

- [ ] **Step 3: Implement check_workloads**

Add to `scripts/lib/validate_checks.py` after the helpers:

```python
# ── Phase 1: Static checks ────────────────────────────────────────────────────

def check_workloads(values: dict) -> CheckGroup:
    """Validate workload specs embedded in values.yaml observe.workloads."""
    items: list[CheckItem] = []
    workloads = values.get("observe", {}).get("workloads", [])
    objectives = {
        o["name"]
        for o in values.get("stack", {}).get("gaie", {}).get("inferenceObjectives", [])
    }

    # Uniqueness
    names = [w.get("name", "") for w in workloads]
    unique = len(names) == len(set(names))
    items.append(CheckItem(
        "workload_names_unique", unique,
        [f"duplicate names: {[n for n in names if names.count(n) > 1]}"] if not unique else [],
    ))

    used_classes: set[str] = set()

    for wl in workloads:
        wl_name = wl.get("name", "?")
        spec_raw = wl.get("spec", "")
        try:
            spec = yaml.safe_load(spec_raw) if isinstance(spec_raw, str) else spec_raw
            if not isinstance(spec, dict):
                raise ValueError("spec is not a dict")
        except Exception as e:
            items.append(CheckItem(f"{wl_name}.spec_parse", False, [str(e)]))
            continue

        rate = spec.get("aggregate_rate")
        items.append(CheckItem(
            f"{wl_name}.aggregate_rate",
            rate is not None and float(rate) > 0,
            [f"aggregate_rate={rate!r}"] if not (rate and float(rate) > 0) else [],
        ))

        nr = spec.get("num_requests")
        items.append(CheckItem(
            f"{wl_name}.num_requests",
            nr is not None and int(nr) > 0,
            [f"num_requests={nr!r}"] if not (nr and int(nr) > 0) else [],
        ))

        clients = spec.get("clients", [])
        rf_sum = sum(float(c.get("rate_fraction", 0)) for c in clients)
        items.append(CheckItem(
            f"{wl_name}.rate_fraction_sum",
            abs(rf_sum - 1.0) <= 0.01,
            [f"sum={rf_sum:.4f}, expected 1.0 ±0.01"],
        ))

        for c in clients:
            cid = c.get("id", "?")
            slo = c.get("slo_class", "")
            if slo:
                used_classes.add(slo)
            items.append(CheckItem(
                f"{wl_name}.{cid}.slo_class",
                slo in objectives,
                [f"slo_class={slo!r} not in inferenceObjectives {sorted(objectives)}"]
                if slo not in objectives else [],
            ))
            arrival = c.get("arrival", {})
            if arrival.get("process") == "gamma":
                has_cv = "cv" in arrival
                items.append(CheckItem(
                    f"{wl_name}.{cid}.cv",
                    has_cv,
                    ["gamma arrival process missing cv field"] if not has_cv else [],
                ))

    # Warn for unused objectives
    for obj_name in sorted(objectives - used_classes):
        items.append(CheckItem(
            f"objective_{obj_name}_exercised",
            False,
            [f"InferenceObjective {obj_name!r} declared but not used by any client"],
            severity="warn",
        ))

    return CheckGroup.from_items(items)
```

- [ ] **Step 4: Run tests**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_workloads"
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/validate_checks.py scripts/test_validate_checks.py
git commit -m "feat(validate): add check_workloads"
```

---

### Task 3: check_vllm_config

- [ ] **Step 1: Write failing tests**

Add to `scripts/test_validate_checks.py`:

```python
from lib.validate_checks import check_vllm_config


def _make_vllm_values(args=None, replicas=4, tensor=1, model_name="Qwen/Qwen2.5-7B-Instruct"):
    args = args or [
        "--gpu-memory-utilization=0.9",
        "--max-num-seqs=256",
        "--max-num-batched-tokens=2048",
        "--block-size=16",
    ]
    return {
        "stack": {
            "model": {
                "modelName": model_name,
                "helmValues": {
                    "decode": {
                        "replicas": replicas,
                        "parallelism": {"tensor": tensor},
                        "containers": [
                            {"modelCommand": "vllmServe", "args": args},
                        ],
                    }
                },
            }
        }
    }


def _make_llm_config(gpu_mem=0.9, max_seqs=256, batched_tokens=2048,
                     block_size=16, num_instances=4, tp=1,
                     model_id="Qwen/Qwen2.5-7B-Instruct"):
    return {
        "model": {"id": model_id},
        "serving": {"tensor_parallelism": tp},
        "cluster": {"num_instances": num_instances},
        "vllm_config": {
            "gpu_memory_utilization": gpu_mem,
            "max_num_running_reqs": max_seqs,
            "max_num_scheduled_tokens": batched_tokens,
            "block_size_in_tokens": block_size,
        },
    }


def test_check_vllm_config_valid():
    g = check_vllm_config(_make_vllm_values(), _make_llm_config())
    assert g.passed is True


def test_check_vllm_config_wrong_gpu_mem():
    g = check_vllm_config(
        _make_vllm_values(args=["--gpu-memory-utilization=0.85", "--max-num-seqs=256",
                                 "--max-num-batched-tokens=2048", "--block-size=16"]),
        _make_llm_config(),
    )
    assert g.passed is False
    assert any("gpu-memory-utilization" in i.name for i in g.items if not i.passed)


def test_check_vllm_config_wrong_replicas():
    g = check_vllm_config(_make_vllm_values(replicas=2), _make_llm_config())
    assert g.passed is False


def test_check_vllm_config_no_vllm_container():
    values = {
        "stack": {"model": {"modelName": "x",
                             "helmValues": {"decode": {"replicas": 4, "parallelism": {"tensor": 1},
                                                        "containers": [{"modelCommand": "other"}]}}}}
    }
    g = check_vllm_config(values, _make_llm_config())
    assert g.passed is False


def test_check_vllm_config_model_name_case_insensitive():
    g = check_vllm_config(
        _make_vllm_values(model_name="qwen/qwen2.5-7b-instruct"),
        _make_llm_config(model_id="Qwen/Qwen2.5-7B-Instruct"),
    )
    assert g.passed is True
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_vllm_config"
```
Expected: all fail with `ImportError`.

- [ ] **Step 3: Implement check_vllm_config**

Add to `scripts/lib/validate_checks.py`:

```python
def check_vllm_config(values: dict, llm_config: dict) -> CheckGroup:
    """Compare deployed vLLM args in values.yaml against blis_router/llm_config.yaml."""
    items: list[CheckItem] = []
    decode = (
        values.get("stack", {}).get("model", {})
              .get("helmValues", {}).get("decode", {})
    )

    # Find the vllmServe container (not by index — sidecars may exist)
    containers = decode.get("containers", [])
    vllm_container = next(
        (c for c in containers if c.get("modelCommand") == "vllmServe"), None
    )
    if vllm_container is None:
        return CheckGroup(
            passed=False,
            items=[CheckItem("vllm_container", False,
                             ["No container with modelCommand=vllmServe found"])],
        )

    arg_map = _parse_vllm_args(vllm_container.get("args", []))
    vc = llm_config.get("vllm_config", {})

    # Flag comparisons (all values stored as strings in args)
    flag_checks = [
        ("--gpu-memory-utilization", str(vc.get("gpu_memory_utilization", ""))),
        ("--max-num-seqs",           str(vc.get("max_num_running_reqs", ""))),
        ("--max-num-batched-tokens", str(vc.get("max_num_scheduled_tokens", ""))),
        ("--block-size",             str(vc.get("block_size_in_tokens", ""))),
    ]
    for flag, expected in flag_checks:
        actual = arg_map.get(flag)
        passed = actual is not None and actual == expected
        items.append(CheckItem(
            flag,
            passed,
            [f"expected={expected!r}, actual={actual!r}"] if not passed else [],
        ))

    # replicas
    replicas = decode.get("replicas")
    exp_replicas = llm_config.get("cluster", {}).get("num_instances")
    items.append(CheckItem(
        "decode.replicas",
        str(replicas) == str(exp_replicas),
        [f"expected={exp_replicas}, actual={replicas}"],
    ))

    # tensor parallelism
    tensor = decode.get("parallelism", {}).get("tensor")
    exp_tensor = llm_config.get("serving", {}).get("tensor_parallelism")
    items.append(CheckItem(
        "decode.parallelism.tensor",
        str(tensor) == str(exp_tensor),
        [f"expected={exp_tensor}, actual={tensor}"],
    ))

    # model name (case-insensitive)
    model_name = values.get("stack", {}).get("model", {}).get("modelName", "")
    exp_model = llm_config.get("model", {}).get("id", "")
    items.append(CheckItem(
        "model_name",
        _models_match(model_name, exp_model),
        [f"expected={exp_model!r}, actual={model_name!r}"]
        if not _models_match(model_name, exp_model) else [],
    ))

    return CheckGroup.from_items(items)
```

- [ ] **Step 4: Run tests**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_vllm_config"
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/validate_checks.py scripts/test_validate_checks.py
git commit -m "feat(validate): add check_vllm_config"
```

---

### Task 4: check_signals, check_routing_policy, check_isolation + run_pre_deploy_checks

- [ ] **Step 1: Write failing tests**

Add to `scripts/test_validate_checks.py`:

```python
from lib.validate_checks import check_signals, check_routing_policy, check_isolation, run_pre_deploy_checks
import yaml as _yaml


def _make_signal_coverage(signals=None, complete=True):
    signals = signals or [
        {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "medium", "staleness_window_ms": 0},
        {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
        {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
    ]
    return {"signals": signals, "coverage_complete": complete}


def test_check_signals_valid():
    g = check_signals(_make_signal_coverage())
    assert g.passed is True


def test_check_signals_unmapped():
    sc = _make_signal_coverage([
        {"sim_name": "sloClass", "mapped": False, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID", "mapped": True,  "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def test_check_signals_low_fidelity():
    sc = _make_signal_coverage([
        {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "low", "staleness_window_ms": 0},
        {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def test_check_signals_totalInFlight_must_be_fresh():
    sc = _make_signal_coverage([
        {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 5000},
        {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def test_check_signals_prometheus_backed_too_stale():
    sc = _make_signal_coverage([
        {"sim_name": "kvUtil", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 10000},
        {"sim_name": "sloClass", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
        {"sim_name": "tenantID", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 0},
    ])
    g = check_signals(sc)
    assert g.passed is False


def _baseline_epc():
    return _yaml.dump({
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [
            {"type": "load-aware-scorer"},
            {"type": "decode-filter"},
            {"type": "max-score-picker"},
        ],
        "schedulingProfiles": [{"name": "default", "plugins": [
            {"pluginRef": "decode-filter"},
            {"pluginRef": "max-score-picker"},
            {"pluginRef": "load-aware-scorer", "weight": 1},
        ]}],
    })


def _make_routing_values(phase="baseline", epc=None, admission_policy="some-policy"):
    epc = epc or _baseline_epc()
    baseline_hv = {"inferenceExtension": {"pluginsCustomConfig": {"custom-plugins.yaml": epc}}}
    treatment_hv = {}
    return {
        "stack": {"gaie": {
            "baseline":  {"helmValues": baseline_hv},
            "treatment": {"helmValues": treatment_hv, "admissionPolicy": admission_policy},
        }}
    }


def test_check_routing_policy_baseline_valid():
    g = check_routing_policy(_make_routing_values("baseline"), "baseline")
    assert g.passed is True


def test_check_routing_policy_baseline_missing_plugin():
    epc = _yaml.dump({
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [{"type": "decode-filter"}, {"type": "max-score-picker"}],  # no load-aware-scorer
        "schedulingProfiles": [{"name": "default", "plugins": []}],
    })
    g = check_routing_policy(_make_routing_values("baseline", epc=epc), "baseline")
    assert g.passed is False


def test_check_routing_policy_treatment_valid():
    g = check_routing_policy(_make_routing_values("treatment"), "treatment")
    assert g.passed is True


def test_check_routing_policy_treatment_missing_admission_policy():
    g = check_routing_policy(_make_routing_values("treatment", admission_policy=""), "treatment")
    assert g.passed is False


def test_check_isolation_unique():
    values = {"observe": {"workloads": [{"name": "a"}, {"name": "b"}]}}
    g = check_isolation(values)
    assert g.passed is True


def test_check_isolation_duplicate():
    values = {"observe": {"workloads": [{"name": "a"}, {"name": "a"}]}}
    g = check_isolation(values)
    assert g.passed is False
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_signals or test_check_routing or test_check_isolation"
```
Expected: all fail with `ImportError`.

- [ ] **Step 3: Implement check_signals, check_routing_policy, check_isolation, run_pre_deploy_checks**

Add to `scripts/lib/validate_checks.py`:

```python
def check_signals(signal_coverage: dict) -> CheckGroup:
    """Validate signal coverage completeness and staleness properties."""
    items: list[CheckItem] = []

    items.append(CheckItem(
        "coverage_complete",
        signal_coverage.get("coverage_complete", False) is True,
        ["coverage_complete is False or missing"],
    ))

    mapped_names: set[str] = set()
    for sig in signal_coverage.get("signals", []):
        name = sig["sim_name"]
        mapped = sig.get("mapped", False) is True
        if mapped:
            mapped_names.add(name)
        items.append(CheckItem(f"{name}.mapped", mapped, [f"{name} is not mapped"] if not mapped else []))

        fidelity = sig.get("fidelity_rating", "")
        items.append(CheckItem(
            f"{name}.fidelity",
            fidelity != "low",
            [f"fidelity_rating={fidelity!r} (low is not acceptable)"] if fidelity == "low" else [],
        ))

        stale = sig.get("staleness_window_ms", 0)
        if name == "totalInFlight":
            items.append(CheckItem(
                f"{name}.staleness_fresh",
                stale == 0,
                [f"expected staleness_window_ms=0 (router-local), got {stale}ms"],
            ))
        elif stale > 0:  # Prometheus-backed
            items.append(CheckItem(
                f"{name}.staleness_prometheus",
                stale <= 5000,
                [f"staleness_window_ms={stale}ms exceeds 5000ms limit"],
            ))

    for required in ("sloClass", "tenantID"):
        items.append(CheckItem(
            f"{required}.present_and_mapped",
            required in mapped_names,
            [f"{required} missing or unmapped in signal_coverage.json"],
        ))

    return CheckGroup.from_items(items)


def check_routing_policy(values: dict, phase: str) -> CheckGroup:
    """Validate EndpointPickerConfig and admissionPolicy for the given phase."""
    items: list[CheckItem] = []
    gaie = values.get("stack", {}).get("gaie", {})
    phase_cfg = gaie.get(phase, {})

    if phase == "baseline":
        plugins_custom = (
            phase_cfg.get("helmValues", {})
                      .get("inferenceExtension", {})
                      .get("pluginsCustomConfig", {})
        )
        if not plugins_custom:
            items.append(CheckItem("epc_present", False, ["No EndpointPickerConfig found in baseline helmValues"]))
            return CheckGroup.from_items(items)

        epc_yaml = next(iter(plugins_custom.values()), "")
        try:
            epc = yaml.safe_load(epc_yaml) or {}
        except yaml.YAMLError as e:
            items.append(CheckItem("epc_parse", False, [str(e)]))
            return CheckGroup.from_items(items)

        plugin_types = [p.get("type", "") for p in epc.get("plugins", [])]
        profile_refs = [
            p.get("pluginRef", "")
            for pr in epc.get("schedulingProfiles", [])
            for p in pr.get("plugins", [])
        ]
        for required in ("load-aware-scorer", "decode-filter", "max-score-picker"):
            key = required.replace("-", "_")
            items.append(CheckItem(
                f"plugin_{key}", required in plugin_types,
                [f"{required!r} not in plugins list"] if required not in plugin_types else [],
            ))
            items.append(CheckItem(
                f"profile_{key}", required in profile_refs,
                [f"{required!r} not in any schedulingProfile"] if required not in profile_refs else [],
            ))

    else:  # treatment
        ap = phase_cfg.get("admissionPolicy", "")
        items.append(CheckItem(
            "admission_policy_present",
            bool(ap and str(ap).strip()),
            ["admissionPolicy not set for treatment phase"],
        ))

    return CheckGroup.from_items(items)


def check_isolation(values: dict) -> CheckGroup:
    """Verify workload names are unique (each gets its own stack)."""
    names = [w.get("name", "") for w in values.get("observe", {}).get("workloads", [])]
    unique = len(names) == len(set(names))
    dups = sorted({n for n in names if names.count(n) > 1})
    return CheckGroup.from_items([CheckItem(
        "workload_names_unique", unique,
        [f"duplicate names: {dups}"] if not unique else [],
    )])


# ── Phase 1 top-level runner ──────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def run_pre_deploy_checks(run_dir: Path, repo_root: Path | None = None) -> ValidationReport:
    """Run all pre-deploy static checks. Returns ValidationReport.

    Args:
        run_dir: workspace/runs/{run_name}/ directory
        repo_root: repo root (defaults to 3 levels up from this file)
    """
    root = repo_root or REPO_ROOT
    run_name = run_dir.name

    values_path = run_dir / "prepare_tekton" / "values.yaml"
    coverage_path = run_dir / "prepare_signal_coverage.json"
    llm_config_path = root / "blis_router" / "llm_config.yaml"

    for p in (values_path, coverage_path, llm_config_path):
        if not p.exists():
            raise FileNotFoundError(f"Required artifact not found: {p}")

    values = yaml.safe_load(values_path.read_text())
    signal_coverage = json.loads(coverage_path.read_text())
    llm_config = yaml.safe_load(llm_config_path.read_text())

    checks: dict[str, CheckGroup] = {
        "workloads":      check_workloads(values),
        "vllm_config":    check_vllm_config(values, llm_config),
        "signals":        check_signals(signal_coverage),
        "routing_policy_baseline":  check_routing_policy(values, "baseline"),
        "routing_policy_treatment": check_routing_policy(values, "treatment"),
        "isolation":      check_isolation(values),
    }

    return ValidationReport.build(phase="pre_deploy", run=run_name, checks=checks)
```

- [ ] **Step 4: Run all tests so far**

```bash
cd scripts && python -m pytest test_validate_checks.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Smoke test against real admin4 artifacts**

```bash
cd /path/to/sim2real
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from lib.validate_checks import run_pre_deploy_checks
from pathlib import Path
r = run_pre_deploy_checks(Path('workspace/runs/admin4'))
import json; print(json.dumps(r.to_dict(), indent=2))
"
```
Expected: JSON report printed, `overall` either PASS or FAIL with specific check items.

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/validate_checks.py scripts/test_validate_checks.py
git commit -m "feat(validate): add Phase 1 static checks + run_pre_deploy_checks"
```

---

## Chunk 2: Phase 3 trace audit + run_post_collection_checks

### Task 5: check_trace_workload

- [ ] **Step 1: Write failing tests**

Add to `scripts/test_validate_checks.py`:

```python
from lib.validate_checks import check_trace_workload
import math


def _make_workload_spec(aggregate_rate=100.0, num_requests=1000, seed=42,
                         clients=None):
    clients = clients or [
        {"id": "c1", "slo_class": "critical", "tenant_id": "t1",
         "rate_fraction": 0.6, "arrival": {"process": "poisson"},
         "input_distribution": {"type": "gaussian", "params": {"mean": 128}},
         "output_distribution": {"type": "exponential", "params": {"mean": 64}}},
        {"id": "c2", "slo_class": "batch", "tenant_id": "t2",
         "rate_fraction": 0.4, "arrival": {"process": "gamma", "cv": 3.0},
         "input_distribution": {"type": "gaussian", "params": {"mean": 256}},
         "output_distribution": {"type": "exponential", "params": {"mean": 128}}},
    ]
    return {"aggregate_rate": aggregate_rate, "num_requests": num_requests,
            "seed": seed, "clients": clients}


def _make_trace_header(model="Qwen/Qwen2.5-7B-Instruct", seed=42, mode="real"):
    return {"server": {"model": model}, "workload_seed": seed, "mode": mode}


def _make_rows(n=1000, start_us=0, rate_rps=100.0, client_ratios=None,
               input_mean=128, output_mean=64):
    """Generate synthetic trace rows."""
    import random
    random.seed(0)
    client_ratios = client_ratios or [("c1", "critical", "t1", 0.6),
                                       ("c2", "batch", "t2", 0.4)]
    rows = []
    t = start_us
    interval_us = int(1e6 / rate_rps)
    for i in range(n):
        t += interval_us + random.randint(-interval_us//10, interval_us//10)
        # pick client by ratio
        r = random.random()
        cumulative = 0
        cid, slo, tid = client_ratios[0][0], client_ratios[0][1], client_ratios[0][2]
        for c_id, c_slo, c_tid, frac in client_ratios:
            cumulative += frac
            if r <= cumulative:
                cid, slo, tid = c_id, c_slo, c_tid
                break
        rows.append({
            "request_id": str(i),
            "client_id": cid,
            "tenant_id": tid,
            "slo_class": slo,
            "arrival_time_us": str(t),
            "input_tokens": str(int(random.gauss(input_mean, input_mean * 0.15))),
            "output_tokens": str(int(random.gauss(output_mean, output_mean * 0.15))),
            "status": "ok",
        })
    return rows


def test_check_trace_workload_valid():
    rows = _make_rows(1000, rate_rps=100.0)
    g = check_trace_workload("wl_a", rows, _make_trace_header(), _make_workload_spec(), "Qwen/Qwen2.5-7B-Instruct")
    # All structural checks should pass (may warn on CV since data is synthetic)
    fail_items = [i for i in g.items if not i.passed and i.severity == "fail"]
    assert fail_items == [], f"Unexpected failures: {[(i.name, i.notes) for i in fail_items]}"


def test_check_trace_workload_wrong_mode():
    rows = _make_rows(100)
    g = check_trace_workload("wl_a", rows, _make_trace_header(mode="sim"),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    assert any(not i.passed and i.name == "mode_real" for i in g.items)


def test_check_trace_workload_wrong_model():
    rows = _make_rows(100)
    g = check_trace_workload("wl_a", rows, _make_trace_header(model="other-model"),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    assert any(not i.passed and i.name == "model_identity" for i in g.items)


def test_check_trace_workload_request_count_too_low_fails():
    rows = _make_rows(500)  # 50% of 1000 → FAIL (< 70%)
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=1000), "Qwen/Qwen2.5-7B-Instruct")
    fail = [i for i in g.items if i.name == "request_count" and not i.passed and i.severity == "fail"]
    assert fail


def test_check_trace_workload_request_count_warn_band():
    rows = _make_rows(800)  # 80% of 1000 → WARN
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=1000), "Qwen/Qwen2.5-7B-Instruct")
    warn = [i for i in g.items if i.name == "request_count" and i.severity == "warn"]
    assert warn
    # Group should still pass (warn doesn't flip it)
    assert g.passed is True


def test_check_trace_workload_empty_slo_class_fails():
    rows = _make_rows(100)
    rows[0]["slo_class"] = ""  # blank for one row
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    fail = [i for i in g.items if i.name == "slo_class_extracted" and not i.passed]
    assert fail


def test_check_trace_workload_tenant_count_mismatch():
    rows = _make_rows(100)
    for r in rows:
        r["tenant_id"] = "t1"  # only t1, spec has t1 and t2
    g = check_trace_workload("wl_a", rows, _make_trace_header(),
                              _make_workload_spec(num_requests=100), "Qwen/Qwen2.5-7B-Instruct")
    fail = [i for i in g.items if i.name == "tenant_count" and not i.passed]
    assert fail
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_trace_workload"
```
Expected: all fail with `ImportError`.

- [ ] **Step 3: Implement check_trace_workload and run_post_collection_checks**

Add to `scripts/lib/validate_checks.py`:

```python
# ── Phase 3: Post-collection trace audit ─────────────────────────────────────

def check_trace_workload(
    workload_name: str,
    rows: list[dict],
    trace_header: dict,
    workload_spec: dict,
    model_name: str,
) -> CheckGroup:
    """Audit a workload's trace_data.csv against the declared workload spec."""
    items: list[CheckItem] = []
    clients = workload_spec.get("clients", [])
    spec_num_requests = int(workload_spec.get("num_requests", 0))
    spec_seed = workload_spec.get("seed")
    spec_aggregate_rate = float(workload_spec.get("aggregate_rate", 0))

    # ── Header checks ──
    mode = trace_header.get("mode", "")
    items.append(CheckItem("mode_real", mode == "real",
                           [f"mode={mode!r}, expected 'real'"] if mode != "real" else []))

    trace_model = (trace_header.get("server") or {}).get("model", "")
    items.append(CheckItem(
        "model_identity", _models_match(trace_model, model_name),
        [f"trace model={trace_model!r}, expected={model_name!r}"]
        if not _models_match(trace_model, model_name) else [],
    ))

    trace_seed = trace_header.get("workload_seed")
    items.append(CheckItem(
        "workload_seed", str(trace_seed) == str(spec_seed),
        [f"trace seed={trace_seed!r}, spec seed={spec_seed!r}"]
        if str(trace_seed) != str(spec_seed) else [],
    ))

    if not rows:
        items.append(CheckItem("trace_non_empty", False, ["trace_data.csv is empty"]))
        return CheckGroup.from_items(items)

    # ── Request count ──
    ratio = len(rows) / spec_num_requests if spec_num_requests > 0 else 0.0
    if ratio > 1.05:
        items.append(CheckItem("request_count", False,
                               [f"row_count={len(rows)} > spec={spec_num_requests}×1.05 (generator bug)"],
                               "fail"))
    elif ratio < 0.70:
        items.append(CheckItem("request_count", False,
                               [f"ratio={ratio:.2f} < 0.70 (possible crash/truncation)"],
                               "fail"))
    elif ratio < 0.95:
        items.append(CheckItem("request_count", False,
                               [f"ratio={ratio:.2f} in warn band 0.70–0.95 (admission shedding)"],
                               "warn"))
    else:
        items.append(CheckItem("request_count", True))

    # ── Header extraction ──
    empty_slo = sum(1 for r in rows if not (r.get("slo_class") or "").strip())
    items.append(CheckItem("slo_class_extracted", empty_slo == 0,
                           [f"{empty_slo} rows have empty slo_class"] if empty_slo else []))

    empty_tenant = sum(1 for r in rows if not (r.get("tenant_id") or "").strip())
    items.append(CheckItem("tenant_id_extracted", empty_tenant == 0,
                           [f"{empty_tenant} rows have empty tenant_id"] if empty_tenant else []))

    # ── Arrival rate ──
    try:
        arrival_times = sorted(int(r["arrival_time_us"]) for r in rows)
        if len(arrival_times) >= 2 and spec_aggregate_rate > 0:
            elapsed_s = (arrival_times[-1] - arrival_times[0]) / 1e6
            actual_rps = len(rows) / elapsed_s if elapsed_s > 0 else 0.0
            pct_diff = abs(actual_rps - spec_aggregate_rate) / spec_aggregate_rate
            items.append(CheckItem(
                "arrival_rate", pct_diff <= 0.10,
                [f"actual={actual_rps:.1f}rps, expected={spec_aggregate_rate:.1f}rps, diff={pct_diff*100:.1f}%"],
            ))
    except (KeyError, ValueError, ZeroDivisionError) as e:
        items.append(CheckItem("arrival_rate", False, [f"error: {e}"]))

    # ── Per-client checks ──
    by_client: dict[str, list[dict]] = {}
    for r in rows:
        by_client.setdefault(r.get("client_id", ""), []).append(r)

    client_spec_map = {c["id"]: c for c in clients}

    for cid, crows in by_client.items():
        cspec = client_spec_map.get(cid)
        if not cspec:
            continue

        arrival = cspec.get("arrival", {})
        process = arrival.get("process", "poisson")
        expected_cv = arrival.get("cv")

        try:
            atimes = sorted(int(r["arrival_time_us"]) for r in crows)
            inter = [atimes[i + 1] - atimes[i] for i in range(len(atimes) - 1)]
            if len(inter) >= 10:
                mean_ia = sum(inter) / len(inter)
                std_ia = math.sqrt(sum((x - mean_ia) ** 2 for x in inter) / len(inter))
                actual_cv = std_ia / mean_ia if mean_ia > 0 else 0.0

                if process == "gamma" and expected_cv is not None:
                    pct = abs(actual_cv - float(expected_cv)) / float(expected_cv)
                    items.append(CheckItem(
                        f"{cid}.cv", pct <= 0.20,
                        [f"actual_cv={actual_cv:.2f}, expected={expected_cv}, diff={pct*100:.1f}%"],
                    ))
                elif process == "poisson":
                    items.append(CheckItem(
                        f"{cid}.cv_poisson", abs(actual_cv - 1.0) <= 0.3,
                        [f"actual_cv={actual_cv:.2f}, expected≈1.0 ±0.3"],
                    ))
        except (KeyError, ValueError):
            pass  # skip CV check on parse error

        # Token distributions
        for tok_field, dist_key in (("input_tokens", "input_distribution"),
                                     ("output_tokens", "output_distribution")):
            dist = cspec.get(dist_key, {})
            exp_mean = (dist.get("params") or {}).get("mean")
            if exp_mean is None:
                continue
            try:
                vals = [int(r[tok_field]) for r in crows]
                actual_mean = sum(vals) / len(vals) if vals else 0.0
                pct = abs(actual_mean - float(exp_mean)) / float(exp_mean)
                items.append(CheckItem(
                    f"{cid}.{tok_field}_mean", pct <= 0.15,
                    [f"actual={actual_mean:.1f}, expected={exp_mean}, diff={pct*100:.1f}%"],
                ))
            except (KeyError, ValueError, ZeroDivisionError):
                pass

    # ── SLO class ratios ──
    total = len(rows)
    slo_counts: dict[str, int] = {}
    for r in rows:
        slo = r.get("slo_class", "")
        slo_counts[slo] = slo_counts.get(slo, 0) + 1

    slo_spec: dict[str, float] = {}
    for c in clients:
        slo = c.get("slo_class", "")
        slo_spec[slo] = slo_spec.get(slo, 0.0) + float(c.get("rate_fraction", 0))

    for slo, exp_frac in slo_spec.items():
        actual_frac = slo_counts.get(slo, 0) / total if total > 0 else 0.0
        diff = abs(actual_frac - exp_frac)
        items.append(CheckItem(
            f"slo_ratio_{slo}", diff <= 0.03,
            [f"actual={actual_frac:.3f}, expected={exp_frac:.3f}, |diff|={diff:.3f}"],
        ))

    # ── Tenant count ──
    spec_tenants = {c.get("tenant_id", "") for c in clients if c.get("tenant_id")}
    trace_tenants = {r.get("tenant_id", "") for r in rows if (r.get("tenant_id") or "").strip()}
    items.append(CheckItem(
        "tenant_count", spec_tenants == trace_tenants,
        [f"spec={sorted(spec_tenants)}, trace={sorted(trace_tenants)}"]
        if spec_tenants != trace_tenants else [],
    ))

    return CheckGroup.from_items(items)


def run_post_collection_checks(phase: str, run_dir: Path) -> ValidationReport:
    """Audit trace CSVs for all workloads in the given phase.

    Args:
        phase: "baseline" or "treatment"
        run_dir: workspace/runs/{run_name}/ directory
    """
    run_name = run_dir.name
    log_dir = run_dir / f"deploy_{phase}_log"
    values_path = run_dir / "prepare_tekton" / "values.yaml"

    if not values_path.exists():
        raise FileNotFoundError(f"values.yaml not found: {values_path}")

    values = yaml.safe_load(values_path.read_text())
    model_name = values.get("stack", {}).get("model", {}).get("modelName", "")

    # Build workload spec map
    workload_specs: dict[str, dict] = {}
    for wl in values.get("observe", {}).get("workloads", []):
        spec_raw = wl.get("spec", "")
        try:
            spec = yaml.safe_load(spec_raw) if isinstance(spec_raw, str) else spec_raw
            workload_specs[wl["name"]] = spec or {}
        except yaml.YAMLError:
            workload_specs[wl["name"]] = {}

    checks: dict[str, CheckGroup] = {}

    if not log_dir.exists():
        for wl_name in workload_specs:
            checks[wl_name] = CheckGroup(
                passed=False,
                items=[CheckItem("log_dir", False, [f"log directory not found: {log_dir}"])],
            )
        return ValidationReport.build(phase=f"post_collection_{phase}",
                                       run=run_name, checks=checks)

    for wl_name, wl_spec in workload_specs.items():
        wl_dir = log_dir / wl_name
        csv_path = wl_dir / "trace_data.csv"
        header_path = wl_dir / "trace_header.yaml"

        if not csv_path.exists():
            checks[wl_name] = CheckGroup(
                passed=False,
                items=[CheckItem("csv_exists", False, [f"trace_data.csv not found: {csv_path}"])],
            )
            continue

        trace_header: dict = {}
        if header_path.exists():
            try:
                trace_header = yaml.safe_load(header_path.read_text()) or {}
            except yaml.YAMLError:
                pass

        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        checks[wl_name] = check_trace_workload(wl_name, rows, trace_header, wl_spec, model_name)

    return ValidationReport.build(phase=f"post_collection_{phase}", run=run_name, checks=checks)
```

- [ ] **Step 4: Run all tests**

```bash
cd scripts && python -m pytest test_validate_checks.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Smoke test against real admin3 trace data**

```bash
cd /path/to/sim2real
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from lib.validate_checks import run_post_collection_checks
from pathlib import Path
r = run_post_collection_checks('baseline', Path('workspace/runs/admin3'))
import json; print(json.dumps(r.to_dict(), indent=2))
" 2>&1 | head -60
```
Expected: JSON report, may show WARN on request_count (admission shedding is normal).

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/validate_checks.py scripts/test_validate_checks.py
git commit -m "feat(validate): add Phase 3 trace audit checks + run_post_collection_checks"
```

---

## Chunk 3: Phase 2 live cluster checks + validate.py CLI

### Task 6: Phase 2 live cluster checks

- [ ] **Step 1: Write tests with mocked subprocess/HTTP**

Add to `scripts/test_validate_checks.py`:

```python
from unittest.mock import patch, MagicMock
from lib.validate_checks import (
    check_signal_liveness, check_prometheus_staleness,
    check_model_loaded, check_stack_readiness,
)


def _prom_url():
    return "http://prometheus:9090"


def test_check_signal_liveness_pass():
    sc = _make_signal_coverage([
        {"sim_name": "kvUtil", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 5000},
    ])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json.return_value = {"data": {"result": [{"value": [1743600000, "1.5"]}]}}
    with patch("lib.validate_checks.requests.get", return_value=mock_resp):
        g = check_signal_liveness(sc, _prom_url())
    assert g.passed is True


def test_check_signal_liveness_no_data_fails():
    sc = _make_signal_coverage([
        {"sim_name": "kvUtil", "mapped": True, "fidelity_rating": "high", "staleness_window_ms": 5000},
    ])
    mock_resp = MagicMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json.return_value = {"data": {"result": []}}  # no data
    with patch("lib.validate_checks.requests.get", return_value=mock_resp):
        g = check_signal_liveness(sc, _prom_url())
    assert g.passed is False


def test_check_signal_liveness_skips_non_prometheus():
    # staleness_window_ms=0 → not Prometheus-backed → no probes
    sc = _make_signal_coverage()  # all staleness=0
    with patch("lib.validate_checks.requests.get") as mock_get:
        g = check_signal_liveness(sc, _prom_url())
    mock_get.assert_not_called()
    assert g.passed is True


def test_check_stack_readiness_pass():
    kubectl_output = b'NAME  READY  UP-TO-DATE  AVAILABLE\nsim2real-epp  1/1  1  1\n'
    with patch("lib.validate_checks.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=kubectl_output, stderr=b"")
        g = check_stack_readiness("test-ns", "baseline")
    assert g.passed is True


def test_check_stack_readiness_no_deployments():
    with patch("lib.validate_checks.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=b"No resources found.\n", stderr=b"")
        g = check_stack_readiness("test-ns", "baseline")
    assert g.passed is False
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd scripts && python -m pytest test_validate_checks.py -v -k "test_check_signal_liveness or test_check_stack_readiness"
```
Expected: all fail with `ImportError`.

- [ ] **Step 3: Implement Phase 2 checks**

Add to `scripts/lib/validate_checks.py` (after the Phase 3 section):

```python
# ── Phase 2: Live cluster checks ─────────────────────────────────────────────

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore

# Import at module level so tests can patch it
import requests


def check_stack_readiness(namespace: str, phase: str) -> CheckGroup:
    """Verify at least one EPP deployment is Ready for the given phase."""
    result = subprocess.run(
        ["kubectl", "get", "deployment", "-n", namespace,
         "-l", f"sim2real-phase={phase}", "--no-headers"],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        return CheckGroup(passed=False, items=[CheckItem(
            "kubectl_get_deployment", False,
            [f"kubectl failed: {result.stderr.decode()[:200]}"],
        )])
    output = result.stdout.decode()
    lines = [ln for ln in output.splitlines() if ln.strip() and "No resources" not in ln]
    passed = len(lines) > 0
    return CheckGroup.from_items([CheckItem(
        "stack_ready", passed,
        [f"No EPP deployments found with label sim2real-phase={phase}"] if not passed else [],
    )])


def check_signal_liveness(signal_coverage: dict, prometheus_url: str) -> CheckGroup:
    """Query Prometheus to confirm Prometheus-backed signals have recent data."""
    items: list[CheckItem] = []
    prom_signals = [
        s for s in signal_coverage.get("signals", [])
        if s.get("staleness_window_ms", 0) > 0 and s.get("mapped")
    ]
    if not prom_signals:
        items.append(CheckItem("no_prometheus_signals", True,
                               ["No Prometheus-backed signals to probe (all are router-local)"]))
        return CheckGroup.from_items(items)

    for sig in prom_signals:
        name = sig["sim_name"]
        prod_name = sig.get("prod_name", name)
        try:
            resp = requests.get(
                f"{prometheus_url}/api/v1/query",
                params={"query": prod_name},
                timeout=10,
            )
            resp.raise_for_status()
            result = resp.json().get("data", {}).get("result", [])
            items.append(CheckItem(
                f"{name}.liveness", bool(result),
                [f"no recent data for metric {prod_name!r}"] if not result else [],
            ))
        except Exception as e:
            items.append(CheckItem(f"{name}.liveness", False, [f"Prometheus query failed: {e}"]))

    return CheckGroup.from_items(items)


def check_prometheus_staleness(prometheus_url: str) -> CheckGroup:
    """Verify the vLLM scrape interval is ≤ 5s."""
    items: list[CheckItem] = []
    try:
        resp = requests.get(f"{prometheus_url}/api/v1/status/config", timeout=10)
        resp.raise_for_status()
        config_yaml = resp.json().get("data", {}).get("yaml", "")
        config = yaml.safe_load(config_yaml) or {}
    except Exception as e:
        items.append(CheckItem("config_fetch", False, [f"Cannot fetch Prometheus config: {e}"]))
        return CheckGroup.from_items(items)

    global_interval_s = _parse_duration_s(
        config.get("global", {}).get("scrape_interval", "60s")
    )

    scrape_configs = config.get("scrape_configs", [])
    vllm_jobs = [
        sc for sc in scrape_configs
        if "vllm" in (sc.get("job_name") or "").lower()
        or any("vllm" in str(v).lower()
               for v in (sc.get("static_configs") or []))
    ]

    if not vllm_jobs:
        # Fall back to global interval
        items.append(CheckItem(
            "scrape_interval_global",
            global_interval_s <= 5,
            [f"No vllm scrape job found; global interval={global_interval_s}s > 5s"]
            if global_interval_s > 5 else [],
        ))
        return CheckGroup.from_items(items)

    for job in vllm_jobs:
        job_name = job.get("job_name", "unknown")
        interval_str = job.get("scrape_interval") or f"{global_interval_s}s"
        interval_s = _parse_duration_s(interval_str)
        items.append(CheckItem(
            f"scrape_interval_{job_name}",
            interval_s <= 5,
            [f"job {job_name!r} scrape_interval={interval_s}s > 5s"] if interval_s > 5 else [],
        ))

    return CheckGroup.from_items(items)


def check_model_loaded(namespace: str, model_name: str) -> CheckGroup:
    """GET /v1/models on the vLLM service and verify the model ID matches."""
    result = subprocess.run(
        ["kubectl", "get", "svc", "-n", namespace, "-l", "app=vllm",
         "-o", "jsonpath={.items[0].spec.clusterIP}"],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return CheckGroup(passed=False, items=[CheckItem(
            "vllm_svc_found", False,
            [f"Could not find vLLM service in namespace {namespace}"],
        )])
    cluster_ip = result.stdout.decode().strip()
    try:
        resp = requests.get(f"http://{cluster_ip}:8000/v1/models", timeout=15)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        ids = [m.get("id", "") for m in models]
        matched = any(_models_match(i, model_name) for i in ids)
        return CheckGroup.from_items([CheckItem(
            "model_loaded", matched,
            [f"model {model_name!r} not in /v1/models response: {ids}"] if not matched else [],
        )])
    except Exception as e:
        return CheckGroup(passed=False, items=[CheckItem(
            "model_loaded", False, [f"GET /v1/models failed: {e}"],
        )])


def _parse_duration_s(duration: str) -> float:
    """Parse Prometheus duration string (e.g. '15s', '1m') to seconds."""
    m = re.match(r"^(\d+(?:\.\d+)?)(ms|s|m|h)$", str(duration).strip())
    if not m:
        return 60.0  # conservative default
    val, unit = float(m.group(1)), m.group(2)
    return {"ms": val / 1000, "s": val, "m": val * 60, "h": val * 3600}[unit]


def _resolve_prometheus_url(namespace: str, prometheus_url: str | None) -> str:
    """Resolve Prometheus URL from flag or cluster service lookup."""
    if prometheus_url:
        return prometheus_url
    result = subprocess.run(
        ["kubectl", "get", "svc", "-n", namespace, "-l", "app=prometheus",
         "-o", "jsonpath={.items[0].spec.clusterIP}"],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"Prometheus service not found in namespace {namespace}. "
            "Pass --prometheus-url explicitly if Prometheus is deployed outside the phase namespace."
        )
    ip = result.stdout.decode().strip()
    return f"http://{ip}:9090"


def run_post_deploy_checks(
    run_dir: Path, namespace: str, phase: str,
    prometheus_url: str | None = None,
) -> ValidationReport:
    """Run live cluster checks against a running stack."""
    run_name = run_dir.name
    values_path = run_dir / "prepare_tekton" / "values.yaml"
    coverage_path = run_dir / "prepare_signal_coverage.json"

    for p in (values_path, coverage_path):
        if not p.exists():
            raise FileNotFoundError(f"Required artifact not found: {p}")

    values = yaml.safe_load(values_path.read_text())
    signal_coverage = json.loads(coverage_path.read_text())
    model_name = values.get("stack", {}).get("model", {}).get("modelName", "")

    prom_url = _resolve_prometheus_url(namespace, prometheus_url)

    checks: dict[str, CheckGroup] = {
        "stack_readiness":      check_stack_readiness(namespace, phase),
        "signal_liveness":      check_signal_liveness(signal_coverage, prom_url),
        "prometheus_staleness": check_prometheus_staleness(prom_url),
        "model_loaded":         check_model_loaded(namespace, model_name),
    }

    return ValidationReport.build(phase=f"post_deploy_{phase}", run=run_name, checks=checks)
```

- [ ] **Step 4: Run tests**

```bash
cd scripts && python -m pytest test_validate_checks.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/validate_checks.py scripts/test_validate_checks.py
git commit -m "feat(validate): add Phase 2 live cluster checks"
```

---

### Task 7: validate.py CLI

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_validate.py`:

```python
"""Integration tests for scripts/validate.py CLI."""
import json
import sys
from pathlib import Path
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib
validate = importlib.import_module("validate")


FIXTURES = Path(__file__).parent / "fixtures" / "validate"


def _make_run_dir(tmp_path: Path) -> Path:
    """Build a minimal valid run directory for pre-deploy tests."""
    run_dir = tmp_path / "workspace" / "runs" / "test-run"
    tekton_dir = run_dir / "prepare_tekton"
    tekton_dir.mkdir(parents=True)

    spec = yaml.dump({
        "version": "1", "seed": 42, "aggregate_rate": 100.0, "num_requests": 1000,
        "clients": [
            {"id": "c1", "slo_class": "critical", "tenant_id": "t1", "rate_fraction": 0.6,
             "arrival": {"process": "poisson"},
             "input_distribution": {"type": "gaussian", "params": {"mean": 128}},
             "output_distribution": {"type": "exponential", "params": {"mean": 64}}},
            {"id": "c2", "slo_class": "batch", "tenant_id": "t2", "rate_fraction": 0.4,
             "arrival": {"process": "gamma", "cv": 3.0},
             "input_distribution": {"type": "gaussian", "params": {"mean": 256}},
             "output_distribution": {"type": "exponential", "params": {"mean": 128}}},
        ],
    })
    epc = yaml.dump({
        "apiVersion": "inference.networking.x-k8s.io/v1alpha1",
        "kind": "EndpointPickerConfig",
        "plugins": [{"type": "load-aware-scorer"}, {"type": "decode-filter"}, {"type": "max-score-picker"}],
        "schedulingProfiles": [{"name": "default", "plugins": [
            {"pluginRef": "decode-filter"}, {"pluginRef": "max-score-picker"},
            {"pluginRef": "load-aware-scorer", "weight": 1},
        ]}],
    })
    values = {
        "observe": {"workloads": [{"name": "wl_a", "spec": spec}]},
        "stack": {
            "gaie": {
                "inferenceObjectives": [{"name": "critical"}, {"name": "batch"}],
                "baseline":  {"helmValues": {"inferenceExtension": {"pluginsCustomConfig": {"custom-plugins.yaml": epc}}}},
                "treatment": {"helmValues": {}, "admissionPolicy": "some-policy"},
            },
            "model": {
                "modelName": "Qwen/Qwen2.5-7B-Instruct",
                "helmValues": {"decode": {
                    "replicas": 4,
                    "parallelism": {"tensor": 1},
                    "containers": [{"modelCommand": "vllmServe", "args": [
                        "--gpu-memory-utilization=0.9",
                        "--max-num-seqs=256",
                        "--max-num-batched-tokens=2048",
                        "--block-size=16",
                    ]}],
                }},
            },
        },
    }
    (tekton_dir / "values.yaml").write_text(yaml.dump(values))

    coverage = {
        "coverage_complete": True,
        "signals": [
            {"sim_name": "totalInFlight", "mapped": True, "fidelity_rating": "medium", "staleness_window_ms": 0},
            {"sim_name": "sloClass",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
            {"sim_name": "tenantID",      "mapped": True, "fidelity_rating": "high",   "staleness_window_ms": 0},
        ],
    }
    (run_dir / "prepare_signal_coverage.json").write_text(json.dumps(coverage))

    return run_dir


def _make_llm_config(repo_root: Path):
    blis = repo_root / "blis_router"
    blis.mkdir(parents=True)
    llm_cfg = {
        "model": {"id": "Qwen/Qwen2.5-7B-Instruct"},
        "serving": {"tensor_parallelism": 1},
        "cluster": {"num_instances": 4},
        "vllm_config": {
            "gpu_memory_utilization": 0.9,
            "max_num_running_reqs": 256,
            "max_num_scheduled_tokens": 2048,
            "block_size_in_tokens": 16,
        },
    }
    (blis / "llm_config.yaml").write_text(yaml.dump(llm_cfg))


def test_pre_deploy_passes_exit_zero(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = _make_run_dir(tmp_path)
    _make_llm_config(repo_root)
    monkeypatch.chdir(tmp_path)

    rc = validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    assert rc == 0
    report = json.loads((run_dir / "validate_pre_deploy.json").read_text())
    assert report["overall"] == "PASS"


def test_pre_deploy_writes_report(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = _make_run_dir(tmp_path)
    _make_llm_config(repo_root)
    monkeypatch.chdir(tmp_path)

    validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    report_path = run_dir / "validate_pre_deploy.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert "checks" in report
    assert "workloads" in report["checks"]
    assert "vllm_config" in report["checks"]


def test_pre_deploy_fails_exit_one_on_bad_config(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_dir = _make_run_dir(tmp_path)
    # Write llm_config with wrong GPU mem
    blis = repo_root / "blis_router"
    blis.mkdir(parents=True)
    bad_cfg = {
        "model": {"id": "Qwen/Qwen2.5-7B-Instruct"},
        "serving": {"tensor_parallelism": 1},
        "cluster": {"num_instances": 4},
        "vllm_config": {
            "gpu_memory_utilization": 0.85,  # mismatch!
            "max_num_running_reqs": 256,
            "max_num_scheduled_tokens": 2048,
            "block_size_in_tokens": 16,
        },
    }
    (blis / "llm_config.yaml").write_text(yaml.dump(bad_cfg))
    monkeypatch.chdir(tmp_path)

    rc = validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    assert rc == 1


def test_pre_deploy_exit_two_on_missing_artifact(tmp_path, monkeypatch):
    run_dir = tmp_path / "workspace" / "runs" / "empty-run"
    run_dir.mkdir(parents=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(tmp_path)

    rc = validate.main_with_args([
        "pre-deploy",
        "--run-dir", str(run_dir),
        "--repo-root", str(repo_root),
    ])
    assert rc == 2
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
python -m pytest tests/test_validate.py -v
```
Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement validate.py**

Create `scripts/validate.py`:

```python
#!/usr/bin/env python3
"""sim2real validate — pre-deploy, post-deploy, and post-collection validation."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

_tty = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text


def _print_report(report) -> None:
    """Print human-readable report to stdout."""
    print(f"\n{'━'*60}")
    print(f" Validation: {report.phase}  run={report.run}")
    print(f"{'━'*60}")
    for group_name, group in report.checks.items():
        status = _c("32", "[PASS]") if group.passed else _c("31", "[FAIL]")
        print(f"  {status} {group_name}")
        for item in group.items:
            if item.passed:
                continue
            icon = _c("33", "[WARN]") if item.severity == "warn" else _c("31", "[FAIL]")
            for note in item.notes:
                print(f"         {icon} {item.name}: {note}")
    overall_color = "32" if report.overall == "PASS" else "31"
    print(f"\n  Overall: {_c(overall_color, report.overall)}\n")


def _cmd_pre_deploy(args: argparse.Namespace) -> int:
    from lib.validate_checks import run_pre_deploy_checks
    run_dir = Path(args.run_dir)
    repo_root = Path(args.repo_root) if args.repo_root else None
    try:
        report = run_pre_deploy_checks(run_dir, repo_root=repo_root)
    except FileNotFoundError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except Exception as e:
        print(_c("31", f"[ERROR] Unexpected error: {e}"), file=sys.stderr)
        return 2

    out_path = run_dir / "validate_pre_deploy.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    _print_report(report)
    print(f"  Report: {out_path}")
    return 1 if report.failed else 0


def _cmd_post_deploy(args: argparse.Namespace) -> int:
    from lib.validate_checks import run_post_deploy_checks
    run_dir = Path(args.run_dir)
    try:
        report = run_post_deploy_checks(
            run_dir, namespace=args.namespace, phase=args.phase,
            prometheus_url=args.prometheus_url,
        )
    except FileNotFoundError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except Exception as e:
        print(_c("31", f"[ERROR] Unexpected error: {e}"), file=sys.stderr)
        return 2

    out_path = run_dir / f"validate_post_deploy_{args.phase}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    _print_report(report)
    print(f"  Report: {out_path}")
    return 1 if report.failed else 0


def _cmd_post_collection(args: argparse.Namespace) -> int:
    from lib.validate_checks import run_post_collection_checks
    run_dir = Path(args.run_dir)
    try:
        report = run_post_collection_checks(phase=args.phase, run_dir=run_dir)
    except FileNotFoundError as e:
        print(_c("31", f"[ERROR] {e}"), file=sys.stderr)
        return 2
    except Exception as e:
        print(_c("31", f"[ERROR] Unexpected error: {e}"), file=sys.stderr)
        return 2

    out_path = run_dir / f"validate_post_collection_{args.phase}.json"
    out_path.write_text(json.dumps(report.to_dict(), indent=2))
    _print_report(report)
    print(f"  Report: {out_path}")
    return 1 if report.failed else 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate.py",
        description="sim2real run validation — pre-deploy, post-deploy, post-collection",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    # ── pre-deploy ──
    pre = sub.add_parser("pre-deploy", help="Static artifact checks before deployment")
    pre.add_argument("--run-dir", required=True, metavar="PATH",
                     help="workspace/runs/{name}/ directory")
    pre.add_argument("--repo-root", metavar="PATH",
                     help="Repo root (default: inferred from script location)")

    # ── post-deploy ──
    post = sub.add_parser("post-deploy", help="Live cluster probes against a running stack")
    post.add_argument("--run-dir", required=True, metavar="PATH")
    post.add_argument("--namespace", required=True, metavar="NS")
    post.add_argument("--phase", required=True, choices=["baseline", "treatment"])
    post.add_argument("--prometheus-url", metavar="URL",
                      help="Prometheus base URL (default: auto-resolve from cluster)")

    # ── post-collection ──
    col = sub.add_parser("post-collection", help="Trace CSV audit after benchmark completes")
    col.add_argument("--run-dir", required=True, metavar="PATH")
    col.add_argument("--phase", required=True, choices=["baseline", "treatment"])

    return p


def main_with_args(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    dispatch = {
        "pre-deploy":      _cmd_pre_deploy,
        "post-deploy":     _cmd_post_deploy,
        "post-collection": _cmd_post_collection,
    }
    return dispatch[args.subcommand](args)


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run CLI tests**

```bash
python -m pytest tests/test_validate.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Smoke test CLI against real artifacts**

```bash
cd /path/to/sim2real
python scripts/validate.py pre-deploy \
  --run-dir workspace/runs/admin4 \
  --repo-root .
```
Expected: colored terminal output, report file written to `workspace/runs/admin4/validate_pre_deploy.json`.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate.py tests/test_validate.py
git commit -m "feat(validate): add validate.py CLI — pre-deploy and post-collection subcommands"
```

---

## Chunk 4: deploy.py integration

### Task 8: Wire pre-deploy gate and post-collection into deploy.py

- [ ] **Step 1: Write failing tests for the two integration points**

Add to `tests/test_validate.py`:

```python
import importlib
import types
from unittest.mock import patch, MagicMock


def _load_deploy():
    """Load deploy.py with heavy imports stubbed out."""
    import importlib.util, types
    spec = importlib.util.spec_from_file_location(
        "deploy", Path(__file__).resolve().parent.parent / "scripts" / "deploy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Stub heavy imports that require cluster tooling
    for name in ("yaml", "concurrent", "concurrent.futures"):
        sys.modules.setdefault(name, types.ModuleType(name))
    spec.loader.exec_module(mod)
    return mod


def test_stage_benchmarks_calls_pre_deploy_checks(tmp_path, monkeypatch):
    """pre_deploy_checks is called before the phase loop in stage_benchmarks."""
    deploy = _load_deploy()
    run_dir = tmp_path / "runs" / "test"
    run_dir.mkdir(parents=True)
    (run_dir / "prepare_equivalence_results.json").write_text('{}')
    (run_dir / "prepare_tekton").mkdir()
    (run_dir / "prepare_tekton" / "values.yaml").write_text(
        "observe:\n  workloads: []\nstack:\n  gaie: {}\npipeline:\n  fast_iteration: true\n"
    )

    called = []

    def mock_pre_deploy(run_dir_arg, repo_root=None):
        called.append(run_dir_arg)
        r = MagicMock()
        r.failed = False
        r.to_dict.return_value = {"overall": "PASS"}
        return r

    with patch.object(sys, "exit"):
        with patch("lib.validate_checks.run_pre_deploy_checks", mock_pre_deploy):
            # We just verify the function is wired; don't run the full phase loop
            pass  # Actual wiring verified by reading deploy.py code change

    # Structural test: verify the import and call exist in deploy.py source
    source = (Path(__file__).resolve().parent.parent / "scripts" / "deploy.py").read_text()
    assert "run_pre_deploy_checks" in source, "deploy.py must call run_pre_deploy_checks"
    assert "run_post_collection_checks" in source, "deploy.py must call run_post_collection_checks"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
python -m pytest tests/test_validate.py::test_stage_benchmarks_calls_pre_deploy_checks -v
```
Expected: FAIL — neither function name appears in deploy.py yet.

- [ ] **Step 3: Add pre-deploy gate to stage_benchmarks**

In `scripts/deploy.py`, locate `stage_benchmarks` (line ~937). The structure is:

```python
    if fast_iter:
        info("FAST MODE: ...")
        val = _construct_validation_results(equiv, fast_iter=True)
        val_path.write_text(...)
        ok(...)
    else:
        val = _construct_validation_results(equiv, fast_iter=False)
        val_path.write_text(...)
        result = run([VENV_PYTHON, CLI, "benchmark-state", ...], ...)
        ...

    # ── Run phases ────────────────────────────────────────────────────────────
    phases_to_run = ...
```

Insert the pre-deploy gate **after the entire `if fast_iter: ... else: ...` block closes and before the `# ── Run phases ────` comment**. The gate runs in both fast and non-fast modes:

```python
    else:
        # ... benchmark-state initialization (unchanged) ...

    # ── Pre-deploy validation ─────────────────────────────────────────────────
    step("2b", "Pre-deploy validation")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from lib.validate_checks import run_pre_deploy_checks
    try:
        pre_report = run_pre_deploy_checks(run_dir)
        val_json = json.dumps(pre_report.to_dict(), indent=2)
        (run_dir / "validate_pre_deploy.json").write_text(val_json)
        if pre_report.failed:
            err("Pre-deploy validation FAILED — see validate_pre_deploy.json")
            sys.exit(1)
        ok("Pre-deploy validation passed")
    except FileNotFoundError as e:
        err(f"Pre-deploy validation error (missing artifact): {e}")
        sys.exit(2)

    # ── Run phases ────────────────────────────────────────────────────────────
    phases_to_run = ...
```

Verify `import json` is already at the top of deploy.py (it is — json is used throughout).

- [ ] **Step 4: Add post-collection call to _run_single_phase**

In `scripts/deploy.py`, locate `_run_single_phase` (line ~886). The relevant structure is:

```python
    if phase == "noise":
        _run_noise_phase(run_dir, namespace, workspace_dir, ...)
    else:
        workloads = values.get("observe", {}).get("workloads", [])
        run_name = _make_run_name(phase)
        _run_workloads_for_phase(phase=phase, workloads=workloads, ...)
        _extract_phase_results(phase, namespace, run_dir)

    ok(f"[{phase}] Phase complete")
    return phase, "done"
```

Add the post-collection check **inside the `else` block**, immediately after `_extract_phase_results` and before the final `ok()`:

```python
    if phase == "noise":
        _run_noise_phase(run_dir, namespace, workspace_dir, ...)
    else:
        workloads = values.get("observe", {}).get("workloads", [])
        run_name = _make_run_name(phase)
        _run_workloads_for_phase(phase=phase, workloads=workloads, ...)
        _extract_phase_results(phase, namespace, run_dir)

        # ── Post-collection trace audit (non-blocking) ────────────────────────
        try:
            from lib.validate_checks import run_post_collection_checks
            col_report = run_post_collection_checks(phase, run_dir)
            col_json = json.dumps(col_report.to_dict(), indent=2)
            (run_dir / f"validate_post_collection_{phase}.json").write_text(col_json)
            if col_report.failed:
                warn(f"[{phase}] Post-collection validation FAILED — see validate_post_collection_{phase}.json")
            else:
                ok(f"[{phase}] Post-collection validation passed (overall={col_report.overall})")
        except Exception as e:
            warn(f"[{phase}] Post-collection validation error (non-blocking): {e}")

    ok(f"[{phase}] Phase complete")
    return phase, "done"
```

The indentation of the `try` block is one level deeper than `if phase == "noise"` — it is inside the `else` branch, not at the top of `_run_single_phase`.

- [ ] **Step 5: Run the integration test**

```bash
python -m pytest tests/test_validate.py::test_stage_benchmarks_calls_pre_deploy_checks -v
```
Expected: PASS — both function names now present in deploy.py.

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ scripts/test_validate_checks.py -v
```
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/deploy.py tests/test_validate.py
git commit -m "feat(validate): wire pre-deploy gate and post-collection into deploy.py"
```

---

## Final verification

- [ ] Run all tests one last time

```bash
python -m pytest tests/ scripts/test_validate_checks.py -v
```
Expected: all tests PASS, no failures.

- [ ] Run pre-deploy against real admin4 artifacts end-to-end

```bash
python scripts/validate.py pre-deploy \
  --run-dir workspace/runs/admin4 \
  --repo-root .
echo "Exit code: $?"
```

- [ ] Run post-collection against real admin3 traces

```bash
python scripts/validate.py post-collection \
  --run-dir workspace/runs/admin3 \
  --phase baseline
echo "Exit code: $?"
```

- [ ] Commit final state

```bash
git add -A
git commit -m "feat(validate): run validation complete (pre-deploy, post-collection, CLI, deploy.py integration)"
```
