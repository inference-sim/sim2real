# sim2real Pipeline Workflow

Four scripts run in sequence: **setup** → **prepare** → **deploy** → **analyze**.

## High-Level Pipeline

```mermaid
flowchart LR
    subgraph "Phase 0: Setup"
        setup["setup.py"]
    end

    subgraph "Phase 1: Prepare"
        prepare["prepare.py"]
    end

    subgraph "Phase 2: Deploy"
        deploy["deploy.py"]
    end

    subgraph "Phase 3: Analyze"
        analyze["analyze.py"]
    end

    setup --> prepare --> deploy --> analyze
```

---

## setup.py — One-Time Environment Bootstrap

Idempotent. Creates namespace, secrets, PVCs, and deploys Tekton tasks.
Safe to re-run.

```mermaid
flowchart TD
    S1["Step 1: Check prerequisites\n(kubectl, tkn, python3, gh, git, envsubst,\npodman/docker)"]
    S2["Step 2: Init git submodules\n(inference-sim, llm-d-inference-scheduler,\ntektonc-data-collection)"]
    S3["Step 3: Create Python .venv\n+ install requirements.txt"]
    S4["Step 4: Configure namespace"]
    S4b["Step 4b: Configure run name\n(e.g. sim2real-2026-03-31)"]
    S5["Step 5: Create HuggingFace secret\n(hf-secret)"]
    S6["Step 6: Apply RBAC roles\n(+ OpenShift SCCs if detected)"]
    S7["Step 7: Create PVCs\n(model-pvc 300Gi, data-pvc 20Gi,\nsource-pvc 20Gi)"]
    S8["Step 8: Verify Tekton operator"]
    S9["Step 9: Deploy Tekton steps + tasks"]
    S10["Step 10: Configure container registry\n+ create registry-secret"]
    S12["Step 12: Update config/env_defaults.yaml\nwith registry values"]
    OUT["Write outputs:\n• workspace/setup_config.json\n• workspace/runs/{run}/run_metadata.json"]

    S1 --> S2 --> S3 --> S4 --> S4b --> S5
    S5 --> S6 --> S7 --> S8 --> S9 --> S10 --> S12 --> OUT

    style S4 fill:#fff3cd,stroke:#ffc107
    style S4b fill:#fff3cd,stroke:#ffc107
    style S5 fill:#fff3cd,stroke:#ffc107
    style S10 fill:#fff3cd,stroke:#ffc107
```

**Legend:**
- Yellow = human interactive input (namespace, tokens, registry credentials)

**Inputs:** CLI flags, environment variables, or interactive prompts
**Outputs:** `workspace/setup_config.json`, `workspace/runs/{run}/run_metadata.json`

---

## prepare.py — Extract, Translate, Generate, Build/Test, Review

The core AI-driven phase. Extracts the algorithm, maps signals, generates a
production scorer plugin, builds/tests it, and runs multi-model AI review.

### Stage 1: Extract

```mermaid
flowchart TD
    E1["transfer_cli.py extract --strict\n(deterministic parser)"]
    E2["validate-schema\nalgorithm_summary.json"]
    E3["Scope validation check"]
    E4["🤖 claude -p\nEnhanced extraction\n(full-file + cross-file context)"]
    E5{"AI Review\n🤖 3 models:\nGPT-4o, Gemini, Claude"}
    E6{"Human Gate\n👤 [e]dit / [c]hat /\n[d]one / [q]uit"}
    E7["prepare_algorithm_summary.json"]

    E1 --> E2 --> E3 --> E4 --> E5
    E5 -->|"issues found"| E6
    E5 -->|"passed"| E6
    E6 -->|"edited"| E5
    E6 -->|"done"| E7

    style E1 fill:#e8f5e9,stroke:#4caf50
    style E4 fill:#e3f2fd,stroke:#2196f3
    style E5 fill:#e3f2fd,stroke:#2196f3
    style E6 fill:#fff3cd,stroke:#ffc107
```

**Inputs:**
- `blis_router/best/` (EVOLVE-BLOCK source, experiment info)
- `docs/transfer/blis_to_llmd_mapping.md`

**Outputs:**
- `prepare_algorithm_summary.json` — signals, weights, cross-file deps

### Stage 2: Translate

