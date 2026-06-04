# Issue #186: build-epp.sh — Set GHCR Package Visibility to Public Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After `build-epp.sh` pushes the EPP image to GHCR for the first time, set the package visibility to public so cluster pods can pull without imagePullSecrets.

**Architecture:** Add a best-effort post-push step inside `pipeline/scripts/build-epp.sh`. The step runs only when the registry is `ghcr.io`, attempts the org-scoped visibility API first then falls back to the user-scoped one, and uses `|| true` semantics so any failure (no `gh` auth, already-public package, nested package paths) does not abort the build. Quay.io and other registries are skipped — Quay manages visibility via web UI per repository.

**Tech Stack:** Bash, `gh` CLI (GitHub REST API).

**Closes:** #186

---

## File Structure

- Modify: `pipeline/scripts/build-epp.sh` — insert a new step between the existing Step 5 (build wait, ends ~line 205) and Step 6 (build pod cleanup, ~line 207–208).

No tests are added: the change is a small best-effort shell snippet that gates on registry host and uses `|| true`. There is no existing test harness for `pipeline/scripts/*.sh`, and the surrounding script is not unit-tested. CI's `ruff` + `pytest` continue to apply unchanged.

---

## Task 1: Add GHCR public-visibility post-push step to build-epp.sh

**Files:**
- Modify: `pipeline/scripts/build-epp.sh` (insert after current Step 5 break, before current Step 6 cleanup)

