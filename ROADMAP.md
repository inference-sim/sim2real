# sim2real Roadmap

> **Status**: Living document. Last updated: 2026-07-30 (session 5).
> This roadmap reflects current strategic priorities as assessed by the Hive strategist agent.
> Operators and contributors should adjust priorities as project needs evolve.

## Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) to production
[llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) scorer plugins.

The project is in **active development** with a small, focused team. The pipeline is functional
end-to-end and has reached 97%+ test coverage. The hardening phase continues with a large
backlog of security and documentation PRs pending human merge.

### Strategic Health (2026-07-30, session 5)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Test coverage | ✅ 97%+ | Enforced in CI since PR #717 |
| CI health | ✅ Stable | Python 3.14, dep bumps, coverage enforcement all merged |
| Security PRs | 🔴 **14 hold-gated** | 14 hold-labeled PRs require human triage and merge |
| Documentation | 🟡 21 guide PRs open | 21 unmerged guide branches; 3 now obsolete (reference deleted blis-context.md) |
| License | 🔴 **MISSING** | No LICENSE file — open-source adoption blocker (PR #729 has it) |
| CONTRIBUTING.md | 🔴 **MISSING** | Only in hold-gated PR #729 |
| ROADMAP.md | 🔴 **MISSING** | Only in hold-gated PR #728 (this file) |
| Releases | 🔴 None | 0 GitHub releases since April 2026 — ecosystem cannot pin stable versions |
| PR queue | 🔴 28 open PRs | 14 hold-labeled, 12 marked merge-eligible (hive infra checks broken) |
| Architecture | 🟡 Decomposition underway | 3 architect branches open; proc.py consolidated |
| Branch hygiene | 🔴 170 branches | 68 unmerged non-worktree branches; 20 dead merged branches not yet deleted |
| Hive infra | 🔴 **Degraded** | PR-request watcher and merge-eligible checks both broken (TLS cert issue) |

---

## Immediate Actions (Before Next Session)

> These are **operator decisions** — agents cannot merge or create releases.

### Critical (Blockers)

1. **🔴 Fix hive infrastructure TLS cert issue** — PR-request watcher and merge-eligibility checks fail with "tls: certificate signed by unknown authority"; blocks all hive-automated PR creation and CI status reporting. Manual PR opens affected.
2. **🔴 Merge PR #729** (CONTRIBUTING.md + Apache 2.0 LICENSE) — Hold-gated; merge to unblock open-source adoption
3. **🔴 Merge PR #728** (ROADMAP.md, this file) — Hold-gated; merge to establish public roadmap

### High Priority (Security)

4. **🟡 Merge PR #699** (BuildKit: rootless pod) — hold-gated security fix; clean, no blockers
5. **🟡 Merge PR #768** (RBAC secrets:get resourceNames) — hold-gated security hardening
6. **🟡 Merge PR #765** (claude-plugins SHA pin) — hold-gated supply chain fix
7. **🟡 Merge PR #763** (Helm pin in Dockerfile) — hold-gated supply chain fix
8. **🟡 Merge sec/fix-clusterrole-pods** (remove cluster-wide pods from ClusterRole) — fresh PR needed or merge the branch directly

### Recommended

9. **🟡 Create v0.1.0 release tag** — pipeline is stable, 97%+ coverage, ready for first tag
10. **🟡 Close PR #766** (guide/docs-blis-context) — references deleted blis-context.md; will introduce broken links
11. **🟡 Sequence architect/scanner merge**: Merge PR #721 (proc-consolidation) first, then PR #744 (timeout fix) applies cleanly on top
12. **🟡 Batch-merge guide doc PRs** #752, #754, #756, #759 — all no-conflict docs fixes

---

## Phase 1: Hardening (Current — Near-Term)

*Goal: Eliminate known security risks, stabilize CI, and reduce technical debt.*

### Open-Source Readiness (Critical — Do First)
- [ ] **Merge LICENSE + CONTRIBUTING.md** (PR #729, hold-gated) — contains both files; unblocks contributor adoption
- [ ] **Merge ROADMAP.md** (PR #728, this file) — public roadmap visibility
- [ ] **Create `v0.1.0` release tag** — pipeline functional, 97%+ coverage, ready for first tag

### Security (14 Hold-Gated PRs)
- [x] Merge PR #648 — Closed (contained regression risk)
- [x] Merge PR #731 — RBAC secrets:get scope documentation ✅ (merged 2026-07-30)
- [ ] Merge PR #699 — Replace privileged BuildKit pod with rootless alternative (hold-gated)
- [ ] Merge PR #697 — Harden `data-pvc-explorer` debug manifest (hold-gated)
- [ ] Merge PR #768 — Restrict secrets:get to known resourceNames (hold-gated)
- [ ] Merge PR #765 — Pin anthropics/claude-plugins-official to commit SHA (hold-gated)
- [ ] Merge PR #763 — Pin Helm to v4.2.3 in Dockerfile (hold-gated)
- [ ] Open PR for sec/fix-clusterrole-pods — Remove cluster-wide pods from ClusterRole (branch exists, no PR yet)
- [ ] Other sec/* branches (#758 go.work, #755 subprocess timeouts, etc.) — batch review

### CI & Quality
- [x] Merge PR #717 — Coverage enforcement ✅
- [x] Merge PR #718 — Node.js 20 deprecation fix ✅
- [x] Merge PR #751 — Python 3.14 upgrade ✅
- [x] Merge PR #706 — deploy.py helper tests ✅
- [ ] Fix CI failure on PR #750 (quality/test-sim2real-cli-errors) — currently CI-failing
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
| 2026-07-30 | Session 5 update: hive infrastructure degradation (TLS cert), 28 open PRs (14 hold-gated), blis-context.md deletion cascades, sec/fix-clusterrole-pods new security branch, proc.py sequencing clarified, branch hygiene analysis |
| 2026-07-30 | Session 2 update: strategic health table, immediate actions, proc.py conflict analysis, license/release gaps |
| 2026-07-30 | Initial roadmap created by strategist agent |

---

*This roadmap is maintained as a living document. Open a PR or issue to suggest changes.*
