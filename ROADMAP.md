# sim2real Roadmap

> **Status**: Living document. Last updated: 2026-07-30 (session 7).
> This roadmap reflects current strategic priorities as assessed by the Hive strategist agent.
> Operators and contributors should adjust priorities as project needs evolve.

## Overview

sim2real is a pipeline for transferring simulation-discovered routing algorithms from
[inference-sim](https://github.com/inference-sim/inference-sim) to production
[llm-d-inference-scheduler](https://github.com/llm-d/llm-d-inference-scheduler) scorer plugins.

The project is in **active development** with a small, focused team. The pipeline is functional
end-to-end and has reached 97%+ test coverage. The hardening phase continues with a large
backlog of security and documentation PRs pending human merge.

### Strategic Health (2026-07-30, session 7)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Test coverage | ✅ 97%+ | Enforced in CI; proc.py + deploy capacity tests added (76 new via quality/test-proc-deploy-capacity) |
| CI health | ✅ Stable | Scanner/fix-787 fixing lint failure in quality/test-proc-deploy-capacity; #750 (CLI tests) still pending CI |
| Security PRs | 🔴 **17 hold-gated sec branches** | 17 sec/* branches pending; all require human merge; #763/#765 already merged ✅ |
| Documentation | 🟡 23 guide branches open | 3 permanently broken (reference deleted blis-context.md); 20 clean doc fixes pending |
| License | 🔴 **MISSING** | No LICENSE file — open-source adoption blocker (in hold PR strategy/contributing-2026) |
| CONTRIBUTING.md | 🔴 **MISSING** | Only in hold-gated strategy/contributing-2026 PR |
| ROADMAP.md | 🔴 **MISSING** | Only in hold-gated strategy/roadmap-2026 PR (this file) |
| Releases | 🔴 None | 0 GitHub releases since April 2026 — ecosystem cannot pin stable versions |
| PR queue | 🟡 **~60 open branches** | 17 sec + 23 guide + 9 scanner + 5 quality + 4 architect + 2 strategy; hive delivery pipeline active |
| Architecture | 🟡 Decomposition progressing | #721 (proc.py), #746 (deploy imports), #774 (ResolveError rename) all merged ✅; 3 architect branches remain |
| Branch hygiene | 🟡 178 branches | scanner/fix-review-cleanup + architect/rename-translation-resolveerror superseded; 3 blis-context branches broken |
| Hive infra | ✅ **Recovered** | TLS cert issue resolved; 17 PRs merged since session 6 (sessions 5-6 backlog cleared) |

---

## Immediate Actions (Before Next Session)

> These are **operator decisions** — agents cannot merge or create releases.

### Critical (Blockers)

1. **🔴 Merge hold-gated CONTRIBUTING.md + LICENSE PR** (strategy/contributing-2026) — both files still missing from main; unblocks contributor adoption and v0.1.0 release
2. **🔴 Merge hold-gated ROADMAP.md PR** (strategy/roadmap-2026, this file) — public roadmap visibility
3. **🔴 Triage 3 broken guide branches** — `guide/docs-blis-context`, `guide/docs-blis-context-709`, `guide/docs-link-blis-context` all reference deleted blis-context.md; close these PRs rather than merging

### High Priority (Security)

4. **🟡 Merge sec/fix-rbac-clusterrole-pods-list-only** — drops pods:get from ClusterRole (IS #646)
5. **🟡 Merge sec/fix-buildkit-pin-and-drop-privileged** — pins moby/buildkit + drops privileged:true; IS #780 (supersedes sec/fix-buildkit-privileged — close that one)
6. **🟡 Merge sec/fix-rbac-secrets-scope** — restricts secrets:get to known resourceNames; IS #767
7. **🟡 Merge sec/fix-data-pvc-explorer** — hardens data-pvc-explorer debug manifest
8. **🟡 Merge sec/fix-kubectl-pin** — pin kubectl version in Dockerfile
9. **🟡 Merge sec/fix-dockerfile-root** — non-root USER (if not superseded by merged PR #647)

### Recommended

10. **🟡 Create v0.1.0 release tag** — pipeline stable, 97%+ coverage; requires CONTRIBUTING.md + LICENSE merged first
11. **🟡 Merge scanner/fix-787-lint-unused-import** — fixes CI lint; unblocks quality/test-proc-deploy-capacity (76 new tests)
12. **🟡 Merge quality/test-sim2real-cli-errors** (PR #750) — 30 CLI error-path tests; ruff lint fix committed
13. **🟡 Close superseded branches**: `scanner/fix-review-cleanup` (superseded by #747), `architect/rename-translation-resolveerror` (superseded by #774)
14. **🟡 Batch-merge guide doc PRs** — 20 guide branches with clean doc fixes pending merge

---

## Phase 1: Hardening (Current — Near-Term)

*Goal: Eliminate known security risks, stabilize CI, and reduce technical debt.*

### Open-Source Readiness (Critical — Do First)
- [ ] **Merge LICENSE + CONTRIBUTING.md** (strategy/contributing-2026, hold-gated) — both files missing from main; unblocks contributor adoption
- [ ] **Merge ROADMAP.md** (strategy/roadmap-2026, this PR, hold-gated) — public roadmap visibility
- [ ] **Create `v0.1.0` release tag** — pipeline functional, 97%+ coverage, ready for first tag

### Security (17 Pending sec/* Branches)
- [x] Merge PR #648 — Closed (contained regression risk)
- [x] Merge PR #731 — RBAC secrets:get scope documentation ✅ (merged 2026-07-30)
- [x] Merge PR #763 — Pin Helm to v4.2.3 ✅ (merged 2026-07-30)
- [x] Merge PR #765 — Pin anthropics/claude-plugins-official ✅ (merged 2026-07-30)
- [ ] Merge sec/fix-rbac-clusterrole-pods-list-only — Drop pods:get from ClusterRole (IS #646)
- [ ] Merge sec/fix-buildkit-pin-and-drop-privileged — Pin moby/buildkit + drop privileged:true (IS #780; supersedes sec/fix-buildkit-privileged)
- [ ] Merge sec/fix-data-pvc-explorer — Harden data-pvc-explorer debug manifest
- [ ] Merge sec/fix-rbac-secrets-scope — Restrict secrets:get to known resourceNames (IS #767)
- [ ] Merge sec/fix-kubectl-pin — Pin kubectl version
- [ ] Merge sec/fix-dockerfile-root — non-root USER directive (IS #645)
- [ ] Merge sec/fix-rbac-cluster, sec/fix-rbac-pods-clusterscope, sec/fix-rbac-secrets-restrict — batch RBAC review
- [ ] Merge sec/fix-subprocess-timeouts (IS #694 — 10 files; already partially addressed by #755)
- [ ] Merge sec/fix-supply-chain-hardening, sec/pin-github-actions-sha — supply chain hardening
- [ ] Merge sec/fix-helm-pin — helm version pin

### CI & Quality
- [x] Merge PR #717 — Coverage enforcement ✅
- [x] Merge PR #718 — Node.js 20 deprecation fix ✅
- [x] Merge PR #751 — Python 3.14 upgrade ✅
- [x] Merge PR #706 — deploy.py helper tests ✅
- [x] Merge PR #721 — proc.py consolidation ✅ (merged 2026-07-30)
- [x] Merge PR #747 — Remove retired review.py + dead anthropic dep ✅ (merged 2026-07-30)
- [x] Merge PR #758 — Remove stale go.work ✅ (merged 2026-07-30)
- [ ] Merge scanner/fix-787-lint-unused-import — fixes ruff lint failure in quality/test-proc-deploy-capacity
- [ ] Merge quality/test-proc-deploy-capacity (PR #787) — 76 tests for proc.py + deploy capacity dispatch
- [ ] Merge quality/test-sim2real-cli-errors (PR #750) — 30 CLI error-path tests; ruff lint fix committed
- [ ] Merge quality/test-resolve-100pct-deploy-gaps — additional coverage gap closures
- [ ] Merge quality/test-setup-and-reset-fix — setup + reset coverage
- [ ] Clean up blis-context guide branches — `guide/docs-blis-context`, `docs-blis-context-709`, `docs-link-blis-context` all reference deleted file; close rather than merge
- [ ] Close superseded branches: `scanner/fix-review-cleanup` (by #747), `architect/rename-translation-resolveerror` (by #774)

### Documentation
- [ ] Merge CONTRIBUTING.md (strategy/contributing-2026, hold-gated)
- [ ] Merge ROADMAP.md (strategy/roadmap-2026, this PR, hold-gated)
- [ ] Batch-merge 20 guide doc PRs — no-conflict documentation fixes
- [ ] Close/rebase guide/docs-proc-module (may reference old proc.py location)

### Architecture
- [x] Merge PR #721 — proc.py consolidation ✅ (merged 2026-07-30)
- [x] Merge PR #746 — deploy deferred imports ✅ (merged 2026-07-30)
- [x] Merge PR #774 — TranslationResolveError rename ✅ (merged 2026-07-30)
- [ ] Merge architect/consolidate-repo-root — layout.repo_root() centralization (IS #657)
- [ ] Merge architect/proc-consolidation — remaining subprocess refactors (check if superseded)
- [ ] Merge architect/deploy-deferred-imports — check if superseded by #746
- [ ] Merge scanner/fix-proc-timeout-721 — subprocess timeout additions (built on proc.py refactor)

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
| Documentation | Batch-merge 20 pending guide PRs | Beginner |
| Test coverage | quality/test-sim2real-cli-errors (#750) | Intermediate |
| kubectl abstraction | Issue #656 | Intermediate |
| deploy.py decomposition | Issue #654 | Advanced |

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions.

---

## Version History

| Date | Update |
|---|---|
| 2026-07-30 | Session 7 update: TLS cert issue resolved; 17 PRs merged (#721, #731, #746-#748, #752, #754-#756, #758-#759, #763-#765, #769-#770, #773-#775); anthropic dep removed; go.work removed; review.py removed; architect PRs #746+#774 merged; 60 branches remain (~17 sec, 23 guide, 9 scanner, 5 quality, 4 arch, 2 strategy) |
| 2026-07-30 | Session 6 update: 31 open PRs (16 hold-gated), 0 auto-merges since session 5 (TLS cert IS #786), new PRs #783 (buildkit pin) and #784 (ClusterRole pods:get drop), IS #789-#791 filed, v0.1.0 readiness criteria clarified |
| 2026-07-30 | Session 5 update: hive infrastructure degradation (TLS cert), 28 open PRs (14 hold-gated), blis-context.md deletion cascades, sec/fix-clusterrole-pods new security branch, proc.py sequencing clarified, branch hygiene analysis |
| 2026-07-30 | Session 2 update: strategic health table, immediate actions, proc.py conflict analysis, license/release gaps |
| 2026-07-30 | Initial roadmap created by strategist agent |

---

*This roadmap is maintained as a living document. Open a PR or issue to suggest changes.*