**Acceptance criteria (from issue #186):**
- After a successful push to a `ghcr.io` registry, the script attempts to set the package visibility to public.
- The attempt is non-fatal: any failure (gh not authenticated, package already public, parsing edge case) emits a warning but does not abort the script.
- Non-GHCR registries (Quay.io, others) are silently skipped — the script's behavior for them is unchanged.
- Both org-owned and user-owned GHCR packages are handled.

- [ ] **Step 1: Read the current build-epp.sh to confirm insertion point**

Run: `sed -n '205,215p' pipeline/scripts/build-epp.sh`
Expected: shows the `done` of the build-wait loop, blank line, the `# ── Step 6: Clean up build pod ────` header, and the `kubectl delete pod "${BUILD_POD}"` line.

- [ ] **Step 2: Insert the new step**

Use `Edit` to replace the block:

```bash
done

# ── Step 6: Clean up build pod ─────────────────────────────────────
kubectl delete pod "${BUILD_POD}" -n "${NAMESPACE}" --ignore-not-found --force --grace-period=0 2>/dev/null || true

# ── Step 7: Update metadata ───────────────────────────────────────
```

with:

```bash
done

# ── Step 6: Set GHCR package visibility to public (best-effort) ────
# GHCR creates new packages with private visibility (inherited from the
# repository). Cluster pods then fail with ImagePullBackOff because no
# imagePullSecret is wired onto their service account. Setting the package
# to public is a one-time per-package operation; subsequent pushes are a
# no-op. Non-GHCR registries (e.g. Quay.io) manage visibility differently
# and are skipped.
#
# Best-effort: a failure here (gh not authenticated on the host running
# this script, package already public, unusual nested path) is non-fatal.
# The user will see ImagePullBackOff later and can fix visibility manually.
IMAGE_NO_TAG="${FULL_IMAGE%:*}"
REGISTRY_HOST="${IMAGE_NO_TAG%%/*}"
if [ "${REGISTRY_HOST}" = "ghcr.io" ]; then
  REST="${IMAGE_NO_TAG#ghcr.io/}"
  if [[ "${REST}" == */* ]]; then
    PKG_OWNER="${REST%%/*}"
    PKG_NAME="${REST#*/}"
    info "Setting GHCR package ${PKG_OWNER}/${PKG_NAME} visibility=public..."
    if gh api --method PUT "/orgs/${PKG_OWNER}/packages/container/${PKG_NAME}/visibility" \
         -f visibility=public >/dev/null 2>&1; then
      ok "GHCR package set to public (org-owned)"
    elif gh api --method PUT "/user/packages/container/${PKG_NAME}/visibility" \
         -f visibility=public >/dev/null 2>&1; then
      ok "GHCR package set to public (user-owned)"
    else
      warn "Could not set GHCR package visibility automatically."
      warn "If pods fail with ImagePullBackOff, run one of:"
      warn "  gh api --method PUT /orgs/${PKG_OWNER}/packages/container/${PKG_NAME}/visibility -f visibility=public"
      warn "  gh api --method PUT /user/packages/container/${PKG_NAME}/visibility -f visibility=public"
    fi
  fi
fi

# ── Step 7: Clean up build pod ─────────────────────────────────────
kubectl delete pod "${BUILD_POD}" -n "${NAMESPACE}" --ignore-not-found --force --grace-period=0 2>/dev/null || true

# ── Step 8: Update metadata ───────────────────────────────────────
```

Note: existing Step 6 becomes Step 7, existing Step 7 becomes Step 8. Renumber comment headers only — no logic changes.

- [ ] **Step 3: Verify syntax with `bash -n`**

Run: `bash -n pipeline/scripts/build-epp.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Verify the renumbering and snippet placement**

Run: `grep -n "^# ── Step" pipeline/scripts/build-epp.sh`
Expected: shows Steps 1, 2, 3, 4, 5, 6 (visibility), 7 (cleanup), 8 (metadata) in order.

- [ ] **Step 5: Run lint and tests**

Run: `ruff check pipeline/ .claude/skills/ --select F`
Expected: `All checks passed!` (no Python files changed).

Run: `python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-translate/tests/ -v 2>&1 | tail -20`
Expected: all tests pass (this change does not touch any Python).

- [ ] **Step 6: Commit**

```bash
git add pipeline/scripts/build-epp.sh docs/superpowers/plans/2026-06-04-issue-186-ghcr-public-visibility.md
git commit -m "$(cat <<'EOF'
fix(build-epp): set pushed GHCR package to public so pods can pull

GHCR creates new packages with private visibility (inherited from repo
settings). The cluster's pods then fail with ImagePullBackOff because no
imagePullSecret is wired onto their service account.

Add a best-effort post-push step that calls the GHCR visibility API for
the just-pushed package. The step is GHCR-only (Quay.io and other
registries manage visibility differently and are skipped), tries the
org-scoped endpoint first then the user-scoped one, and is non-fatal so
a missing gh auth or already-public package does not abort the build.

Closes #186
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Issue's "Proposed Fix" — add post-push visibility step ✓ (Task 1, Step 2)
- Issue's `|| true` semantics ("convenience step, non-fatal") ✓ (each `gh api` call falls through; warning only)
- Refinements identified during vet:
  - GHCR-only gating ✓ (`if [ "${REGISTRY_HOST}" = "ghcr.io" ]`)
  - Org vs. user packages ✓ (org endpoint tried first, user fallback)
- Both code paths in `build-epp.sh` (`--image-ref` direct vs. metadata-derived) ✓ — implementation parses `FULL_IMAGE`, which is set in both branches.

**Placeholder scan:** No TBDs, no "appropriate error handling," no "similar to Task N." The full snippet is in the plan.

**Type consistency:** N/A — single-file shell change with no new types or signatures.

**Edge cases handled:**
- Non-GHCR registry → entire block skipped.
- GHCR with no `/` in path (e.g., `ghcr.io/foo` with no package segment, implausible but defensive) → inner `[[ "${REST}" == */* ]]` guard skips.
- `gh` not authenticated → both API calls fail, fall through to warn-only.
- Package already public → API typically returns 200/204 even if already public; if it fails for any reason, warn-only.
- Nested package paths (`ghcr.io/owner/sub/pkg`) → `PKG_NAME` ends up `sub/pkg`. The visibility API requires URL-encoded slashes; this case will fail and emit the warning. Acceptable: the user can run the printed command (where they may need to URL-encode) manually. Not in scope to handle nested paths automatically.
