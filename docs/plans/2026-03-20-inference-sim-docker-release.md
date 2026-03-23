# inference-sim Docker Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-stage Dockerfile and a GitHub Actions release workflow that builds and pushes `ghcr.io/inference-sim/blis:<tag>` on every `v*` tag push.

**Architecture:** A two-stage Docker build (Go builder → minimal Alpine runtime) produces a lean `blis` binary image. A dedicated `release.yml` workflow triggers exclusively on tag pushes, logs in to GHCR with the built-in `GITHUB_TOKEN`, and publishes both the exact version tag and a `latest` alias.

**Tech Stack:** Go 1.21, Docker multi-stage build, GitHub Actions, GitHub Container Registry (ghcr.io), `docker/build-push-action@v5`

> **Note:** All files in this plan live inside the `inference-sim/` submodule directory. The PR target is the `inference-sim` repository, not `sim2real`.

---

## File Map

| Action | Path (relative to inference-sim root) |
|--------|--------------------------------------|
| Create | `Dockerfile` |
| Create | `.github/workflows/release.yml` |

---

### Task 1: Write the Dockerfile

**Files:**
- Create: `Dockerfile`

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1

# ── Build stage ────────────────────────────────────────────────────────────────
FROM golang:1.21-alpine AS builder

WORKDIR /src

# Cache dependencies separately from source so layer is reused on code-only changes.
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /blis main.go

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM alpine:3.19

RUN apk add --no-cache ca-certificates tzdata

COPY --from=builder /blis /usr/local/bin/blis

ENTRYPOINT ["/usr/local/bin/blis"]
```

Save to `inference-sim/Dockerfile`.

- [ ] **Step 2: Build the image locally to verify it compiles**

Run from `inference-sim/`:
```bash
docker build -t blis:local .
```
Expected: build completes without error, final image is ~15–25 MB.

- [ ] **Step 3: Smoke-test the binary inside the container**

```bash
docker run --rm blis:local --help
```
Expected: usage text printed, exit 0. If `blis` has no `--help` flag, `docker run --rm blis:local` should print usage and exit non-zero but not crash with "executable not found".

- [ ] **Step 4: Commit**

```bash
cd inference-sim
git add Dockerfile
git commit -m "build: add multi-stage Dockerfile for blis binary"
```

---

### Task 2: Write the release CI workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  docker:
    name: Build and push image
    runs-on: ubuntu-latest

    permissions:
      contents: read
      packages: write   # required to push to ghcr.io

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract image metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/inference-sim/blis
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=raw,value=latest

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          platforms: linux/amd64,linux/arm64
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Save to `inference-sim/.github/workflows/release.yml`.

- [ ] **Step 2: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))" && echo OK
```
Expected: `OK`

- [ ] **Step 3: Cross-check the trigger pattern matches existing tag convention**

Verify `docs.yml` also triggers on `v*` tags:
```bash
grep -A2 'tags:' .github/workflows/docs.yml
```
Expected: output contains `- 'v*'` — confirming both workflows use the same tag glob.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: build and push ghcr.io/inference-sim/blis on version tag"
```

---

### Task 3: Open the PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 2: Confirm GHCR package visibility**

After the first tag push triggers the workflow and the image is published, new GHCR packages default to **private**. The Tekton pipeline pulls the image from a cluster and will fail with a 401 if the package is private.

To make it public: go to `https://github.com/orgs/inference-sim/packages/container/blis/settings` → Change visibility → Public.

This only needs to be done once per package.

- [ ] **Step 3: Create the PR**

```bash
gh pr create \
  --title "build: Dockerfile + release workflow for ghcr.io/inference-sim/blis" \
  --body "$(cat <<'EOF'
## Summary

- Adds a multi-stage `Dockerfile` (Go 1.21 builder → Alpine 3.19 runtime) that produces the `blis` binary image.
- Adds `.github/workflows/release.yml` that triggers on `v*` tag pushes and publishes to `ghcr.io/inference-sim/blis` with exact version, major.minor, and `latest` tags.
- Multi-arch build (amd64 + arm64) with GitHub Actions layer caching.

## Why

The `sim2real` transfer pipeline (Stage 3 → Stage 5) needs a published `ghcr.io/inference-sim/blis:<tag>` image to populate `observe.image` in the Tekton benchmarking values. Without this image the Stage 5 PipelineRuns cannot start.

## Test plan

- [ ] `docker build -t blis:local .` completes without error
- [ ] `docker run --rm blis:local --help` prints usage
- [ ] After merge and tagging, verify image appears at `ghcr.io/inference-sim/blis`
EOF
)"
```

Expected: PR URL printed.