```mermaid
flowchart TD
    T1["Submodule staleness check\n(mapping commit hash vs HEAD)"]
    T2["🤖 Single LLM call: GPT-4o\nMap sim signals → production equivalents"]
    T3["Post-process + validate-schema"]
    T4["🤖 claude -p\nEnhanced translation\n(type mappings, helper translations)"]
    T5{"AI Review\n🤖 3 models"}
    T6{"Human Gate\n👤 [e]dit / [c]hat /\n[d]one / [q]uit"}
    T7["Write prepare_full_context.md"]
    T8["prepare_signal_coverage.json"]

    T1 --> T2 --> T3 --> T4 --> T5
    T5 -->|"issues"| T6
    T5 -->|"passed"| T6
    T6 -->|"edited"| T5
    T6 -->|"done"| T7 --> T8

    style T2 fill:#e3f2fd,stroke:#2196f3
    style T4 fill:#e3f2fd,stroke:#2196f3
    style T5 fill:#e3f2fd,stroke:#2196f3
    style T6 fill:#fff3cd,stroke:#ffc107
```

**Inputs:**
- `prepare_algorithm_summary.json` (from Stage 1)
- `docs/transfer/blis_to_llmd_mapping.md`

**Outputs:**
- `prepare_signal_coverage.json` — signal→production mapping with access paths
- `prepare_full_context.md` — combined context for downstream stages

### Stage 3: Generate (Writer + Reviewer Loop)

The most complex stage. An LLM writes the plugin code, the code is built and
tested, then 3 reviewer models check translation fidelity. Issues feed back
into the next round.

```mermaid
flowchart TD
    P["🤖 Preamble: claude -p (haiku)\nBuild codebase + reviewer context docs"]
    GEN["🤖 Writer: claude -p (opus)\nGenerate plugin .go + test .go\n+ register in register.go"]
    SNAP["Snapshot plugin source"]
    BUILD{"go build + go vet + go test\n(deterministic)"}
    REV["🤖 Review: claude -p (haiku)\nruns review_translation.py which\ncalls GPT-4o + Gemini + Claude"]
    CONS{"Consensus?"}
    PAUSE{"Human pause\n👤 [c]ontinue / [+N] /\n[a]ccept / [q]uit"}
    TEKTON["Generate Tekton artifacts:\n• algorithm_values.yaml\n• merge-values → values.yaml"]
    OUT["prepare_stage3_output.json"]

    P --> GEN --> SNAP --> BUILD
    BUILD -->|"fail"| GEN
    BUILD -->|"pass"| REV --> CONS
    CONS -->|"yes"| TEKTON
    CONS -->|"no, rounds left"| GEN
    CONS -->|"no, max rounds"| PAUSE
    PAUSE -->|"continue"| GEN
    PAUSE -->|"accept"| TEKTON
    TEKTON --> OUT

    style P fill:#e3f2fd,stroke:#2196f3
    style GEN fill:#e3f2fd,stroke:#2196f3
    style REV fill:#e3f2fd,stroke:#2196f3
    style PAUSE fill:#fff3cd,stroke:#ffc107
    style BUILD fill:#e8f5e9,stroke:#4caf50
```

**Inputs:**
- `prepare_algorithm_summary.json`, `prepare_signal_coverage.json`
- `docs/transfer/scorer_template.go.md`, example plugins
- `config/env_defaults.yaml`, `blis_router/llm_config.yaml`

**Outputs:**
- Plugin `.go` file in `llm-d-inference-scheduler/pkg/plugins/scorer/`
- `prepare_stage3_output.json`
- `prepare_tekton/algorithm_values.yaml`, `prepare_tekton/values.yaml`

### Stage 4: Build/Test + Equivalence Gate

```mermaid
flowchart TD
    BT["go build + go vet + go test\n(re-validates plugin compiles)"]
    EQ["Run equivalence commands\nfrom transfer.yaml manifest"]
    OUT["prepare_equivalence_results.json"]

    BT --> EQ --> OUT

    style BT fill:#e8f5e9,stroke:#4caf50
    style EQ fill:#e8f5e9,stroke:#4caf50
```

### Stage 5: Final Review

```mermaid
flowchart TD
    FR["🤖 review_translation.py\nAll 3 models review plugin\nvs EVOLVE-BLOCK fidelity"]
    PASS{"Consensus?"}
    RETRY["Return to Stage 3\n(up to 3 outer retries)"]
    DONE["prepare_translation_reviews.json"]
    SNAP["Persist plugin + test snapshots\ninto run directory"]

    FR --> PASS
    PASS -->|"no"| RETRY
    PASS -->|"yes"| DONE --> SNAP

    style FR fill:#e3f2fd,stroke:#2196f3
```

