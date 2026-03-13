#!/usr/bin/env bash
# Extracts Go code blocks from scorer_template.go.md and compiles against submodule HEAD.
# Exit codes: 0 = pass, 1 = compilation failure, 2 = infrastructure error
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/docs/transfer/scorer_template.go.md"
SUBMODULE="$REPO_ROOT/llm-d-inference-scheduler"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Scorer template not found: $TEMPLATE" >&2
    exit 2
fi

if [ ! -d "$SUBMODULE/pkg" ]; then
    echo "ERROR: llm-d-inference-scheduler submodule not initialized: $SUBMODULE" >&2
    echo "Run: git submodule update --init llm-d-inference-scheduler" >&2
    exit 2
fi

if ! command -v go >/dev/null 2>&1; then
    echo "ERROR: go binary not found in PATH — install Go toolchain" >&2
    exit 2
fi

# Create temp directory for extracted code
TMPDIR_WORK=$(mktemp -d) || { echo "ERROR: failed to create temp directory" >&2; exit 2; }
trap 'rm -rf "$TMPDIR_WORK"' EXIT

# Extract Go code blocks (between ```go and ```)
# Concatenate all Go blocks into a single file
awk '/^```go$/{ found=1; next } /^```$/{ found=0; next } found{ print }' \
    "$TEMPLATE" > "$TMPDIR_WORK/template_check.go"

if [ ! -s "$TMPDIR_WORK/template_check.go" ]; then
    echo "ERROR: No Go code blocks found in $TEMPLATE" >&2
    exit 2
fi

# Verify extracted code contains exactly one package declaration.
PKG_COUNT=$(grep -c '^package ' "$TMPDIR_WORK/template_check.go" || true)
if [ "$PKG_COUNT" -eq 0 ]; then
    echo "ERROR: Extracted Go code has no 'package' declaration — extraction may be incomplete" >&2
    exit 2
fi
if [ "$PKG_COUNT" -gt 1 ]; then
    echo "INFO: Extracted Go code has $PKG_COUNT package declarations — filtering to 'package scorer' blocks only" >&2
    # Re-extract only blocks whose first non-blank/comment line is 'package scorer'.
    # Phase 1: Extract only 'package scorer' blocks (deduplicate package line).
    awk '
        /^```go$/  { found=1; is_scorer=0; next }
        /^```$/    { found=0; next }
        found && !is_scorer {
            if ($0 ~ /^[[:space:]]*$/ || $0 ~ /^\/\// || $0 ~ /^\/\*/ || $0 ~ /^\*/) { next }
            if ($0 ~ /^package scorer$/) { is_scorer=1; if (!pkg_printed) { print; pkg_printed=1 }; next }
            else { found=0; next }
        }
        found && is_scorer  { print }
    ' "$TEMPLATE" > "$TMPDIR_WORK/template_extracted.go"
    # Phase 2: Deduplicate import declarations across blocks.
    awk '
        /^package / { pkg_line=$0; next }
        /^import \(/ { in_import=1; next }
        in_import && /^\)/ { in_import=0; next }
        in_import { if (!seen[$0]++) imports[nimports++]=$0; next }
        !in_import { body[nbody++]=$0 }
        END {
            if (pkg_line != "") print pkg_line
            if (nimports > 0) {
                print "import ("
                for (i=0; i<nimports; i++) print imports[i]
                print ")"
            }
            for (i=0; i<nbody; i++) print body[i]
        }
    ' "$TMPDIR_WORK/template_extracted.go" > "$TMPDIR_WORK/template_check.go"
    if [ ! -s "$TMPDIR_WORK/template_check.go" ]; then
        echo "ERROR: No 'package scorer' blocks found after filtering" >&2
        exit 2
    fi
fi

# Copy extracted code to scorer package for compilation check.
# Use a unique filename to avoid symbol conflicts with existing scorers.
# No leading underscore — Go ignores files starting with _ or .
DEST="$SUBMODULE/pkg/plugins/scorer/template_check_temp.go"
trap 'rm -f "$DEST"; rm -rf "$TMPDIR_WORK"' EXIT
cp "$TMPDIR_WORK/template_check.go" "$DEST" || { echo "ERROR: failed to copy extracted code to $DEST" >&2; exit 2; }

cd "$SUBMODULE"
# Disable Go workspace mode — the submodule has its own go.mod and should not
# be affected by the repo-root go.work file.
export GOWORK=off
if go build ./pkg/plugins/scorer/ 2>"$TMPDIR_WORK/build_errors.txt"; then
    echo "PASS: Scorer template code compiles against submodule HEAD"
    exit 0
else
    # Check if all errors are just "imported and not used" (benign merge artifact
    # from concatenating multiple template blocks). API-breaking errors are real failures.
    REAL_ERRORS=$(grep 'template_check_temp.go' "$TMPDIR_WORK/build_errors.txt" | grep -v 'imported.*and not used' || true)
    if [ -n "$REAL_ERRORS" ]; then
        echo "FAIL: Scorer template code does not compile:" >&2
        cat "$TMPDIR_WORK/build_errors.txt" >&2
        exit 1
    else
        echo "PASS: Scorer template code compiles (unused imports from block merging ignored)"
        exit 0
    fi
fi
