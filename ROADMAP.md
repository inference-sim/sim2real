# sim2real Roadmap

> **Status**: Living document. Last updated: 2026-07-30.
> This roadmap reflects current strategic priorities as assessed by the Hive strategist agent.
> Operators and contributors should adjust priorities as project needs evolve.

## Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) to production
[llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) scorer plugins.

The project is in **active development** with a small, focused team. The pipeline is functional
end-to-end and has recently reached 97% test coverage. Current focus is hardening, decomposition,
and expanding the feature surface.

---

## Phase 1: Hardening (Current — Near-Term)

*Goal: Eliminate known security risks, stabilize CI, and reduce technical debt.*

### Security (Immediate)
- [ ] Merge PR #699 — Replace privileged BuildKit pod with rootless alternative
- [ ] Merge PR #697 — Harden `data-pvc-explorer` debug manifest (remove root, pin image)
- [ ] Close PR #648 — Contains regression risk; open clean RBAC-only fix instead
- [ ] Resolve `id-token: write` in `claude.yml` — scope to only jobs that need OIDC

### CI & Quality
- [ ] Merge PR #717 — Add coverage enforcement (fail below threshold)
- [ ] Merge PR #718 — Suppress Node.js 20 deprecation warnings
- [ ] Close 20 fully-merged stale branches (issue #724)
- [ ] Add `git fetch --prune` to CI workflow

### Documentation
- [ ] Create `CONTRIBUTING.md` (issue #620) — contributor onboarding
- [ ] Merge open guide doc PRs (#702, #704, #705, #707, #710) as a batch

---

## Phase 2: Decomposition (Mid-Term — 1–3 Months)

*Goal: Break apart the monolithic pipeline modules to enable parallel development, better testability, and faster iteration.*

### Architecture Refactoring
- [ ] **kubectl abstraction layer** (issue #656) — Replace 58+ raw subprocess calls in `deploy.py`, `setup.py`, `cluster.py` with a typed `kubectl.py` module
  - Unblocks: reliable timeout enforcement, subprocess security hardening
  - Effort: ~1 week
- [ ] **deploy.py decomposition** (issue #654) — Split 3,776-line monolith into modules by subcommand
  - Effort: ~1.5 weeks
- [ ] **sim2real.py decomposition** (issue #658) — Resolve 24 deferred imports, extract to modules
  - Effort: ~1 week
- [ ] **REPO_ROOT centralization** (issue #657) — Replace 7× fragile parent-chain traversal with single config
  - Effort: ~2 days

### Feature Work
- [ ] Round-trip integration test: `translation register --build` → `sim2real assemble` (issue #592)
- [ ] Epic: Step-6 validate/execute + auto-fix (issue #534)
- [ ] Per-iteration `wipe` subcommand consolidation (issue #168)

---

## Phase 3: Scale & Reliability (Mid-Term — 2–4 Months)

*Goal: Make the pipeline robust for multi-team, multi-cluster operations.*

### Reliability
- [ ] Orchestrator ConfigMap save/restore — prevent clobber of out-of-band edits (issue #450)
- [ ] classify infra-caused `PipelineRun Failed` and route to retry (issue #567)
- [ ] Data PVC path scoping by scenario (issue #553)
- [ ] Auto-prune stale progress-dict entries (issue #554)

### Observability
- [ ] Stale orchestrator image warning when `--remote` is used (issue #376)
- [ ] GPU capacity probe: per-node fragmentation for multi-GPU pods (issue #262)
- [ ] Shadow reservation ledger (from worktree-issue-255)

---

## Phase 4: Calibration & Advanced Features (Long-Term — 3+ Months)

*Goal: Enable production-grade algorithm transfer with calibration and validation.*

- [ ] Per-pod calibration phase infrastructure (issue #305)
- [ ] Wire calibration into `pipeline.yaml` and extend `deploy.py collect` (issue #306)
- [ ] Capacity probe extractor reads `acceleratorType.labelValues` (issue #270)
- [ ] Interactive size probe before parallel dispatch (issue #207)

---

## Contributor Focus Areas

New contributors are especially welcome in these areas:

| Area | Entry Point | Skill Level |
|---|---|---|
| Documentation | Issues #619, #675, #700 | Beginner |
| Test coverage | Issues #631, #635 | Intermediate |
| kubectl abstraction | Issue #656 | Intermediate |
| deploy.py decomposition | Issue #654 | Advanced |

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions (coming soon — issue #620).

---

## Version History

| Date | Update |
|---|---|
| 2026-07-30 | Initial roadmap created by strategist agent |

---

*This roadmap is maintained as a living document. Open a PR or issue to suggest changes.*
