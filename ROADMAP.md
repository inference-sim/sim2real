# sim2real Roadmap

> **Status**: Living document. Last updated: 2026-07-30 (session 6).
> This roadmap reflects current strategic priorities as assessed by the Hive strategist agent.
> Operators and contributors should adjust priorities as project needs evolve.

## Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) to production
[llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) scorer plugins.

The project is in **active development** with a small, focused team. The pipeline is functional
end-to-end and has reached 97%+ test coverage. The hardening phase continues with a large
backlog of security and documentation PRs pending human merge.

### Strategic Health (2026-07-30, session 6)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Test coverage | ✅ 97%+ | Enforced in CI since PR #717 |
| CI health | ✅ Stable | Python 3.14, dep bumps, coverage enforcement all merged |
| Security PRs | 🔴 **16 hold-gated** | 16 hold-labeled PRs require human triage; new: #783 (buildkit pin), #784 (ClusterRole pods:get drop) |
| Documentation | 🟡 11 guide PRs open | Merged several guide PRs; 3 still obsolete (reference deleted blis-context.md) |
| License | 🔴 **MISSING** | No LICENSE file — open-source adoption blocker (PR #729 has it) |
| CONTRIBUTING.md | 🔴 **MISSING** | Only in hold-gated PR #729 |
| ROADMAP.md | 🔴 **MISSING** | Only in hold-gated PR #728 (this file) |
| Releases | 🔴 None | 0 GitHub releases since April 2026 — ecosystem cannot pin stable versions |
| PR queue | 🔴 **31 open PRs** | 16 hold-labeled, 15 non-hold (14 merge-eligible per hive, but all blocked by TLS) |
| Architecture | 🟡 Decomposition underway | 4 architect PRs open (#721, #746, #772, #774); proc.py ready to merge |
| Branch hygiene | 🔴 175 branches | 4 superseded sec branches; 3 broken guide branches (reference deleted blis-context.md) |
| Hive infra | 🔴 **Degraded** | PR-request watcher and auto-merge both broken (TLS cert — IS #786); **0 auto-merges since session 5** |

---

## Immediate Actions (Before Next Session)

> These are **operator decisions** — agents cannot merge or create releases.

### Critical (Blockers)

1. **🔴 Fix hive infrastructure TLS cert issue (IS #786)** — All 15 non-hold CI-passing PRs are blocked from auto-merge; hive PR-request watcher and merge-eligibility checks both fail with TLS cert error. See also IS #782.
2. **🔴 Merge PR #729** (CONTRIBUTING.md + Apache 2.0 LICENSE) — Hold-gated; merge to unblock open-source adoption and v0.1.0 release
3. **🔴 Merge PR #728** (ROADMAP.md, this file) — Hold-gated; merge to establish public roadmap

### High Priority (Security)

4. **🟡 Merge PR #784** (RBAC: drop pods:get from ClusterRole) — addresses IS #646; mergeable
5. **🟡 Merge PR #783** (pin moby/buildkit + drop privileged:true) — addresses IS #780; mergeable
6. **🟡 Merge PR #768** (restrict secrets:get to resourceNames) — addresses IS #767; hold-gated
7. **🟡 Merge PR #765** (pin anthropics/claude-plugins-official) — addresses IS #761; hold-gated
8. **🟡 Merge PR #763** (pin Helm to v4.2.3) — addresses IS #762; hold-gated
9. **🟡 Merge PR #699** (replace privileged BuildKit pod) — hold-gated security fix; clean

### Recommended

10. **🟡 Create v0.1.0 release tag (IS #790)** — pipeline stable, 97%+ coverage; requires #729 merged first
11. **🟡 Close PR #766 + delete broken guide branches (IS #789)** — reference deleted blis-context.md
12. **🟡 Delete 4 superseded sec branches (IS #791)**: `sec/fix-supply-chain-hardening`, `sec/pin-claude-action-supply-chain`, `sec/pin-github-actions-sha`, `scanner/fix-claude-supply-chain`
13. **🟡 Sequence architect/scanner merge**: #721 (proc.py) → #744 (timeout fix)
14. **🟡 Batch-merge guide doc PRs** #752, #754, #756, #759, #764, #769, #770, #775, #776 — all no-conflict docs fixes

---

## Phase 1: Hardening (Current — Near-Term)

*Goal: Eliminate known security risks, stabilize CI, and reduce technical debt.*

### Open-Source Readiness (Critical — Do First)
- [ ] **Merge LICENSE + CONTRIBUTING.md** (PR #729, hold-gated) — contains both files; unblocks contributor adoption
- [ ] **Merge ROADMAP.md** (PR #728, this file) — public roadmap visibility
- [ ] **Create `v0.1.0` release tag** — pipeline functional, 97%+ coverage, ready for first tag

### Security (16 Hold-Gated PRs)
- [x] Merge PR #648 — Closed (contained regression risk)
- [x] Merge PR #731 — RBAC secrets:get scope documentation ✅ (merged 2026-07-30)
- [ ] Merge PR #784 — Drop pods:get from ClusterRole (mergeable; IS #646)
- [ ] Merge PR #783 — Pin moby/buildkit + drop privileged:true (mergeable; IS #780)
- [ ] Merge PR #699 — Replace privileged BuildKit pod with rootless alternative (hold-gated)
- [ ] Merge PR #697 — Harden `data-pvc-explorer` debug manifest (hold-gated)
- [ ] Merge PR #768 — Restrict secrets:get to known resourceNames (hold-gated; IS #767)
- [ ] Merge PR #765 — Pin anthropics/claude-plugins-official to commit SHA (hold-gated; IS #761)
- [ ] Merge PR #763 — Pin Helm to v4.2.3 in Dockerfile (hold-gated; IS #762)
- [ ] Other sec/* branches (#758 go.work, #755 subprocess timeouts, etc.) — batch review

### CI & Quality
- [x] Merge PR #717 — Coverage enforcement ✅
- [x] Merge PR #718 — Node.js 20 deprecation fix ✅
- [x] Merge PR #751 — Python 3.14 upgrade ✅
- [x] Merge PR #706 — deploy.py helper tests ✅
- [ ] Fix CI failure on PR #750 (quality/test-sim2real-cli-errors) — PR #781 (lint fix) is hold-gated; merge #781 first
- [ ] Merge PR #750 after CI fix — 30 new CLI error-path tests
- [ ] Clean up dead merged branches — 20 already-merged branches still present (10 worktree-issue, 10 non-worktree)
- [ ] Clean up blis-context guide branches — `guide/docs-blis-context`, `docs-blis-context-709`, `docs-link-blis-context` all reference deleted file; close PR #766

### Documentation
- [ ] Merge CONTRIBUTING.md PR #729 (hold-gated)
- [ ] Merge ROADMAP.md PR #728 (this PR, hold-gated)
- [ ] Batch-merge guide doc PRs: #752, #754, #756, #759, #764, #769, #770 (all no-conflict)
- [ ] Merge PR #775 (errors.py library table sync) and #774/#772 (architect refactors)
- [ ] Remove retired `review.py` + dead `anthropic` dependency (PR #747, hold-gated; IS #745)

### Architecture (proc.py Sequencing)
- [ ] Merge PR #721 (architect/proc-consolidation) — subprocess abstraction layer
  - PR #744 (scanner/fix-proc-timeout-721) is BUILT ON TOP of #721 — merge #721 first
- [ ] Merge PR #744 after #721 merges — timeout fix applies cleanly
- [ ] Merge PR #746 (deploy deferred imports refactor)
- [ ] Merge PR #772/#774 (layout.repo_root + ResolveError disambiguation)

---

## Phase 2: Decomposition (Mid-Term — 1–3 Months)

*Goal: Break apart the monolithic pipeline modules to enable parallel development, better testability, and faster iteration.*

### Architecture Refactoring
- [ ] **kubectl abstraction layer** (issue #656) — Replace 58+ raw subprocess calls in `deploy.py`, `setup.py`, `cluster.py` with a typed `kubectl.py` module
  - Unblocks: reliable timeout enforcement, subprocess security hardening
  - Effort: ~1 week
- [ ] **deploy.py decomposition** (issue #654) — Split 170K-line monolith into modules by subcommand
  - Effort: ~1.5 weeks
- [ ] **sim2real.py decomposition** (issue #658) — Resolve 24 deferred imports, extract to modules
  - Effort: ~1 week
- [ ] **REPO_ROOT centralization** (issue #657) — Replace 7× fragile parent-chain traversal with single layout.repo_root()
  - **PR #772 in progress** — architect working on this; mergeable once hold-queue clears
  - Effort: ~2 days

### Branch Hygiene (Operational)
- [ ] Delete 20 already-merged dead branches (10 worktree-issue, 10 named branches)
- [ ] Delete/close 3 blis-context guide branches (reference deleted file)
- [ ] Audit sec/* branches for duplicates (sec/fix-rbac-cluster is superseded by sec/fix-clusterrole-pods)
- [ ] Consider archiving worktree-issue-* namespace (88 branches, most stale)

### Feature Work
- [ ] Round-trip integration test: `translation register --build` → `sim2real assemble` (issue #592)
- [ ] Epic: Step-6 validate/execute + auto-fix (issue #534)
- [ ] Per-iteration `wipe` subcommand consolidation (issue #168)

---

## Phase 3: Scale & Reliability (Mid-Term — 2–4 Months)

*Goal: Make the pipeline robust for multi-team, multi-cluster operations.*

### Reliability
- [ ] Orchestrator ConfigMap save/restore — prevent clobber of out-of-band edits (issue #450)
- [ ] Classify infra-caused `PipelineRun Failed` and route to retry (issue #567)
- [ ] Data PVC path scoping by scenario (issue #553)
- [ ] Auto-prune stale progress-dict entries (issue #554)

### Observability
- [ ] Stale orchestrator image warning when `--remote` is used (issue #376)
- [ ] GPU capacity probe: per-node fragmentation for multi-GPU pods (issue #262)

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
| Documentation | Issues #700, #732, #675 | Beginner |
| Test coverage | PR #750 (needs CI fix) | Intermediate |
| kubectl abstraction | Issue #656 | Intermediate |
| deploy.py decomposition | Issue #654 | Advanced |

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

---

## Version History

| Date | Update |
|---|---|
| 2026-07-30 | Session 6 update: 31 open PRs (16 hold-gated), 0 auto-merges since session 5 (TLS cert IS #786), new PRs #783 (buildkit pin) and #784 (ClusterRole pods:get drop), IS #789-#791 filed, v0.1.0 readiness criteria clarified |
| 2026-07-30 | Session 5 update: hive infrastructure degradation (TLS cert), 28 open PRs (14 hold-gated), blis-context.md deletion cascades, sec/fix-clusterrole-pods new security branch, proc.py sequencing clarified, branch hygiene analysis |
| 2026-07-30 | Session 2 update: strategic health table, immediate actions, proc.py conflict analysis, license/release gaps |
| 2026-07-30 | Initial roadmap created by strategist agent |

---

*This roadmap is maintained as a living document. Open a PR or issue to suggest changes.*