**Final prepare.py outputs** (all in `workspace/runs/{run}/`):
| Artifact | Description |
|----------|-------------|
| `prepare_algorithm_summary.json` | Extracted algorithm metadata |
| `prepare_signal_coverage.json` | Signal→production mapping |
| `prepare_stage3_output.json` | Plugin file paths, Tekton artifact paths |
| `prepare_equivalence_results.json` | Equivalence gate results |
| `prepare_translation_reviews.json` | Final AI review consensus |
| `prepare_scorer_snapshot.go` | Frozen copy of generated plugin |
| `prepare_tekton/values.yaml` | Merged Tekton pipeline values |

---

## deploy.py — Build EPP, Cluster Benchmarks, PR

Builds the treatment container image, runs real cluster benchmarks via Tekton
pipelines, and optionally creates a PR.

### Stage 1: Build EPP Image

```mermaid
flowchart TD
    PRE["Check prerequisites:\n• Phase 1 artifacts exist\n• Plugin builds\n• AI review passed\n• Equivalence gate passed\n• Registry configured"]
    BUILD["Build EPP image on-cluster\nvia BuildKit (build-epp.sh)"]
    INJECT["Inject image ref into\nalgorithm_values.yaml"]
    MERGE["Re-merge values.yaml"]
    COMPILE["compile-pipeline → Tekton YAMLs\nfor noise, baseline, treatment"]
    APPLY["kubectl apply pipelines"]

    PRE --> BUILD --> INJECT --> MERGE --> COMPILE --> APPLY

    style BUILD fill:#e8f5e9,stroke:#4caf50
```

### Stage 2: Cluster Benchmarks

```mermaid
flowchart TD
    FAST{"fast_iteration\nmode?"}

    subgraph "Full Mode"
        NOISE["Noise characterization\n(N sequential Tekton runs)"]
        NRES["Extract noise results"]
    end

    BASE["Baseline pipeline\n(load-aware scorer)"]
    BRES["Extract baseline results"]
    TREAT["Treatment pipeline\n(evolved scorer)"]
    TRES["Extract treatment results"]
    MECH["Mechanism check:\ntransfer_cli.py benchmark\n(noise CV, T_eff, per-workload)"]
    TABLE["Comparison table:\ntransfer_cli.py compare"]
    VAL["deploy_validation_results.json"]

    FAST -->|"false"| NOISE --> NRES --> BASE
    FAST -->|"true\n(skip noise + mech)"| BASE
    BASE --> BRES --> TREAT --> TRES
    TRES -->|"full mode"| MECH --> TABLE --> VAL
    TRES -->|"fast mode"| TABLE --> VAL

    style NOISE fill:#fce4ec,stroke:#e91e63
    style BASE fill:#fce4ec,stroke:#e91e63
    style TREAT fill:#fce4ec,stroke:#e91e63
```

Pink = runs on Kubernetes cluster via Tekton pipelines (blis observe against live vLLM)

### Stage 3: PR Creation (if --pr and not fast_iteration)

```mermaid
flowchart TD
    CHECK["Validate overall_verdict\n(PASS or INCONCLUSIVE with operator_notes)"]
    EVIDENCE["generate-evidence → transfer_evidence.md"]
    BRANCH["git checkout -b transfer/{alg_name}"]
    PUSH["git push -u origin"]
    CAL["Append calibration log"]
    PR["gh pr create"]

    CHECK --> EVIDENCE --> BRANCH --> PUSH --> CAL --> PR

    style PR fill:#e8f5e9,stroke:#4caf50
```

**deploy.py outputs** (all in `workspace/runs/{run}/`):
| Artifact | Description |
|----------|-------------|
| `deploy_baseline_results.json` | Baseline latency metrics per workload |
| `deploy_treatment_results.json` | Treatment latency metrics per workload |
| `deploy_noise_results.json` | Noise characterization (full mode only) |
| `deploy_benchmark_output.json` | Mechanism check verdict (full mode only) |
| `deploy_validation_results.json` | Overall verdict (PASS/FAIL/INCONCLUSIVE) |
| `deploy_comparison_table.txt` | Human-readable latency comparison |

---

## analyze.py — Latency Comparison Charts

Post-processing script that generates visual charts from deploy artifacts.
No LLM involvement — purely deterministic.

