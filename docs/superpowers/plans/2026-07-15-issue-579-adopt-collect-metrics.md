# Issue #579 — Adopt `collect_metrics.sh`, provision EPP metrics RBAC at bootstrap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the sim2real-owned `stream-metrics` sidecar shell script with a thin wrapper around `llm-d-benchmark/workload/harnesses/collect_metrics.sh`, and provision the EPP-side auth RBAC ourselves at cluster bootstrap so the wrapper can present a bearer token that EPP will validate.

**Architecture:** Cluster-side RBAC (ClusterRole + per-namespace ClusterRoleBinding) is added to the submodule's existing `roles-*.yaml` bundles, applied by `cluster_ops.provision_namespace`'s existing `_step_rbac`. A per-namespace `type: kubernetes.io/service-account-token` Secret is also added to `roles-ns.yaml`, giving `collect_metrics.sh` a bearer token to send to EPP. `stream-metrics.yaml` becomes a shell wrapper that curls `collect_metrics.sh` + `process_metrics.py` from a pinned llm-d-benchmark commit, runs `start`, polls the sentinel, then runs `stop` + `process`.

**Tech stack:** Bash, `kubectl`, `curl`, `python3` — installed into the existing `alpine/kubectl:1.34.1` image via `apk add` at task start. No new images. No changes to `llmdbenchmark-standup`. No cluster-Prometheus ServiceMonitor.

## Global constraints

- **Two-PR flow again** (submodule PR merges first, parent PR bumps + wires downstream). Same pattern as #575 and #577.
- **`llmdbenchmark-standup` is unchanged.** It keeps stripping `router.monitoring.prometheus.enabled` under `--non-admin`; we don't fight that.
- **No cluster-scoped RBAC creation happens at pipeline time.** ClusterRole + ClusterRoleBinding are created at `cluster.py provision` / `slot add` (the admin bootstrap moment).
- **Existing pipeline SA is unchanged** — `helm-installer` remains the PipelineRun SA. It becomes authorized on `/metrics` via the new group-subject binding.
- **CI must pass** — `ruff check pipeline/ --select F` and the pytest suite in `.github/workflows/test.yml`.

## Design decisions (locked)

1. **collect_metrics.sh install strategy: curl-pin from raw.githubusercontent.com** at a task-param SHA (default = the currently-vendored llm-d-benchmark submodule SHA). Simplest option — zero moving parts, matches the existing `install-inference-perf.yaml` pattern that already does `wget https://raw.githubusercontent.com/llm-d/llm-d-benchmark/...`. Rejected: workspace-mount (adds a workspace binding to stream-metrics not present today), image-bake (requires publishing a custom image).
2. **Image: `python:3.12-slim-bookworm`.** Same image `install-inference-perf.yaml` and `llmdbenchmark-standup.yaml` already use for tasks that mix python + shell. Base ships bash, python3, apt-get. Add `curl` (via apt) + a pinned `kubectl` binary at task start (matches the `1.34.1` version the sibling streamers use via `alpine/kubectl:1.34.1`). Two package installs vs. three on alpine; the sibling streamers keep alpine because they're pure shell + awk and don't need python — our task does.
3. **Sentinel-exit contract preserved.** Wrapper starts `collect_metrics.sh start` in background, polls the `metrics_stream_done` sentinel, calls `stop` + `process` on sentinel fire. Preserves parallel-with-workload lifecycle; no change to `collect-results.yaml`.
4. **Bearer identity: `helm-installer` SA token via type-annotated Secret.** The Secret we create annotates `kubernetes.io/service-account.name: helm-installer`; kubelet auto-populates `data.token`. `collect_metrics.sh` reads it verbatim — no upstream code changes.
5. **RBAC group subject: `system:serviceaccounts:${NAMESPACE}`.** Blast radius = one namespace. Covers both `helm-installer` (the pipeline SA) and the chart-derived EPP SA (whatever its name turns out to be per release). Simpler than chasing per-release SA names.
6. **Task params trimmed.** Drop `eppPort`, `vllmPort`, `eppSelector`, `eppSelectorFallback`, `vllmSelector`, `metricsAllowlist` — all handled internally by `collect_metrics.sh`'s env vars. Keep `namespace`, `resultsDir`, `intervalSeconds` (default `"15"` matching collect_metrics.sh). Add `harnessSha`.

---

## Section A — `tektonc-data-collection` submodule PR

