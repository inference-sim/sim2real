# sim2real Roadmap

> **Status**: Living document. Priorities will shift as the project evolves — treat this as
> direction, not commitment. Open a PR or issue to suggest changes.

## Overview

sim2real is a pipeline for taking simulation-discovered algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) into production serving systems.
The aim is a general, reproducible process for promoting an algorithm found in simulation to a
real deployment — it is not tied to any single production target. In practice the pipeline is
currently developed and validated against [llm-d-router](https://github.com/llm-d/llm-d-router)
(as scorer/EPP plugins), which serves as the reference target, but the process is designed to
generalize to other targets.

The project is in **active development**. The pipeline is functional end-to-end and CI enforces
≥90% test coverage. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## Phase 1: Hardening & Open-Source Readiness (Near-Term)

*Goal: Eliminate known security risks, stabilize CI, and reach public-release readiness.*

- [ ] Publish contributor-facing docs — LICENSE, `CONTRIBUTING.md`, `ROADMAP.md`
- [ ] Cut the first `v0.1.0` release tag
- [ ] Complete the security hardening pass — RBAC least-privilege, supply-chain pinning
      (base images, GitHub Actions, buildkit / kubectl / helm), and subprocess timeouts

---

## Phase 2: Decomposition (Mid-Term — 1–3 Months)

*Goal: Break apart the monolithic pipeline modules to enable parallel development, better
testability, and faster iteration.*

### Architecture Refactoring
- [ ] **kubectl abstraction layer** (#656) — replace the many raw subprocess calls in
      `deploy.py`, `setup.py`, and `cluster.py` with a typed `kubectl.py` module
- [ ] **deploy.py decomposition** (#654) — split the orchestrator monolith into modules by subcommand
- [ ] **sim2real.py decomposition** (#658) — resolve deferred imports and extract to modules

### Feature Work
- [ ] Round-trip integration test: `translation register --build` → `sim2real assemble` (#592)
- [ ] Epic: step-6 validate/execute + auto-fix (#534)

---

## Phase 3: Scale & Reliability (Mid-Term — 2–4 Months)

*Goal: Make the pipeline robust for multi-team, multi-cluster operations.*

### Reliability
- [ ] Orchestrator ConfigMap save/restore — prevent clobber of out-of-band edits (#450)
- [ ] Classify infra-caused `PipelineRun Failed` and route to retry (#567)
- [ ] Data PVC path scoping by scenario (#553)
- [ ] Auto-prune stale progress-dict entries (#554)

### Observability
- [ ] Stale orchestrator image warning when `--remote` is used (#376)
- [ ] GPU capacity probe: per-node fragmentation for multi-GPU pods (#262)

---

## Contributor Focus Areas

New contributors are especially welcome in these areas:

| Area | Entry Point | Skill Level |
|---|---|---|
| Documentation | Issues labeled `documentation` | Beginner |
| Test coverage | Issues labeled `good first issue` | Intermediate |
| kubectl abstraction | Issue #656 | Intermediate |
| deploy.py decomposition | Issue #654 | Advanced |

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

---

*This roadmap is a living document. Open a PR or issue to suggest changes.*
