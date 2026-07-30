# sim2real Roadmap

> **Status**: Living document. Last updated: 2026-07-30 (session 2).
> This roadmap reflects current strategic priorities as assessed by the Hive strategist agent.
> Operators and contributors should adjust priorities as project needs evolve.

## Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) to production
[llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) scorer plugins.

The project is in **active development** with a small, focused team. The pipeline is functional
end-to-end and has reached 97%+ test coverage. Current focus is hardening, decomposition,
and open-source readiness.

### Strategic Health (2026-07-30)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Test coverage | ✅ 97%+ | Coverage enforcement added (PR #717 merged) |
| CI health | ✅ Stable | 7 dependency PRs merged today |
| Security PRs | 🟡 Pending | PR #699 (BuildKit), #697, #690 awaiting human merge |
| Documentation | 🟡 In progress | CONTRIBUTING (#729) and ROADMAP (#728) PRs filed |
| License | 🔴 **MISSING** | No LICENSE file — adoption blocker |
| Releases | 🔴 None | 0 GitHub releases since April 2026 |
| PR queue | 🟡 13 open | 6 hold-labeled, 2 with merge conflicts |
| Architecture | 🟡 Monolithic | deploy.py (170K lines), sim2real.py (109K lines) need decomposition |

---

## Immediate Actions (Before Next Session)

> These are **operator decisions** — agents cannot merge or create releases.

1. **🔴 Add Apache 2.0 LICENSE file** — CONTRIBUTING.md references it; it doesn't exist
2. **🟡 Merge PR #699** (BuildKit security) — clean, mergeable, no blockers
3. **🟡 Merge PR #729** (CONTRIBUTING.md) — clean, mergeable
4. **🟡 Sequence PR conflict**: Merge PR #721 (architect/proc) first, then scanner rebases PR #744
5. **🟡 Create v0.1.0 release tag** — pipeline is stable, coverage is high

---

## Phase 1: Hardening (Current — Near-Term)

*Goal: Eliminate known security risks, stabilize CI, and reduce technical debt.*

### Open-Source Readiness (Critical — Do First)
- [ ] **Add `LICENSE` file** (Apache 2.0) — CONTRIBUTING.md references it but file doesn't exist ⚠️
- [ ] **Create `v0.1.0` release tag** — pipeline functional, 97%+ coverage, ready for first tag
- [ ] Merge `CONTRIBUTING.md` PR #729 — contributor onboarding guide

### Security (Immediate)
- [x] Merge PR #648 — Closed (contained regression risk; PR #690 is the clean fix)
- [ ] Merge PR #699 — Replace privileged BuildKit pod with rootless alternative (clean, mergeable)
- [ ] Merge PR #697 — Harden `data-pvc-explorer` debug manifest
- [ ] Merge PR #690 — Remove cluster-wide pods permission from ClusterRole (hold-gated)
- [ ] Merge PR #731 — Document secrets:get scope and resourceNames hardening (clean, mergeable)

### CI & Quality
- [x] Merge PR #717 — Coverage enforcement added ✅ (merged 2026-07-30)
- [x] Merge PR #718 — Node.js 20 deprecation suppression ✅
- [ ] Fix lint failure on PR #706 (quality/test-deploy-helpers) — issue #719
- [ ] Resolve CI test list drift — issues #640, #632
- [ ] Clean up stale branches — 143 remote branches (87 stale worktree-issue/* branches)

### Documentation
- [ ] Merge CONTRIBUTING.md PR #729 — contributor onboarding (clean, mergeable)
- [ ] Merge ROADMAP.md PR #728 (this PR) — public roadmap
- [ ] Merge guide doc PRs #707, #710, #730 as a batch
- [ ] Remove retired `review.py` + dead `anthropic` dependency (issue #745)
- [ ] Close issue #620 once CONTRIBUTING.md is merged

### Architecture (proc.py Conflict)
- [ ] Merge PR #721 (architect/proc-consolidation) — subprocess abstraction layer
- [ ] Scanner rebases PR #744 on top of merged #721 (timeout fix)

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

---

## Version History

| Date | Update |
|---|---|
| 2026-07-30 | Session 2 update: strategic health table, immediate actions, proc.py conflict analysis, license/release gaps, Dependabot churn noted |
| 2026-07-30 | Initial roadmap created by strategist agent |

---

*This roadmap is maintained as a living document. Open a PR or issue to suggest changes.*