### Task A1: Add `sim2real-metrics-reader` ClusterRole + per-namespace ClusterRoleBinding to `roles-cluster.yaml`

**Files:**
- Modify: `tektonc-data-collection/tekton/roles-cluster.yaml`

**Interfaces:**
- Consumes: `${NAMESPACE}` from `_envsubst` (already threaded by `_step_rbac`).
- Produces: cluster-scoped `ClusterRole/sim2real-metrics-reader` (name is constant — creating from N per-namespace applies is idempotent under `kubectl apply`); per-namespace `ClusterRoleBinding/sim2real-metrics-reader-${NAMESPACE}` with `Group: system:serviceaccounts:${NAMESPACE}` subject.

- [ ] **Step 1: Append the ClusterRole + ClusterRoleBinding to the file.**

  At the end of `roles-cluster.yaml` (below existing `helm-installer-clusterrole` and its bindings):

  ```yaml
  ---
  # ClusterRole: grants the RBAC verbs the llm-d-router chart's monitoring
  # subchart would emit (see _rbac.yaml in the routerlib chart). Bundled
  # here so sim2real's cluster-admin bootstrap owns this, allowing pipeline
  # runs to stay non-admin while still having a working /metrics auth path
  # against EPP.
  #
  # See sim2real#579 for the design rationale; the corresponding secret
  # (`inference-gateway-sa-metrics-reader-secret`) is created per-namespace
  # by roles-ns.yaml.
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRole
  metadata:
    name: sim2real-metrics-reader
  rules:
  # EPP calls TokenReview against the k8s API to validate incoming bearer
  # tokens on /metrics scrapes. Every SA in namespaces where EPP might
  # run needs this permission — hence the group-subject binding below.
  - apiGroups: ["authentication.k8s.io"]
    resources: ["tokenreviews"]
    verbs: ["create"]
  # EPP additionally calls SubjectAccessReview to check whether the
  # (validated) token identifies a principal authorized to GET /metrics.
  - apiGroups: ["authorization.k8s.io"]
    resources: ["subjectaccessreviews"]
    verbs: ["create"]
  # Client-side authorization: the scraper SA (helm-installer) needs
  # nonResourceURL /metrics get so its bearer token passes EPP's SAR check.
  - nonResourceURLs: ["/metrics"]
    verbs: ["get"]
  ---
  # Per-namespace binding — one CRB per pool namespace, distinguished by
  # the ${NAMESPACE} substitution. Group subject covers every SA in the
  # namespace: both helm-installer (the pipeline SA presenting the bearer)
  # and the chart-derived EPP SA (which calls TokenReview/SAR to validate).
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRoleBinding
  metadata:
    name: sim2real-metrics-reader-${NAMESPACE}
  subjects:
    - kind: Group
      name: system:serviceaccounts:${NAMESPACE}
      apiGroup: rbac.authorization.k8s.io
  roleRef:
    apiGroup: rbac.authorization.k8s.io
    kind: ClusterRole
    name: sim2real-metrics-reader
  ```

- [ ] **Step 2: Verify with `envsubst` simulation.**

  ```bash
  NAMESPACE=kalantar-0 envsubst < tektonc-data-collection/tekton/roles-cluster.yaml > /tmp/roles-cluster-expanded.yaml
  python3 -c "import yaml; list(yaml.safe_load_all(open('/tmp/roles-cluster-expanded.yaml')))" && echo "multi-doc YAML OK"
  grep -c 'kind: ClusterRoleBinding' /tmp/roles-cluster-expanded.yaml
  ```
  Expected: `multi-doc YAML OK`, at least 2 ClusterRoleBindings (`helm-installer-crb-kalantar-0` + `sim2real-metrics-reader-kalantar-0`).

### Task A2: Add per-namespace reader Secret to `roles-ns.yaml`

**Files:**
- Modify: `tektonc-data-collection/tekton/roles-ns.yaml`

**Interfaces:**
- Consumes: `${NAMESPACE}`, `helm-installer` SA (created earlier in same file).
- Produces: `Secret/inference-gateway-sa-metrics-reader-secret` in `${NAMESPACE}` with annotation `kubernetes.io/service-account.name: helm-installer`. Kubernetes populates `data.token` asynchronously with a valid helm-installer SA token.

