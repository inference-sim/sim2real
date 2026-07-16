# Issue #574 — stream-metrics Prometheus scraper sidecar

> **Superseded by issue #579 / plan `2026-07-15-issue-579-adopt-collect-metrics.md`.**
> This plan is preserved as a historical record of the initial sim2real-owned
> sidecar design (issues #574 / #576, PRs #575 / #577). The current design
> wraps upstream `collect_metrics.sh` from llm-d-benchmark instead of
> maintaining our own scraper, and provisions the EPP-side auth RBAC at
> cluster bootstrap. See the #579 plan for the current shape.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `stream-metrics` Tekton streaming sidecar that scrapes the EPP and vLLM `/metrics` Prometheus endpoints during each workload iteration and persists the time-series into the cell bundle, giving offline analysis access to `WaitingQueueSize`, `KVCacheUsagePercent`, EPP `flow_control_pool_saturation`, and other engine/router signals that today can only be inferred from trace behaviour.

**Architecture:** A single Tekton Task, `stream-metrics`, discovers EPP and vLLM decode pods in the pipelinerun's namespace via label selectors (Option A from the issue), scrapes each pod's `/metrics` endpoint every 2 s using `wget`, appends one CSV row per sample × metric-line into `metrics/timeseries.csv` under the resultsDir, re-discovers targets every 30 s to survive pod restarts, and exits when `collect-results` writes the `metrics_stream_done` sentinel. Same lifecycle contract as `stream-epp-logs` and `stream-gpu-stats`. Failure isolation is per-target: a scrape failure logs a warning and continues.

**Tech Stack:** Tekton v1 Task, `alpine/kubectl:1.34.1` image (already used by the other streamers), pure POSIX shell + busybox `wget` + `awk` (no Python or Go binaries). Parent-repo integration: `pipeline/pipeline.yaml` wiring, `pipeline/deploy.py` collect logic, PyYAML-based tests.

## Global Constraints

- **Two-PR flow.** The new Task and the `collect-results` sentinel touch live in the `tektonc-data-collection` submodule (repo `inference-sim/tektonc-data-collection`). The parent-repo work (pipeline wiring, deploy.py collect, tests, docs, submodule pointer bump) lives in `inference-sim/sim2real`. Recent precedent: PR #538 in sim2real bumped the submodule after PR #57/#58 landed upstream. Order: submodule PR merges first, then sim2real PR bumps the pointer and closes #574.
- **RBAC:** no changes required. The pipeline's `helm-installer` ServiceAccount already carries `pods` `list` on the pipelinerun namespace via `tektonc-data-collection/tekton/roles-ns.yaml:54-56`, which is all Option A discovery needs.
- **Non-fatal streamer.** Task failures must not fail the parent PipelineRun. Match the `set +e` + `exit 0` pattern of the existing streamers.
- **Cell layout:** output goes to `${RESULTS_DIR}/metrics/` where `RESULTS_DIR = /workspace/data/$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)` (same shape as `epp_logs/` and `gpu_logs/`).
- **Sentinel:** `${RESULTS_DIR}/metrics_stream_done`. `collect-results` touches it alongside `epp_stream_done` and `gpu_stream_done`.
- **CI:** all changes must pass `ruff check pipeline/ --select F` and the pytest suite listed in `.github/workflows/test.yml`.

## Design decisions (revised after user feedback 2026-07-15)

1. **Output columns are `timestamp_us, target, metric_name, labels, value`** — timestamp naming matches `trace_data.csv`'s `send_time_us / arrival_time_us / first_chunk_time_us / last_chunk_time_us` convention. Values are microseconds since Unix epoch, computed as `$(( $(date +%s) * 1000000 ))`. Real precision is 1 s (matches scrape cadence), but the naming/unit is consistent so analysis code that already handles `_us` columns for the trace side works unchanged for metrics.
2. **`labels`, not `labels_json`.** The raw Prometheus label-string as it appears between `{...}` in the metric line (e.g., `le="0.005",method="POST"`), empty for unlabeled metrics. Shell/awk emitting valid JSON with correct `\"` / `\\` escaping is fragile; pandas can parse the raw form via `Series.str.extractall(r'(\w+)="([^"]*)"')` — the same parse pass Prometheus itself uses.
3. **Allowlist by default, configurable via task param `metricsAllowlist`.** Comma-separated list of metric-name prefixes; a line's `metric_name` passes iff it starts with any allowlist entry (so `vllm:request_queue_time_seconds` catches `_bucket`, `_sum`, `_count`). Default: curated set covering the SaturationDetector inputs plus the derived value, plus queue-time histogram, plus prefix-cache and preemption counters. Empty string = capture everything (research escape hatch). This is a **revision from the issue's "capture full response" recommendation** — the user preferred a small, curated starting set with a discovery aid (see next point).
4. **Per-target metric-name discovery log.** On the first successful scrape of each target, the task writes `metrics/<target>.discovered.txt` — the sorted-unique list of metric names the target exposed. Operators can consult this file (in the cell dir, no cluster access needed) to decide what to add to the allowlist next run. This addresses the user's "documentation on what metrics are available or how to find out" ask without shipping a static list that will drift.
5. **Pod-name discovery** uses the same label selectors as `stream-epp-logs.yaml` (`llm-d.ai/igw-mode=llm-d-router-gateway`, fallback to `inferencepool`) and `stream-gpu-stats.yaml` (`llm-d.ai/role=decode`). Rationale: those two tasks already run in the same Tekton lifecycle (same PipelineRun, same namespace) and are known to work; the smoketest's mode-keyed selector is a different codepath from a different lifecycle. If either selector proves wrong on a live deployment, updating them is a one-line change and matches how stream-epp-logs handles its own fallback.
6. **ServiceAccount:** unchanged — the PipelineRun runs under `helm-installer`, which already carries `pods` `list` verb in-namespace via `tektonc-data-collection/tekton/roles-ns.yaml:54-56`. No RBAC changes.
7. **Discovery re-run every 30 s, hardcoded** (not a task param). Matches `stream-gpu-stats.yaml`'s `RE_RESOLVE_EVERY=6` at INTERVAL=10s (once per minute) style; here 15 iterations at INTERVAL=2 s = once per 30 s.
8. **Ports:** default `eppPort=9090`, `vllmPort=8000` — both task params, overridable per deployment.

---

## Section A — `tektonc-data-collection` submodule PR

### Task A1: Create `stream-metrics.yaml`

**Files:**
- Create: `tektonc-data-collection/tekton/tasks/stream-metrics.yaml`

**Interfaces:**
- Consumes: `data-storage` workspace (data-pvc); params `namespace`, `resultsDir`, `intervalSeconds` (default 2), `eppPort` (default 9090), `vllmPort` (default 8000), `eppSelector` (default `llm-d.ai/igw-mode=llm-d-router-gateway`), `eppSelectorFallback` (default `inferencepool`), `vllmSelector` (default `llm-d.ai/role=decode`), `metricsAllowlist` (default is the curated list below; empty string = capture all).
- Produces:
  - `${RESULTS_DIR}/metrics/timeseries.csv` — header `timestamp_us,target,metric_name,labels,value`, one row per (scrape × metric-line) that passes the allowlist.
  - `${RESULTS_DIR}/metrics/<target>.discovered.txt` — sorted-unique list of every metric name each target exposed on its first successful scrape (written once per target per run, unfiltered by the allowlist).
  - `${RESULTS_DIR}/metrics/<target>.err` — per-target scrape-failure log (only if failures occur).
- Exit condition: `${RESULTS_DIR}/metrics_stream_done` sentinel appears, OR discovery finds zero EPP+vLLM pods after re-discovery.

**Default `metricsAllowlist`** (comma-separated, prefix-match; captures histogram suffixes `_bucket / _sum / _count`):
```
vllm:num_requests_waiting,vllm:num_requests_running,vllm:gpu_cache_usage_perc,vllm:num_preemptions_total,vllm:request_queue_time_seconds,vllm:iteration_tokens_total,vllm:gpu_prefix_cache_hits_total,vllm:gpu_prefix_cache_queries_total,flow_control_pool_saturation
```

- [ ] **Step 1: Write the Task file.**

  The file must define:
  - Task metadata (`name: stream-metrics`, description mirroring stream-epp-logs' preamble, including sentinel contract, output layout, and the allowlist + discovery-file convention).
  - Workspaces: `data` bound to the shared PVC.
  - Params exactly as listed under "Interfaces" above.
  - One `step` named `stream-metrics`, image `alpine/kubectl:1.34.1`, `securityContext.runAsUser: 0`, shell script implementing:

    - **Phase 0 — Validation.** Reject non-integer `intervalSeconds` and out-of-range (`1..60`) with `exit 0` (non-fatal). Match the pattern in `stream-epp-logs.yaml:42-45`.

    - **Phase 1 — Target discovery.** Loop up to 20 × 3 s waiting for pods. Each iteration:
      - Try the EPP primary selector (`${EPP_SELECTOR}`); if empty, try the fallback (`${EPP_SELECTOR_FALLBACK}`). Store the first-hit selector so re-discovery uses the same one, mirroring `stream-epp-logs.yaml:60-72`.
      - Query the vLLM selector.
      - Extract `NAME` and `PODIP` (from `.status.podIP`) via `-o custom-columns=NAME:.metadata.name,PODIP:.status.podIP`.
      - Build target tuples `"<kind>:<pod>|<ip>|<port>"` (kind is `epp` or `vllm-decode`). Skip pods whose `PODIP` is `<none>` — they're not routable yet.
      - Break as soon as at least one target exists.
      - After 60 s with zero targets, log a warning and `exit 0` (non-fatal).

    - **Phase 2 — Sentinel wipe + output prep.**
      - `rm -f "${SENTINEL}"`.
      - `mkdir -p "${METRICS_DIR}"`.
      - Overwrite the CSV header: `echo 'timestamp_us,target,metric_name,labels,value' > "${METRICS_DIR}/timeseries.csv"`. Overwrite is fine — if the task is re-attempted for the same iteration, we want a clean file.

    - **Phase 3 — Scrape loop.** Track a `DISCOVERED_${target}=1` flag per target in a state file / marker file per target. On each iteration:
      - `ts=$(( $(date -u +%s) * 1000000 ))` — integer microseconds since epoch.
      - For each target `"<name>|<ip>|<port>"`:
        - Fetch with `wget -q -T 5 -O - "http://${ip}:${port}/metrics"`. Capture stdout to a temp file.
        - If the fetch returns non-zero or the temp file is empty:
          - Append a one-line failure record (`EXIT=$rc TS=${ts}`) to `${METRICS_DIR}/${name}.err`. Continue to next target.
        - If discovery-marker for this target doesn't exist yet (`${METRICS_DIR}/.disc-${name}` sentinel):
          - Extract sorted-unique metric names from the temp file: `grep -v '^#' | awk 'NF>0 {sub(/\{.*/, "", $1); print $1}' | sort -u > "${METRICS_DIR}/${name}.discovered.txt"`.
          - `touch "${METRICS_DIR}/.disc-${name}"`.
        - Pipe the temp file into an awk parser (allowlist filter + CSV emit).
      - Every `${RE_RESOLVE_EVERY}=15` iterations (30 s at INTERVAL=2 s), re-run Phase 1's discovery block. If it yields zero targets, `break`. If it yields a different set, replace `TARGETS`. Discovery markers for gone pods stay on disk (harmless); newly discovered pods get their own discovery file on first scrape.
      - Sentinel poll: sleep in 1-second slices totaling `${INTERVAL}` between batches, checking the sentinel each slice for prompt exit.

    - **Phase 4 — Cleanup.** Log completion, `exit 0`.

  **Awk parser (allowlist + CSV emit).** Prometheus text format lines:
  ```
  # HELP metric_name Description
  # TYPE metric_name gauge
  metric_name{label1="v1",label2="v2"} 3.14
  metric_name_bucket{le="0.005"} 42
  metric_name_sum 12.3
  metric_name_count 100
  unlabeled_metric 7
  ```
  Regex: `/^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})?[ \t]+([^ \t\n]+)/`. Prometheus label values must not contain unescaped `}` so `[^}]*` is safe.

  Parser body (encoded into the shell script — this is the exact awk):
  ```awk
  BEGIN {
    n = split(ALLOW, prefixes, ",")
    for (i = 1; i <= n; i++) prefixes[i] = prefixes[i]
  }
  /^#/ || NF == 0 { next }
  {
    line = $0
    # split into name, labels, value
    if (match(line, /^[a-zA-Z_:][a-zA-Z0-9_:]*/)) {
      name = substr(line, RSTART, RLENGTH)
      rest = substr(line, RLENGTH + 1)
    } else { next }
    labels = ""
    if (substr(rest, 1, 1) == "{") {
      close_at = index(rest, "}")
      if (close_at == 0) next
      labels = substr(rest, 2, close_at - 2)
      rest = substr(rest, close_at + 1)
    }
    # remaining rest is " <value>[ <timestamp>]"
    sub(/^[ \t]+/, "", rest)
    split(rest, parts, /[ \t]+/)
    value = parts[1]
    if (value == "") next
    # allowlist: prefix match; empty allowlist means all
    if (n == 0 || (n == 1 && prefixes[1] == "")) { pass = 1 }
    else {
      pass = 0
      for (i = 1; i <= n; i++) {
        p = prefixes[i]
        if (p != "" && substr(name, 1, length(p)) == p) { pass = 1; break }
      }
    }
    if (!pass) next
    # RFC-4180 escape for labels: double each embedded "
    gsub(/"/, "\"\"", labels)
    printf "%s,%s,%s,\"%s\",%s\n", TS, TARGET, name, labels, value
  }
  ```
  The shell passes `TS`, `TARGET`, and `ALLOW` via `awk -v`. Output is redirected `>> timeseries.csv`.

  Length target: ~200 lines, matching `stream-epp-logs.yaml` in style. Include a top-of-file comment block explaining the label-selector strategy, allowlist + discovery-file design, output format, and sentinel contract. Include an ASSUMPTIONS block covering: `helm-installer` SA has pods list; EPP + vLLM Deployments expose `/metrics` on the ports named by params; label conventions from llm-d-modelservice + llm-d-router chart.

- [ ] **Step 2: Manual smoke via YAML parse.**

  Run: `python -c "import yaml, sys; yaml.safe_load(open('tektonc-data-collection/tekton/tasks/stream-metrics.yaml'))"` from the worktree root.
  Expected: no output (parses clean).

- [ ] **Step 3: Commit inside the submodule.**

  ```bash
  cd tektonc-data-collection
  git checkout -b feat/stream-metrics-sidecar
  git add tekton/tasks/stream-metrics.yaml
  git commit -m "feat(stream-metrics): scrape EPP + vLLM /metrics into cell dir"
  cd -
  ```

### Task A2: Extend `collect-results` to write the third sentinel

**Files:**
- Modify: `tektonc-data-collection/tekton/tasks/collect-results.yaml:70-71` (wipe block) and `:108-109` (touch block).

**Interfaces:**
- Consumes: nothing new (same params, same script).
- Produces: `${RESULTS_DIR}/metrics_stream_done` alongside `epp_stream_done` and `gpu_stream_done`.

- [ ] **Step 1: Edit the wipe block.**

  Before:
  ```yaml
          rm -f "${RESULTS_DIR}/epp_stream_done"
          rm -f "${RESULTS_DIR}/gpu_stream_done"
  ```
  After:
  ```yaml
          rm -f "${RESULTS_DIR}/epp_stream_done"
          rm -f "${RESULTS_DIR}/gpu_stream_done"
          rm -f "${RESULTS_DIR}/metrics_stream_done"
  ```

- [ ] **Step 2: Edit the touch block.**

  Before:
  ```yaml
          touch "${RESULTS_DIR}/epp_stream_done"
          touch "${RESULTS_DIR}/gpu_stream_done"
  ```
  After:
  ```yaml
          touch "${RESULTS_DIR}/epp_stream_done"
          touch "${RESULTS_DIR}/gpu_stream_done"
          touch "${RESULTS_DIR}/metrics_stream_done"
  ```

- [ ] **Step 3: Update the task description.**

  Change line 12–14's `epp_stream_done and gpu_stream_done sentinels` to `epp_stream_done, gpu_stream_done, and metrics_stream_done sentinels`, and update the parenthetical list of streamers to include `stream-metrics`.

- [ ] **Step 4: Commit inside the submodule.**

  ```bash
  cd tektonc-data-collection
  git add tekton/tasks/collect-results.yaml
  git commit -m "feat(collect-results): touch metrics_stream_done alongside existing sentinels"
  cd -
  ```

### Task A3: Push the submodule branch and open the upstream PR

- [ ] **Step 1: Push.**

  ```bash
  cd tektonc-data-collection
  git push -u origin feat/stream-metrics-sidecar
  cd -
  ```

- [ ] **Step 2: Open PR against `inference-sim/tektonc-data-collection`.**

  ```bash
  cd tektonc-data-collection
  gh pr create --repo inference-sim/tektonc-data-collection \
    --base main \
    --title "feat(stream-metrics): scrape EPP + vLLM /metrics into cell dir" \
    --body-file /tmp/upstream-pr-body.md
  cd -
  ```

  PR body must:
  - Note that this supports sim2real #574.
  - Describe the new Task, its params, its output format.
  - Explain the label-selector strategy (llm-d.ai/igw-mode + llm-d.ai/role).
  - State the sentinel contract change in collect-results.
  - Explain the deviations from the sim2real issue's proposal (output columns, discovery cadence hardcoded).

- [ ] **Step 3: Wait for upstream merge.**

  This is a manual gate. The parent-repo PR (Section B) cannot land until the submodule PR merges, because the submodule bump would otherwise point at a non-existent SHA on `origin/main`.

---

## Section B — `sim2real` parent PR

Everything from here on is in the sim2real worktree (`.claude/worktrees/issue-574-stream-metrics`).

### Task B1: Bump the submodule pointer

**Files:**
- Modify: `tektonc-data-collection` submodule gitlink (the recorded SHA in `.gitmodules`-adjacent state, updated via `git add tektonc-data-collection`).

**Interfaces:**
- Consumes: the merged upstream commit SHA on `tektonc-data-collection/main`.
- Produces: recorded pointer moves to that SHA. `stream-metrics` Task and updated `collect-results` Task become available to `cluster.py provision` via `apply_cluster_resources`.

- [ ] **Step 1: Fetch upstream main into the submodule and detach at the merged commit.**

  ```bash
  cd tektonc-data-collection
  git fetch origin
  git checkout origin/main
  cd -
  ```

- [ ] **Step 2: Verify the new files are present.**

  ```bash
  test -f tektonc-data-collection/tekton/tasks/stream-metrics.yaml
  grep -q metrics_stream_done tektonc-data-collection/tekton/tasks/collect-results.yaml
  ```
  Expected: both succeed.

- [ ] **Step 3: Stage the submodule bump.**

  ```bash
  git add tektonc-data-collection
  git status --short
  ```
  Expected: one entry, `M tektonc-data-collection`.

  (Commit happens at the end of Section B once all sibling changes are in.)

### Task B2: Wire `stream-metrics` into `pipeline/pipeline.yaml`

**Files:**
- Modify: `pipeline/pipeline.yaml` — insert a new task entry alongside `stream-epp-logs` (`:114-125`) and `stream-gpu-stats` (`:127-138`).

**Interfaces:**
- Consumes: `namespace`, `runName`, `phase`, `workloadName`, `replica` (already-declared pipeline params).
- Produces: an additional task in the DAG that starts alongside the other streamers and shares their `resultsDir`.

- [ ] **Step 1: Write the failing test first.**

  Add a new test class to `pipeline/tests/test_pipeline_yaml.py` at the bottom of the file, next to `TestStreamGpuStatsTask`:

  ```python
  class TestStreamMetricsTask:
      """stream-metrics task invocation in pipeline.yaml."""

      def _get_stream_metrics_task(self):
          pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
          tasks = pipeline["spec"]["tasks"]
          s = [t for t in tasks if t["name"] == "stream-metrics"]
          assert len(s) == 1, "Expected exactly one stream-metrics task"
          return s[0]

      def test_task_present(self):
          self._get_stream_metrics_task()

      def test_task_ref(self):
          task = self._get_stream_metrics_task()
          assert task["taskRef"]["name"] == "stream-metrics"

      def test_runs_after_standup_and_prepare_results_dir(self):
          task = self._get_stream_metrics_task()
          run_after = task["runAfter"]
          assert "llmdbenchmark-standup" in run_after
          assert "prepare-results-dir" in run_after
          assert "run-workload-blis-observe-binary" not in run_after

      def test_data_workspace_bound(self):
          task = self._get_stream_metrics_task()
          ws = {w["name"]: w["workspace"] for w in task["workspaces"]}
          assert ws.get("data") == "data-storage"

      def test_has_namespace_and_results_dir(self):
          task = self._get_stream_metrics_task()
          names = [p["name"] for p in task["params"]]
          assert "namespace" in names
          assert "resultsDir" in names

      def test_results_dir_matches_workload(self):
          pipeline = yaml.safe_load(PIPELINE_YAML.read_text())
          tasks = pipeline["spec"]["tasks"]
          s = next(t for t in tasks if t["name"] == "stream-metrics")
          w = next(t for t in tasks if t["name"] == "run-workload-blis-observe-binary")
          s_rd = next(p["value"] for p in s["params"] if p["name"] == "resultsDir")
          w_rd = next(p["value"] for p in w["params"] if p["name"] == "resultsDir")
          assert s_rd == w_rd
  ```

- [ ] **Step 2: Run tests to verify they fail.**

  Run: `python -m pytest pipeline/tests/test_pipeline_yaml.py::TestStreamMetricsTask -v`
  Expected: all six tests FAIL with "Expected exactly one stream-metrics task".

- [ ] **Step 3: Add the task entry to `pipeline/pipeline.yaml`.**

  Insert after the `stream-gpu-stats` block (currently ending at `:138`), before `run-workload-blis-observe-binary`:

  ```yaml
      - name: stream-metrics
        runAfter: ["llmdbenchmark-standup", "prepare-results-dir"]
        taskRef:
          name: stream-metrics
        workspaces:
          - name: data
            workspace: data-storage
        params:
          - name: namespace
            value: "$(params.namespace)"
          - name: resultsDir
            value: "$(params.runName)/$(params.phase)/$(params.workloadName)/i$(params.replica)"
  ```

- [ ] **Step 4: Run tests to verify they pass.**

  Run: `python -m pytest pipeline/tests/test_pipeline_yaml.py::TestStreamMetricsTask -v`
  Expected: all six PASS.

- [ ] **Step 5: Run the whole test_pipeline_yaml.py suite.**

  Run: `python -m pytest pipeline/tests/test_pipeline_yaml.py -v`
  Expected: no regressions. Existing `TestStreamEppLogsTask` / `TestStreamGpuStatsTask` still pass.

### Task B3: Teach `deploy.py collect` about `metrics/` + sentinel

**Files:**
- Modify: `pipeline/deploy.py:1461` (add `"metrics"` to the wipe-stale-subdirs tuple), `:1471-1472` (add `"metrics_stream_done"` to the sentinel copy list), `:1494-1505` (add a `metrics/` directory copy stanza after `gpu_logs/`).

**Interfaces:**
- Consumes: same PVC layout as today.
- Produces: `${cell_dir}/metrics/` + `${cell_dir}/metrics_stream_done` in the local results tree after `deploy.py collect`.

- [ ] **Step 1: Write the failing test.**

  Extend `pipeline/tests/test_deploy_collect.py::test_collect_skip_logs_invokes_gpu_logs_copy` with metric-specific assertions. Add a new sibling test:

  ```python
  def test_collect_skip_logs_invokes_metrics_copy(tmp_path, monkeypatch):
      """--skip-logs path issues a kubectl cp for metrics/ alongside epp_logs/ and gpu_logs/,
      scoped to each iteration subdirectory (post step-5 layout)."""
      from pipeline import deploy
      import subprocess

      run_dir = tmp_path / "workspace" / "runs" / "test-run"
      (run_dir / "cluster").mkdir(parents=True)

      data = {
          "wl-smoke-baseline": {"workload": "smoke", "package": "baseline",
                                "status": "done", "completed_namespace": "ns-0"},
      }
      _mock_cm(monkeypatch, data)

      cp_targets = []
      def mock_run(cmd, **kwargs):
          mock = MagicMock(returncode=0, stdout="", stderr="")
          cmd_list = cmd if isinstance(cmd, list) else cmd.split()
          cmd_str = " ".join(cmd_list)
          if "exec" in cmd_str and "ls " in cmd_str and "/wl-smoke/" in cmd_str:
              mock.stdout = "i1"
          elif "exec" in cmd_str and "ls" in cmd_str:
              mock.stdout = "wl-smoke"
          if "exec" in cmd_str and "stat" in cmd_str:
              mock.stdout = ""
          if "cp" in cmd_list and len(cmd_list) >= 4:
              cp_targets.append(cmd_list[2])
          return mock

      monkeypatch.setattr(subprocess, "run", mock_run)

      deploy._extract_phases_from_pvc(
          ["baseline"], "test-run", "ns-0", run_dir, skip_logs=True)

      sources = " ".join(cp_targets)
      assert "/wl-smoke/i1/metrics/" in sources, (
          f"no i1/metrics/ copy issued; saw: {cp_targets}")
      assert "/wl-smoke/i1/metrics_stream_done" in sources, (
          f"no i1/metrics_stream_done sentinel copy; saw: {cp_targets}")
  ```

  Also extend the `--skip-logs stale-wipe` test (`test_collect_skip_logs_wipes_stale`, at `:1736`) to seed a stale `metrics/` dir and assert it's wiped:

  ```python
  # inside test_collect_skip_logs_wipes_stale, after stale_gpu = ...:
  stale_metrics = iter_dir / "metrics" / "stale.csv"
  stale_metrics.parent.mkdir(parents=True, exist_ok=True)
  stale_metrics.write_text("stale")
  # ...
  # after existing assertions:
  assert not stale_metrics.exists()
  ```

- [ ] **Step 2: Run tests to verify they fail.**

  Run: `python -m pytest pipeline/tests/test_deploy_collect.py::test_collect_skip_logs_invokes_metrics_copy pipeline/tests/test_deploy_collect.py::test_collect_skip_logs_wipes_stale -v`
  Expected: both FAIL — `test_collect_skip_logs_invokes_metrics_copy` fails on the `/wl-smoke/i1/metrics/` assertion; the stale-wipe test fails on `not stale_metrics.exists()` since the code doesn't wipe `metrics/` yet.

- [ ] **Step 3: Update `pipeline/deploy.py`.**

  Edit `pipeline/deploy.py:1461` — add `"metrics"` to the stale-subdir wipe tuple:

  Before:
  ```python
                          for sub in ("server_logs", "epp_logs", "gpu_logs", "resources"):
  ```
  After:
  ```python
                          for sub in ("server_logs", "epp_logs", "gpu_logs", "metrics", "resources"):
  ```

  Edit `pipeline/deploy.py:1471-1472` — add the new sentinel to the copy list:

  Before:
  ```python
                          for fname in ("trace_data.csv", "trace_header.yaml",
                                        "epp_stream_done", "gpu_stream_done"):
  ```
  After:
  ```python
                          for fname in ("trace_data.csv", "trace_header.yaml",
                                        "epp_stream_done", "gpu_stream_done",
                                        "metrics_stream_done"):
  ```

  Insert a `metrics/` copy stanza after the `gpu_logs` block (currently ending at `:1505`), mirroring `epp_logs`/`gpu_logs`:

  ```python
                          # Copy metrics directory
                          metrics_dest = iN_dest / "metrics"
                          metrics_dest.mkdir(exist_ok=True)
                          r = run(
                              ["kubectl", "cp", f"{remote_prefix}/metrics/",
                               str(metrics_dest), "--retries=3"],
                              check=False, capture=True,
                          )
                          if r.returncode != 0 and "no such file" not in r.stderr.lower():
                              wl_errors.append(
                                  f"{wl_name}/{iN}/metrics: {r.stderr.strip()}"
                              )
  ```

- [ ] **Step 4: Run tests to verify they pass.**

  Run: `python -m pytest pipeline/tests/test_deploy_collect.py -v -k "metrics or skip_logs"`
  Expected: new + updated tests PASS. No regressions in sibling tests.

### Task B4: Update `pipeline/README.md` — workspace artifact section + metric-discovery how-to

**Files:**
- Modify: `pipeline/README.md` — the workspace artifact table (search for `runs/<run>/results/`) AND a new subsection documenting how operators discover available metrics.

**Interfaces:** none (docs only).

- [ ] **Step 1: Locate the workspace-artifact table.**

  Run: `grep -n 'runs/<run>/results' pipeline/README.md`
  Expected: at least one hit inside the workspace artifact table.

- [ ] **Step 2: Add rows for `metrics/timeseries.csv` and `metrics/<target>.discovered.txt`.**

  Add immediately below the existing `results/{phase}/<workload>/i<N>/gpu_logs/<node>.log` row:

  ```
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/timeseries.csv` | `deploy.py collect` (pulled from PVC) | analysis — EPP + vLLM `/metrics` time-series, columns `timestamp_us,target,metric_name,labels,value` (filtered by the allowlist) |
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/<target>.discovered.txt` | `deploy.py collect` (pulled from PVC) | ops/analysis — sorted-unique list of every metric name each target exposed on first scrape; consult before widening the allowlist |
  ```

- [ ] **Step 3: Add a "Metric capture" subsection.**

  After the workspace-artifact table (or in the section that describes the streaming sidecars — search `stream-epp-logs`), add a subsection like:

  ```markdown
  ### Metric capture (`stream-metrics`)

  The `stream-metrics` sidecar scrapes the EPP and each vLLM decode pod's Prometheus
  `/metrics` endpoint every `intervalSeconds` seconds (default 2 s) and appends one row
  per (scrape × metric-line) into `metrics/timeseries.csv`. The default `metricsAllowlist`
  covers the SaturationDetector inputs (`vllm:num_requests_waiting`,
  `vllm:gpu_cache_usage_perc`) plus the derived value the flow-controller consumes
  (`flow_control_pool_saturation`) plus queue-time and preemption counters — see the
  Task definition in `tektonc-data-collection/tekton/tasks/stream-metrics.yaml` for the
  exact defaults.

  **Discovering what metrics are available.** On the first successful scrape of each
  target, the task writes `metrics/<target>.discovered.txt` in the cell — a sorted,
  unique list of every metric name that target exposed, regardless of allowlist. Read
  this file after any run to decide what to add to the allowlist next time:

      cat workspace/runs/<run>/results/<phase>/<workload>/i<N>/metrics/epp:<pod>.discovered.txt

  **Widening the allowlist.** The allowlist is prefix-match, so
  `vllm:request_queue_time_seconds` captures the histogram's `_bucket`, `_sum`, and
  `_count` variants automatically. Empty string in `metricsAllowlist` captures every
  metric — use for exploratory runs, expect ~500 metrics per target per scrape.

  **Live inspection.** For ad-hoc exploration outside a run, port-forward the target
  pod:

      kubectl port-forward -n <namespace> <epp-or-vllm-pod> 9090:9090
      curl -s http://localhost:9090/metrics | grep -v '^#' | awk '{sub(/\{.*/,""); print $1}' | sort -u
  ```

- [ ] **Step 4: Verify the additions.**

  Run: `grep -c 'metrics/timeseries.csv\|discovered.txt\|stream-metrics' pipeline/README.md`
  Expected: at least 5 hits.

### Task B5: Update `CLAUDE.md` — workspace artifact table + streamer list

**Files:**
- Modify: `CLAUDE.md` — workspace artifact table (search for `epp_stream_done` / `gpu_stream_done`).

**Interfaces:** none (docs only).

- [ ] **Step 1: Locate the workspace-artifact table entries for the existing sentinels.**

  Run: `grep -n 'epp_stream_done\|gpu_stream_done\|epp_logs\|gpu_logs' CLAUDE.md`

- [ ] **Step 2: Add a `metrics/` row and mention `stream-metrics` where the existing streamers are listed.**

  Add the row `| runs/<run>/results/{phase}/<workload>/i<N>/metrics/timeseries.csv | deploy.py collect (pulled from PVC) | analysis / debugging |` to the table.

  Update any prose that lists the streamers (search for `stream-epp-logs` and `stream-gpu-stats`) to also mention `stream-metrics` where relevant.

### Task B6: Sweep for stale references

- [ ] **Step 1: Grep for the names of things that changed.**

  From the worktree root:
  ```bash
  grep -rn --include='*.md' --include='*.py' --include='*.yaml' \
    -e 'epp_stream_done' -e 'gpu_stream_done' -e 'metrics_stream_done' \
    -e 'stream-epp-logs' -e 'stream-gpu-stats' -e 'stream-metrics' \
    -e 'epp_logs/' -e 'gpu_logs/' -e 'metrics/timeseries' \
    .claude/ pipeline/ docs/ CLAUDE.md
  ```

- [ ] **Step 2: For each hit, decide stale vs still-accurate.**

  - `pipeline/README.md` — updated above.
  - `CLAUDE.md` — updated above.
  - `pipeline/deploy.py` — updated above.
  - Skill docs under `.claude/skills/sim2real-analyze/` may reference the artifact table; update if so.
  - Test files — updated above.

  Record decisions inline (in a scratch note or the PR body).

### Task B7: Run the full CI suite locally

- [ ] **Step 1: Lint.**

  Run: `ruff check pipeline/ .claude/skills/ --select F`
  Expected: no errors.

- [ ] **Step 2: Full pytest run (matches `.github/workflows/test.yml`).**

  Run:
  ```bash
  python -m pytest pipeline/ \
    pipeline/tests/test_layout.py \
    pipeline/tests/test_cluster_ops.py \
    pipeline/tests/test_cluster_py.py \
    pipeline/tests/test_slicer.py \
    pipeline/tests/test_sim2real.py \
    pipeline/tests/test_assemble_run.py \
    pipeline/tests/test_translation_ref.py \
    pipeline/tests/test_translate.py \
    pipeline/tests/test_build.py \
    pipeline/tests/test_pairkey.py \
    pipeline/tests/test_load_pairs.py \
    .claude/skills/sim2real-analyze/tests/ \
    .claude/skills/sim2real-bootstrap/tests/ \
    .claude/skills/sim2real-translate/tests/ \
    .claude/skills/sim2real-check/tests/ \
    -v
  ```
  Expected: green. Fix any regressions inline before proceeding.

### Task B8: Commit, push, open PR

- [ ] **Step 1: Verify path discipline.**

  Run: `pwd` and confirm you're inside `.claude/worktrees/issue-574-stream-metrics/`.
  Run: `git -C ../../.. status` (parent repo root) and confirm zero changes leaked out.

- [ ] **Step 2: Stage all changes.**

  ```bash
  git add tektonc-data-collection \
          pipeline/pipeline.yaml \
          pipeline/deploy.py \
          pipeline/tests/test_pipeline_yaml.py \
          pipeline/tests/test_deploy_collect.py \
          pipeline/README.md \
          CLAUDE.md \
          docs/superpowers/plans/2026-07-15-issue-574-stream-metrics.md
  ```

- [ ] **Step 3: Commit with a descriptive message.**

  ```bash
  git commit -m "$(cat <<'EOF'
  feat(pipeline): wire stream-metrics sidecar (Prometheus scraper) and collect metrics/ from PVC

  Closes #574.

  Adds a third streaming sidecar next to stream-epp-logs and stream-gpu-stats
  that scrapes EPP + vLLM /metrics on a 2 s cadence and writes one CSV per
  cell (metrics/timeseries.csv), giving offline analysis access to
  WaitingQueueSize, KVCacheUsagePercent, flow_control_pool_saturation, and
  the rest of the Prometheus surface without needing cluster reachability.

  Companion PR: inference-sim/tektonc-data-collection#<N> — must merge first.
  This bumps the submodule pointer to include the new Task and the third
  sentinel in collect-results.
  EOF
  )"
  ```

- [ ] **Step 4: Push.**

  ```bash
  git push -u origin worktree-issue-574-stream-metrics
  ```

- [ ] **Step 5: Open the PR.**

  ```bash
  gh pr create --title "feat(pipeline): stream-metrics sidecar for EPP + vLLM /metrics — closes #574" \
    --body-file /tmp/parent-pr-body.md
  ```

  PR body must:
  - `Closes #574`.
  - Link the upstream companion PR and note "must merge first".
  - Summarize: what the sidecar does, why (SaturationDetector inputs currently uninferable from traces), where the output lands.
  - Call out the four deviations from the issue's proposal (see "Deviations" section of this plan) so reviewers see them without hunting.
  - Note the stale-reference sweep results.

  If `gh` fails with `Resource not accessible by personal access token`, retry with `unset GITHUB_TOKEN GH_TOKEN` prefix.

## Acceptance Criteria (derived from the issue)

1. A new Tekton `Task` named `stream-metrics` exists in `tektonc-data-collection/tekton/tasks/`.
2. The task starts in parallel with `run-workload-blis-observe-binary`, after `llmdbenchmark-standup` and `prepare-results-dir`.
3. The task discovers EPP and vLLM decode pods via label selectors (`llm-d.ai/igw-mode=llm-d-router-gateway` and `llm-d.ai/role=decode`) and scrapes `/metrics` on each.
4. Output lands in `${RESULTS_DIR}/metrics/timeseries.csv` with rows `timestamp,target,metric_name,labels,value`.
5. `collect-results` writes `${RESULTS_DIR}/metrics_stream_done` alongside the existing two sentinels.
6. The task exits when the sentinel appears; failures are non-fatal.
7. `deploy.py collect` pulls `metrics/` and `metrics_stream_done` from the PVC into the local cell dir.
8. `pipeline/pipeline.yaml` and `deploy.py` have unit-test coverage of the wiring.
9. `pipeline/README.md` and `CLAUDE.md` list the new artifact.