```mermaid
flowchart TD
    LOAD["Load baseline + treatment results\nfrom run directory"]
    TABLE["Print comparison table\n(transfer_cli.py compare)"]
    CHARTS["Generate per-workload bar charts\n(3×3 subplots: TTFT/TPOT/E2E ×\nmean/p50/p99)"]
    HEAT["Generate summary heatmap\n(workloads × metrics, % change)"]
    SUMMARY["Print terminal summary\n(noise CV, T_eff, per-workload\nclassifications)"]

    LOAD --> TABLE --> CHARTS --> HEAT --> SUMMARY

    style LOAD fill:#e8f5e9,stroke:#4caf50
    style CHARTS fill:#e8f5e9,stroke:#4caf50
    style HEAT fill:#e8f5e9,stroke:#4caf50
```

**Inputs:** `deploy_baseline_results.json`, `deploy_treatment_results.json`, `deploy_validation_results.json`
**Outputs:** `results_charts/workload_*.png`, `results_charts/summary_heatmap.png`

---

## End-to-End Data Flow

```mermaid
flowchart TD
    subgraph "Inputs"
        BLIS["blis_router/best/\n(EVOLVE-BLOCK,\nllm_config.yaml,\nworkloads/)"]
        MAP["docs/transfer/\nblis_to_llmd_mapping.md"]
        TMPL["docs/transfer/\nscorer_template.go.md"]
        ENV["config/\nenv_defaults.yaml"]
    end

    subgraph "setup.py  👤"
        SETUP_OUT["workspace/\nsetup_config.json"]
    end

    subgraph "prepare.py  🤖 + 👤"
        ALGO["algorithm_summary\n.json"]
        SIG["signal_coverage\n.json"]
        PLUGIN["scorer plugin\n.go + _test.go"]
        TEKTON_V["prepare_tekton/\nvalues.yaml"]
        EQUIV["equivalence_results\n.json"]
    end

    subgraph "deploy.py  🖥️ cluster"
        EPP["EPP container\nimage"]
        BASE_R["baseline_results\n.json"]
        TREAT_R["treatment_results\n.json"]
        VAL_R["validation_results\n.json"]
    end

    subgraph "analyze.py  📊"
        CHARTS["results_charts/\n*.png"]
        COMP["comparison_table\n.txt"]
    end

    BLIS --> ALGO
    MAP --> ALGO
    MAP --> SIG
    ALGO --> SIG
    SIG --> PLUGIN
    TMPL --> PLUGIN
    ENV --> TEKTON_V
    ALGO --> TEKTON_V
    PLUGIN --> EPP
    TEKTON_V --> EPP
    SETUP_OUT --> EPP
    EPP --> BASE_R
    EPP --> TREAT_R
    BASE_R --> VAL_R
    TREAT_R --> VAL_R
    BASE_R --> CHARTS
    TREAT_R --> CHARTS
    BASE_R --> COMP
    TREAT_R --> COMP
```

---

## Actor Legend

| Symbol | Actor | Where |
|--------|-------|-------|
| 🤖 | LLM (claude -p, GPT-4o, Gemini, Claude) | Extract enhance, Translate, Generate, Review |
| 👤 | Human operator | Setup prompts, review gates ([e]dit/[c]hat/[d]one), round pauses |
| 🖥️ | Kubernetes cluster (Tekton) | Noise/baseline/treatment benchmarks, EPP build |
| 📊 | Deterministic code (Python/CLI) | extract parser, validate-schema, merge-values, compare, charts |

### LLM Usage Summary

| Stage | LLM Role | Models Used |
|-------|----------|-------------|
| Extract (base) | None — deterministic parser | — |
| Extract (enhanced) | Full-file + cross-file context extraction | claude -p |
| Extract review | Check completeness | GPT-4o + Gemini + Claude |
| Translate (base) | Map sim signals → production paths | GPT-4o (single call) |
| Translate (enhanced) | Add type mappings, helper translations | claude -p |
| Translate review | Check coverage | GPT-4o + Gemini + Claude |
| Generate (context) | Build codebase + reviewer context docs | claude -p (haiku) |
| Generate (writer) | Write scorer plugin + test | claude -p (opus) |
| Generate (review) | Translation fidelity check via review script | GPT-4o + Gemini + Claude |
| Final review | Same as generate review | GPT-4o + Gemini + Claude |

### Human Touchpoints

| Where | What | Can Skip? |
|-------|------|-----------|
| setup.py | Namespace, tokens, registry credentials | Yes (CLI flags / env vars) |
| Extract gate | Review AI-extracted algorithm summary | Yes (`--no-gate`) |
| Translate gate | Review signal mapping | Yes (`--no-gate`) |
| Generate pause | Continue/accept after max review rounds | No (intentional) |
| Artifact reuse | Reuse vs regenerate existing artifacts | Yes (`--force`) |