- [ ] **Step 1: Append the Secret manifest to the file** (after the existing SA + Role + RoleBinding + SCC bindings):

  ```yaml
  ---
  # Bearer-token Secret consumed by the stream-metrics sidecar's
  # collect_metrics.sh scraper. Kubernetes auto-populates data.token
  # with a valid ServiceAccount token for helm-installer (the SA named
  # in the annotation). collect_metrics.sh reads this secret by its
  # default name (LLMDBENCH_EPP_METRICS_SECRET), so no upstream change
  # is required.
  #
  # This is the sim2real-side replacement for the Secret that the
  # llm-d-router chart's monitoring subchart would create if we let
  # standup enable it — which we can't, because it also emits
  # cluster-scoped RBAC that the non-admin pipeline SA can't provision.
  # See sim2real#579.
  apiVersion: v1
  kind: Secret
  metadata:
    name: inference-gateway-sa-metrics-reader-secret
    namespace: ${NAMESPACE}
    annotations:
      kubernetes.io/service-account.name: helm-installer
  type: kubernetes.io/service-account-token
  ```

- [ ] **Step 2: Verify.**

  ```bash
  NAMESPACE=kalantar-0 envsubst < tektonc-data-collection/tekton/roles-ns.yaml > /tmp/roles-ns-expanded.yaml
  python3 -c "import yaml; list(yaml.safe_load_all(open('/tmp/roles-ns-expanded.yaml')))" && echo "multi-doc YAML OK"
  grep -A2 'kind: Secret' /tmp/roles-ns-expanded.yaml
  ```
  Expected: `multi-doc YAML OK`, the Secret block visible with correct namespace + annotation.

### Task A3: Rewrite `tekton/tasks/stream-metrics.yaml` as a `collect_metrics.sh` wrapper

**Files:**
- Modify (rewrite): `tektonc-data-collection/tekton/tasks/stream-metrics.yaml`

