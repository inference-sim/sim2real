"""Validation check primitives for sim2real run verification."""

from __future__ import annotations
import csv
import json
import math
import re
import requests
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
    passed_replicas = str(replicas) == str(exp_replicas)
    items.append(CheckItem(
        "decode.replicas",
        passed_replicas,
        [f"expected={exp_replicas}, actual={replicas}"] if not passed_replicas else [],
    ))

    # tensor parallelism
    tensor = decode.get("parallelism", {}).get("tensor")
    exp_tensor = llm_config.get("serving", {}).get("tensor_parallelism")
    passed_tensor = str(tensor) == str(exp_tensor)
    items.append(CheckItem(
        "decode.parallelism.tensor",
        passed_tensor,
        [f"expected={exp_tensor}, actual={tensor}"] if not passed_tensor else [],
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
            except (KeyError, ValueError):
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


# ── Phase 2: Live cluster checks ─────────────────────────────────────────────


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