**Interfaces:**
- Consumes: `data` workspace (unchanged), params `namespace`, `resultsDir`, `intervalSeconds` (default `"15"`), `harnessSha` (default = current vendored llm-d-benchmark commit SHA).
- Produces: `${RESULTS_DIR}/metrics/{raw,processed}/` + `metrics/collector.pid` + `metrics/metrics_collection.log` (from collect_metrics.sh's own logging).
- Exit condition: `${RESULTS_DIR}/metrics_stream_done` sentinel appears, OR collect_metrics.sh PID dies. Non-fatal on all failures.

- [ ] **Step 1: Full rewrite. Overwrite the file with the wrapper.**

  Reference: current file at 77e5c7b is ~330 lines of shell + awk. New file is ~60 lines of wrapper.

  ```yaml
  # tektonc-data-collection/tekton/tasks/stream-metrics.yaml
  apiVersion: tekton.dev/v1
  kind: Task
  metadata:
    name: stream-metrics
  spec:
    description: >-
      Runs llm-d-benchmark's collect_metrics.sh scraper as a Tekton
      sidecar, in parallel with the workload. Same sentinel-exit
      contract as stream-epp-logs and stream-gpu-stats.

      collect_metrics.sh + process_metrics.py are pulled from raw
      GitHub at a pinned commit SHA — no image customization, no
      workspace mount. The scraper reads its EPP bearer token from
      the `inference-gateway-sa-metrics-reader-secret` Secret which
      cluster.py provision / slot add creates per-namespace (annotated
      to helm-installer, type: kubernetes.io/service-account-token).

      Output layout (collect_metrics.sh convention):
        metrics/raw/<pod>_<timestamp>_metrics.log         (Prometheus text)
        metrics/raw/collection_debug.log
        metrics/processed/replica_status.json
        metrics/processed/replica_status_timeseries.json
        metrics/processed/pod_startup_times.json
        metrics/processed/metrics_summary.json

      Non-fatal: any failure logs a warning and exits 0 — the pipeline
      is not blocked by scraper failures.

    workspaces:
      - name: data
        description: "Shared PVC (data-pvc)"

    params:
      - { name: namespace,       type: string }
      - { name: resultsDir,      type: string }
      - { name: intervalSeconds, type: string, default: "15" }
      - name: harnessSha
        type: string
        description: >-
          llm-d-benchmark commit SHA to fetch collect_metrics.sh and
          process_metrics.py from. Default is the SHA vendored in
          sim2real's llm-d-benchmark tree at the time this task was
          last updated. Override for pinning behavior tests.
        default: "main"

    steps:
      - name: stream-metrics
        image: python:3.12-slim-bookworm
        securityContext:
          runAsUser: 0
        script: |
          #!/usr/bin/env bash
          set +e

          NAMESPACE="$(params.namespace)"
          RESULTS_DIR="/workspace/data/$(params.resultsDir)"
          SENTINEL="${RESULTS_DIR}/metrics_stream_done"
          INTERVAL="$(params.intervalSeconds)"
          HARNESS_SHA="$(params.harnessSha)"
          KUBECTL_VER="v1.34.1"

          # Phase 0 — install curl (apt) + kubectl (pinned binary from k8s.io).
          # python:3.12-slim ships python3 + bash + apt-get; we need curl for
          # scraping /metrics and fetching the harness scripts, and kubectl
          # for the pod-discovery calls inside collect_metrics.sh.
          apt-get update -qq >/dev/null 2>&1 && apt-get install -y --no-install-recommends curl >/dev/null 2>&1 || {
            echo "WARNING: apt-get install curl failed, cannot run collect_metrics.sh"
            exit 0
          }
          curl -fsSL -m 30 -o /usr/local/bin/kubectl \
            "https://dl.k8s.io/release/${KUBECTL_VER}/bin/linux/amd64/kubectl" || {
            echo "WARNING: kubectl download failed"
            exit 0
          }
          chmod +x /usr/local/bin/kubectl

          # Phase 1 — fetch collect_metrics.sh + process_metrics.py at pinned SHA.
          HARNESS_URL="https://raw.githubusercontent.com/llm-d/llm-d-benchmark/${HARNESS_SHA}/workload/harnesses"
          mkdir -p /usr/local/bin
          curl -fsSL -m 30 -o /usr/local/bin/collect_metrics.sh "${HARNESS_URL}/collect_metrics.sh" || {
            echo "WARNING: curl collect_metrics.sh failed (harnessSha=${HARNESS_SHA})"
            exit 0
          }
          curl -fsSL -m 30 -o /usr/local/bin/process_metrics.py  "${HARNESS_URL}/process_metrics.py" || {
            echo "WARNING: curl process_metrics.py failed (harnessSha=${HARNESS_SHA})"
            exit 0
          }
          chmod +x /usr/local/bin/collect_metrics.sh

          # Phase 2 — env vars consumed by collect_metrics.sh.
          export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR="${RESULTS_DIR}"
          export LLMDBENCH_VLLM_COMMON_NAMESPACE="${NAMESPACE}"
          export METRICS_COLLECTION_INTERVAL="${INTERVAL}"
          # Default value matches what roles-ns.yaml creates; kept explicit
          # so downstream can override if the Secret name ever changes.
          export LLMDBENCH_EPP_METRICS_SECRET="inference-gateway-sa-metrics-reader-secret"

          mkdir -p "${RESULTS_DIR}/metrics"
          rm -f "${SENTINEL}"
          LOG="${RESULTS_DIR}/metrics/metrics_collection.log"

          # Phase 3 — start scraper in background.
          echo "Starting collect_metrics.sh (harnessSha=${HARNESS_SHA}, interval=${INTERVAL}s)"
          bash /usr/local/bin/collect_metrics.sh start >> "${LOG}" 2>&1 &
          SCRAPER_PID=$!
          echo "Scraper PID: ${SCRAPER_PID}"

          # Phase 4 — sentinel poll. Exits on:
          #   (a) sentinel file appears (collect-results wrote it)
          #   (b) scraper process died on its own
          while true; do
            if [ -f "${SENTINEL}" ]; then
              echo "Sentinel found, stopping scraper."
              break
            fi
            if ! kill -0 "${SCRAPER_PID}" 2>/dev/null; then
              echo "Scraper process ${SCRAPER_PID} exited before sentinel."
              break
            fi
            sleep 1
          done

          # Phase 5 — stop + process.
          bash /usr/local/bin/collect_metrics.sh stop >> "${LOG}" 2>&1
          wait "${SCRAPER_PID}" 2>/dev/null || true
          echo "Processing collected metrics..."
          bash /usr/local/bin/collect_metrics.sh process >> "${LOG}" 2>&1

          # Phase 6 — final report.
          n_raw=$(find "${RESULTS_DIR}/metrics/raw" -name '*_metrics.log' 2>/dev/null | wc -l | tr -d ' ')
          n_proc=$(find "${RESULTS_DIR}/metrics/processed" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
          echo "Metrics scraping complete for $(params.resultsDir): ${n_raw} raw scrape(s), ${n_proc} processed JSON(s)"
  ```

  Notable choices:
  - `#!/usr/bin/env bash` in the wrapper — python-slim ships bash as `/bin/bash`. `collect_metrics.sh` invoked as `bash /usr/local/bin/collect_metrics.sh` for clarity even though the interpreter would find it.
  - `curl -fsSL -m 30` for the harness + kubectl fetches: `-f` fails on HTTP 4xx/5xx (so pipes stay clean), `-m 30` avoids indefinite hangs.
  - Every install step is `|| exit 0` — non-fatal: if apt or the download endpoint is unreachable, task stays green rather than failing the whole PipelineRun. Sentinels + workload progress are unaffected.
  - No target discovery in the wrapper — `collect_metrics.sh` does it internally.
  - `kubectl` pin `v1.34.1` matches the version the sibling streamers use via `alpine/kubectl:1.34.1`.

- [ ] **Step 2: Verify YAML parses.**

  ```bash
  python3 -c "import yaml; yaml.safe_load(open('tektonc-data-collection/tekton/tasks/stream-metrics.yaml'))" && echo "OK"
  ```

### Task A4: Commit + push + open upstream PR

- [ ] **Step 1: Create branch in submodule + commit.**

  ```bash
  cd tektonc-data-collection
  git checkout -b feat/adopt-collect-metrics-with-rbac
  git add tekton/roles-cluster.yaml tekton/roles-ns.yaml tekton/tasks/stream-metrics.yaml
  git commit -m "feat: adopt llm-d-benchmark's collect_metrics.sh as stream-metrics scraper
  
  ... (see full body below)"
  ```

  Body should cover: (a) RBAC additions to roles-cluster/roles-ns, (b) rewrite of stream-metrics as wrapper, (c) why (issue #579 rationale summary), (d) note that sim2real bump PR follows.

- [ ] **Step 2: Push + PR.**

  ```bash
  git push -u origin feat/adopt-collect-metrics-with-rbac
  gh pr create --repo inference-sim/tektonc-data-collection --base main \
    --title "feat: adopt collect_metrics.sh; add sim2real-metrics-reader RBAC" \
    --body-file /tmp/upstream-579-pr-body.md
  ```

- [ ] **Step 3: Wait for upstream merge.**

---

## Section B — `sim2real` parent PR

### Task B1: Bump submodule pointer

- [ ] **Step 1: Fetch merged upstream + check out.**

  ```bash
  cd tektonc-data-collection
  git fetch origin
  git checkout origin/main
  cd -
  ```

- [ ] **Step 2: Verify the three files carry the changes.**

  ```bash
  grep -q sim2real-metrics-reader tektonc-data-collection/tekton/roles-cluster.yaml && echo "CR/CRB present"
  grep -q inference-gateway-sa-metrics-reader-secret tektonc-data-collection/tekton/roles-ns.yaml && echo "Secret present"
  grep -q 'apk add' tektonc-data-collection/tekton/tasks/stream-metrics.yaml && echo "wrapper installed apk step present"
  ```

- [ ] **Step 3: Stage the bump.** `git add tektonc-data-collection`.

### Task B2: Update `deploy.py collect` for the new output layout

**Files:**
- Modify: `pipeline/deploy.py:1461-1517` — the `_extract_phases_from_pvc` skip_logs=True block.
- Modify: `pipeline/tests/test_deploy_collect.py` — update the metrics-specific tests.

**Interfaces:**
- Existing `metrics/` copy stanza (added in #575) does a recursive `kubectl cp remote/metrics/ local/metrics/`. This already captures whatever collect_metrics.sh writes inside `metrics/` — no code change needed for the copy itself. The stale-wipe stanza already includes `"metrics"` in the subdir tuple.
- What DOES change: the test assertions and the analysis-side expectations.

- [ ] **Step 1: Update `test_collect_skip_logs_invokes_metrics_copy` in `test_deploy_collect.py`.**

  Existing test asserts `/wl-smoke/i1/metrics/` and `/wl-smoke/i1/metrics_stream_done` appear in cp targets. Both are still correct — the copy is recursive; new layout just puts more files inside. No functional change to the test, but update the docstring to say "collect_metrics.sh-shaped `metrics/{raw,processed}/` layout" instead of "timeseries.csv layout".

- [ ] **Step 2: Run the test suite.**

  ```bash
  python -m pytest pipeline/tests/test_deploy_collect.py -v -k 'metrics or stale'
  ```
  Expected: green.

### Task B3: Update `pipeline/README.md` — "Metric capture" subsection

**Files:**
- Modify: `pipeline/README.md`, the "Metric capture" section added in #575 (search for `### Metric capture` around line 524).

- [ ] **Step 1: Rewrite the subsection.**

  Replace the old content with a description of the new architecture:
  - `stream-metrics` is a thin wrapper around `collect_metrics.sh` from llm-d-benchmark.
  - Output layout: `metrics/raw/<pod>_<ts>_metrics.log` (Prometheus text) + `metrics/processed/*.json` (aggregated summaries).
  - EPP bearer auth uses `inference-gateway-sa-metrics-reader-secret` provisioned per-namespace by `cluster.py provision` / `slot add`.
  - How to widen metric coverage: edit `AGGREGATE_METRICS` in `process_metrics.py` upstream; harnessSha task param picks the version.
  - Live inspection recipe (kubectl port-forward + curl) — carry over unchanged.

  Also update the artifact-inventory paragraph earlier in the section to list the new `metrics/raw/` and `metrics/processed/` paths instead of `metrics/timeseries.csv` + `metrics/<target>.discovered.txt`.

- [ ] **Step 2: Verify markdown renders (visual grep).**

  ```bash
  grep -c 'collect_metrics.sh\|metrics/raw\|metrics/processed' pipeline/README.md
  ```
  Expected: ≥ 4 hits.

### Task B4: Update `CLAUDE.md` — workspace artifact table

**Files:**
- Modify: `CLAUDE.md`, the workspace artifact table (currently has rows for `metrics/timeseries.csv` and `metrics/<target>.discovered.txt`).

- [ ] **Step 1: Replace the two rows with new ones.**

  Delete:
  ```
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/timeseries.csv` | ... |
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/<target>.discovered.txt` | ... |
  ```

  Add:
  ```
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/raw/<pod>_<ts>_metrics.log` | `deploy.py collect` (pulled from PVC) | analysis — raw Prometheus text-exposition-format scrapes, one file per pod per tick (collect_metrics.sh output) |
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/processed/metrics_summary.json` | `deploy.py collect` (pulled from PVC) | analysis — aggregated percentiles over the metrics named in process_metrics.py:AGGREGATE_METRICS |
  | `runs/<run>/results/{phase}/<workload>/i<N>/metrics/processed/replica_status_timeseries.json` | `deploy.py collect` (pulled from PVC) | analysis — replica scale/state over the run |
  ```

### Task B5: Update `sim2real-analyze` skill data reference

**Files:**
- Modify: `.claude/skills/sim2real-analyze/SKILL.md` — the data-reference block that shows the cell layout.

- [ ] **Step 1: Replace the `metrics/timeseries.csv` + `<target>.discovered.txt` lines** with the new `metrics/raw/*.log` + `metrics/processed/*.json` layout.

### Task B6: Add a superseded-by note to the #574 plan doc (do NOT delete)

**Files:**
- Modify: `docs/superpowers/plans/2026-07-15-issue-574-stream-metrics.md`

- [ ] **Step 1: Add a preamble.**

  Prepend to the top of the file:

  ```markdown
  > **Superseded by issue #579 / plan `2026-07-15-issue-579-adopt-collect-metrics.md`.**
  > This plan is preserved as a historical record of the initial sim2real-owned
  > sidecar design (issues #574 / #576, PRs #575 / #577). The current design
  > wraps upstream `collect_metrics.sh` instead of maintaining our own scraper.
  ```

  Do not delete the file — it's a historical record of design evolution.

### Task B7: Update / close #578

**Interfaces:** none — GitHub-side hygiene only.

- [ ] **Step 1: Add a comment to #578** noting that #579 supersedes it, with a brief summary of why (chart-side auth-delegator can't be provided by the chart because non-admin standup strips the monitoring path; sim2real provisions equivalent RBAC directly at bootstrap).

- [ ] **Step 2: Close #578.**

  ```bash
  gh issue close 578 --comment "Superseded by #579 — that issue's fix provisions the equivalent auth RBAC at cluster.py provision / slot add time, sidestepping the need for a chart-side change."
  ```

### Task B8: Sweep for stale references

- [ ] **Step 1: grep for old artifact names.**

  ```bash
  grep -rn --include='*.md' --include='*.py' --include='*.yaml' \
    -e 'timeseries.csv' -e 'discovered.txt' \
    -e 'metricsAllowlist' -e 'EPP_SELECTOR_USED' \
    .claude/ pipeline/ docs/ CLAUDE.md
  ```

- [ ] **Step 2: For each hit, decide stale vs still-accurate.** Update any docs/prompts referencing the old shape.

### Task B9: Run the full CI suite locally

- [ ] **Step 1: Lint.**

  ```bash
  ruff check pipeline/ .claude/skills/ --select F
  ```

- [ ] **Step 2: Full pytest run (matches `.github/workflows/test.yml`).**

  ```bash
  python -m pytest pipeline/ \
    pipeline/tests/test_layout.py pipeline/tests/test_cluster_ops.py \
    pipeline/tests/test_cluster_py.py pipeline/tests/test_slicer.py \
    pipeline/tests/test_sim2real.py pipeline/tests/test_assemble_run.py \
    pipeline/tests/test_translation_ref.py pipeline/tests/test_translate.py \
    pipeline/tests/test_build.py pipeline/tests/test_pairkey.py \
    pipeline/tests/test_load_pairs.py \
    .claude/skills/sim2real-analyze/tests/ \
    .claude/skills/sim2real-bootstrap/tests/ \
    .claude/skills/sim2real-translate/tests/ \
    .claude/skills/sim2real-check/tests/ \
    -v
  ```

### Task B10: Commit + push + open parent PR

- [ ] **Step 1: Path-discipline check.**

  ```bash
  pwd  # should contain .claude/worktrees/issue-579-adopt-collect-metrics
  git status --short
  git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status --short | head -10
  ```

- [ ] **Step 2: Stage + commit.**

  ```bash
  git add tektonc-data-collection \
          pipeline/deploy.py pipeline/tests/test_deploy_collect.py \
          pipeline/README.md CLAUDE.md \
          .claude/skills/sim2real-analyze/SKILL.md \
          docs/superpowers/plans/2026-07-15-issue-574-stream-metrics.md \
          docs/superpowers/plans/2026-07-15-issue-579-adopt-collect-metrics.md
  git commit -m "$(cat <<'EOF'
  feat(pipeline): adopt collect_metrics.sh; provision EPP metrics RBAC at bootstrap
  
  Closes #579. Supersedes #578.
  
  ... (full body — issue rationale summary + companion upstream PR link)
  EOF
  )"
  ```

- [ ] **Step 3: Push + PR.**

  ```bash
  git push -u origin worktree-issue-579-adopt-collect-metrics
  gh pr create --title "feat(pipeline): adopt collect_metrics.sh + provision EPP metrics RBAC — closes #579" \
               --body-file /tmp/parent-579-pr-body.md
  ```

  PR body should:
  - `Closes #579`.
  - Link the merged upstream PR.
  - Stale-reference sweep summary.
  - Test results.

## Acceptance criteria

1. `sim2real-metrics-reader` ClusterRole exists after `cluster.py provision` on a fresh cluster.
2. `sim2real-metrics-reader-<ns>` ClusterRoleBinding + `inference-gateway-sa-metrics-reader-secret` Secret exist per pool namespace after `slot add`.
3. Secret's `data.token` field is auto-populated by kubelet (visible via `kubectl get secret … -o jsonpath='{.data.token}' | base64 -d`).
4. A workload run in a provisioned namespace produces:
   - `metrics/raw/*_metrics.log` for both EPP and vLLM decode pods.
   - `metrics/processed/metrics_summary.json` with populated percentiles.
   - No `Authentication failed` / `tokenreviews … is forbidden` lines in EPP pod logs.
5. `deploy.py collect` copies both `metrics/raw/` and `metrics/processed/` into the local cell.
6. `slot remove <ns>` results in the ClusterRoleBinding + Secret being removed (idempotent if not present).
7. Existing `test_collect_skip_logs_invokes_metrics_copy` still passes (recursive copy already handles the new layout).
8. `ruff check pipeline/ --select F` clean; full pytest suite green.
9. Issue #578 is closed with a comment linking #579.

## Post-merge

The user will re-add the slot (or manually re-apply the updated `stream-metrics` Task) to their live cluster after merge. No migration instructions or automated migration path required in the PR body.
