# Issue #497: schema `byo: true` marker + validator + command-specific guards

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-algorithm `byo: true` marker to the v3 `transfer.yaml` schema, loosen the validator so BYO entries can omit `source:` and all-BYO manifests can omit `component:`, and add explicit guards in `sim2real translate` / `sim2real build` so BLIS-only commands fail with actionable messages on BYO-shaped manifests.

**Architecture:** The change lands in three places. (1) `pipeline/lib/manifest.py` gets a new optional `byo` bool per algorithm entry, and two validator loosens — the per-algo required-field loop skips `source` when `byo is True`, and `_validate_v3_fields`'s `component` requirement changes from "any algorithm" to "any non-BYO algorithm". (2) `pipeline/sim2real.py` gains two per-command guards: `_cmd_translate` errors on any BYO algorithm, and `_cmd_build` loads `transfer.yaml` (with the same discovery fallback `_cmd_translate` uses) and errors if `component:` is absent. (3) Tests + README note.

**Tech Stack:** Python 3, PyYAML, pytest.

## Global Constraints

- Non-BYO entries still require `source:`. Non-BYO manifests still require `component:`. BLIS validation stays strict.
- Error messages verbatim from the issue acceptance criteria — do not paraphrase.
- Preserve existing tests (regression); no drift in surface area outside what the issue names.
- Lint: `ruff check pipeline/ --select F` must stay clean.
- File paths: this session runs inside the worktree at `.claude/worktrees/issue-497-byo-marker-schema/`. Every path in every step must include that substring.

---

### Task 1: Schema — accept, type-check, and document the `byo: true` marker

**Files:**
- Modify: `pipeline/lib/manifest.py:80-90` (per-algorithm required-field loop)
- Test: `pipeline/tests/test_manifest.py` (add tests after the existing algorithms-block tests)

**Interfaces:**
- Consumes: nothing new
- Produces: loader accepts `algorithms[i].byo: true|false` (bool); type-check errors on non-bool; when `byo is True`, `source:` becomes optional for that entry; `name` and `defaults` remain required for every entry

- [ ] **Step 1: Write failing tests for `byo` acceptance, type check, and BYO-entry source loosening**

Append to `pipeline/tests/test_manifest.py`:

```python
# ── byo: true per-algorithm marker (v3 schema addition) ─────────────────

def test_byo_true_algorithm_loads_without_source(tmp_path):
    """`algorithms[i].byo: true` makes `source:` optional for that entry.

    Non-BYO manifests still need `component:`, so keep the MINIMAL_V3
    component block; loosening `component:` is exercised separately.
    """
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "byoalgo", "defaults": "baseline", "byo": True},
        ],
    }
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"][0]["name"] == "byoalgo"
    assert m["algorithms"][0]["byo"] is True
    assert "source" not in m["algorithms"][0]


def test_byo_false_algorithm_still_requires_source(tmp_path):
    """`byo: false` (or absent) leaves `source:` required — regression."""
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "treatment", "defaults": "baseline", "byo": False},
        ],
    }
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithms.*source"):
        load_manifest(path)


def test_byo_absent_algorithm_still_requires_source(tmp_path):
    """Absent `byo:` behaves identically to `byo: false` — regression."""
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "treatment", "defaults": "baseline"},
        ],
    }
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="algorithms.*source"):
        load_manifest(path)


@pytest.mark.parametrize("bad_value", ["true", 1, 0, [True], {"v": True}])
def test_byo_must_be_boolean(tmp_path, bad_value):
    """Non-bool `byo:` is rejected with an error naming the field and type."""
    data = {
        **MINIMAL_V3,
        "algorithms": [
            {"name": "byoalgo", "defaults": "baseline", "byo": bad_value,
             "source": "sim2real_golden/routers/router_adaptive_v2.go"},
        ],
    }
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="byo.*bool"):
        load_manifest(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py -v -k "byo"`
Expected: FAIL — either the tests fail because the loader rejects `byo`, or because the loosening isn't in place.

- [ ] **Step 3: Implement the schema change in `pipeline/lib/manifest.py`**

Replace the algorithms required-field loop at approximately `pipeline/lib/manifest.py:80-90`. The current loop is:

```python
        for i, entry in enumerate(algos):
            if not isinstance(entry, dict):
                raise ManifestError(f"algorithms[{i}] must be a mapping")
            for f in ("name", "source", "defaults"):
                if f not in entry:
                    raise ManifestError(f"algorithms[{i}] missing required field: {f}")
            _validate_package_name(entry["name"], f"algorithms[{i}]")
            if entry["name"] in seen_algo_names:
                raise ManifestError(f"algorithms: duplicate name '{entry['name']}'")
            seen_algo_names.add(entry["name"])
```

Replace with:

```python
        for i, entry in enumerate(algos):
            if not isinstance(entry, dict):
                raise ManifestError(f"algorithms[{i}] must be a mapping")
            # `byo: true` marker (v3 schema addition). Bool type; if True,
            # `source:` is optional for this entry (per-command validation
            # in sim2real.py rejects BYO manifests where a `.go` source
            # is actually needed).
            byo = entry.get("byo", False)
            if "byo" in entry and not isinstance(byo, bool):
                raise ManifestError(
                    f"algorithms[{i}].byo must be a bool, got {type(byo).__name__}"
                )
            required = ("name", "defaults") if byo else ("name", "source", "defaults")
            for f in required:
                if f not in entry:
                    raise ManifestError(f"algorithms[{i}] missing required field: {f}")
            _validate_package_name(entry["name"], f"algorithms[{i}]")
            if entry["name"] in seen_algo_names:
                raise ManifestError(f"algorithms: duplicate name '{entry['name']}'")
            seen_algo_names.add(entry["name"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py -v -k "byo"`
Expected: PASS (all four tests).

- [ ] **Step 5: Run the full manifest test file to check for regressions**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py -v`
Expected: All tests pass (existing tests unchanged behavior).

- [ ] **Step 6: Commit**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-497-byo-marker-schema
git add pipeline/lib/manifest.py pipeline/tests/test_manifest.py
git commit -m "manifest: accept optional \`byo: true\` per-algorithm marker

Loosen the algorithms required-field loop so an entry marked
\`byo: true\` may omit \`source:\`. \`name\` and \`defaults\` remain
required for every entry. Non-bool \`byo:\` values are rejected with
an error naming the field and its type.

Refs #497"
```

---

### Task 2: Schema — `component:` optional when all algorithms are BYO

**Files:**
- Modify: `pipeline/lib/manifest.py:160-171` (component requirement in `_validate_v3_fields`)
- Test: `pipeline/tests/test_manifest.py` (add tests after Task 1's block)

**Interfaces:**
- Consumes: `entry.get("byo") is True` marker from Task 1
- Produces: `component:` is optional when every algorithm entry carries `byo: true`; mixed manifests (any non-BYO algo) still require `component:`

- [ ] **Step 1: Write failing tests**

Append to `pipeline/tests/test_manifest.py`:

```python
def test_component_optional_when_all_algorithms_byo(tmp_path):
    """`component:` may be absent when every algorithm carries `byo: true`."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    data["algorithms"] = [
        {"name": "byoa", "defaults": "baseline", "byo": True},
        {"name": "byob", "defaults": "baseline", "byo": True},
    ]
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert "component" not in m
    assert len(m["algorithms"]) == 2


def test_component_required_when_mixed_byo_and_blis(tmp_path):
    """`component:` still required when any algorithm is non-BYO."""
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    data["algorithms"] = [
        {"name": "byoalgo", "defaults": "baseline", "byo": True},
        {"name": "blisalgo", "defaults": "baseline",
         "source": "sim2real_golden/routers/router_adaptive_v2.go"},
    ]
    path = _write_manifest(tmp_path, data)
    with pytest.raises(ManifestError, match="component.*required"):
        load_manifest(path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py -v -k "component_optional_when_all_algorithms_byo or component_required_when_mixed"`
Expected: FAIL — the all-BYO test currently fails because `_validate_v3_fields` errors on missing `component:` whenever algorithms is non-empty.

- [ ] **Step 3: Implement the component-requirement loosening**

In `pipeline/lib/manifest.py`, replace the `has_algorithms` block inside `_validate_v3_fields`. Current:

```python
    # component (required only when algorithms are present)
    component = data.get("component")
    has_algorithms = bool(data.get("algorithms"))

    if component is None:
        if has_algorithms:
            raise ManifestError(
                "component is required when algorithms are specified"
            )
        # Remove explicit null so downstream .get("component", {}) returns {} not None
        data.pop("component", None)
```

Replace with:

```python
    # component (required only when at least one algorithm is BLIS-shape;
    # all-BYO manifests may omit component: since BYO images are pre-built).
    component = data.get("component")
    algos = data.get("algorithms") or []
    has_blis_algorithm = any(algo.get("byo") is not True for algo in algos)

    if component is None:
        if has_blis_algorithm:
            raise ManifestError(
                "component is required when non-BYO algorithms are specified"
            )
        # Remove explicit null so downstream .get("component", {}) returns {} not None
        data.pop("component", None)
```

**Note:** the error message changes from `"component is required when algorithms are specified"` to `"component is required when non-BYO algorithms are specified"`. Existing test `test_component_required_when_algorithms_present` matches on `"component.*required.*algorithms"` — this regex still matches. Verify.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py -v -k "byo or component"`
Expected: All pass, including existing `test_component_required_when_algorithms_present` (regex still matches the new message).

- [ ] **Step 5: Run the full manifest test file**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-497-byo-marker-schema
git add pipeline/lib/manifest.py pipeline/tests/test_manifest.py
git commit -m "manifest: make \`component:\` optional when all algorithms are BYO

The component submodule exists to source-build BLIS-generated
algorithm images; BYO operators bring pre-built images and have no
submodule to pin. Loosen \`_validate_v3_fields\` so \`component:\` is
required only when at least one algorithm entry is non-BYO. Mixed
manifests (any \`byo: false\`/absent entry) still require it.

Refs #497"
```

---

### Task 3: `sim2real translate` — BYO algorithm guard

**Files:**
- Modify: `pipeline/sim2real.py` `_cmd_translate` (around line 600, after `declared_algos = ...`)
- Test: `pipeline/tests/test_translate.py` (append to file)

**Interfaces:**
- Consumes: manifest with `algorithms[].byo: true`
- Produces: exit code 2 + exact error message on stderr when any algorithm is BYO

- [ ] **Step 1: Write failing test**

Append to `pipeline/tests/test_translate.py`:

```python
# ── BYO guard (issue #497) ────────────────────────────────────────────────


class TestTranslateByoGuard:
    """`sim2real translate` refuses to run on BYO algorithms — the BYO
    path is `sim2real translation register`, not `translate`."""

    def _write_byo_manifest(self, tmp_path):
        exp = tmp_path
        (exp / "algorithms").mkdir(exist_ok=True)
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "byo-scenario",
            "baselines": [{"name": "base", "scenario": "baseline-scenario"}],
            "algorithms": [
                {"name": "byoalgo", "defaults": "base", "byo": True},
            ],
            "context": {"text": "", "files": []},
        }
        path = exp / "transfer.yaml"
        path.write_text(yaml.safe_dump(manifest))
        return path

    def test_all_byo_manifest_errors(self, tmp_path, capsys):
        self._write_byo_manifest(tmp_path)
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "cannot translate algorithm 'byoalgo'" in err
        assert "sim2real translation register" in err

    def test_mixed_manifest_errors_on_byo_algo(self, tmp_path, capsys):
        """One BYO + one BLIS algorithm → still errors, naming the BYO one."""
        exp = tmp_path
        (exp / "algorithms").mkdir(exist_ok=True)
        (exp / "algorithms" / "blisalgo.py").write_text("# stub\n")
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "mixed-scenario",
            "baselines": [{"name": "base", "scenario": "baseline-scenario"}],
            "algorithms": [
                {"name": "byoalgo", "defaults": "base", "byo": True},
                {"name": "blisalgo", "defaults": "base",
                 "source": "algorithms/blisalgo.py"},
            ],
            "component": {"repo": "example.com/x/y", "kind": "scorer"},
            "context": {"text": "", "files": []},
        }
        (exp / "transfer.yaml").write_text(yaml.safe_dump(manifest))
        assert _run_translate([]) == 2
        err = capsys.readouterr().err
        assert "cannot translate algorithm 'byoalgo'" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translate.py::TestTranslateByoGuard -v`
Expected: FAIL — with the schema loosened but no guard yet, the loader lets the BYO manifest through; then either `_cmd_translate` completes (all-BYO test) or errors with a different message (missing `source:` file, since slicer skips it — actually it may succeed and write a checkpoint, which is exactly the silent-misprocess bug this guard prevents).

- [ ] **Step 3: Implement the guard**

In `pipeline/sim2real.py` `_cmd_translate`, after the algorithms-declared check:

```python
    declared_algos = manifest.get("algorithms") or []
    if not declared_algos:
        print("error: transfer.yaml has no algorithms declared", file=sys.stderr)
        return 2
    for algo in declared_algos:
        try:
            translation_ref.validate_name(algo.get("name", ""))
        except translation_ref.ValidationError as exc:
            print(f"error: invalid algorithm name: {exc}", file=sys.stderr)
            return 2
```

Add immediately after, before the `slicer.translation_hash_with_sources` call:

```python
    # BYO algorithms are registered directly via `sim2real translation
    # register`; they have no `source:` for the skill-driven translate
    # pipeline to work on. Error early with a pointer to the right command
    # rather than silently writing a checkpoint with zero-source algorithms.
    for algo in declared_algos:
        if algo.get("byo") is True:
            print(
                f"error: cannot translate algorithm '{algo['name']}' — no "
                f"`source:` in transfer.yaml (BYO algorithm; use "
                f"`sim2real translation register` directly).",
                file=sys.stderr,
            )
            return 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translate.py::TestTranslateByoGuard -v`
Expected: PASS.

- [ ] **Step 5: Run the whole `test_translate.py` for regressions**

Run: `.venv/bin/python -m pytest pipeline/tests/test_translate.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-497-byo-marker-schema
git add pipeline/sim2real.py pipeline/tests/test_translate.py
git commit -m "sim2real translate: reject BYO algorithms with pointer to \`register\`

The skill-driven \`translate\` pipeline consumes \`algorithms[].source\`
(reads bytes, hashes, and passes to the skill). BYO algorithms have
no such source. With the schema loosening in the prior commits, an
all-BYO manifest would otherwise pass validation and produce a
zero-source checkpoint that silently misrepresents intent.

Guard early in \`_cmd_translate\`; error mentions the algorithm name
and points at \`sim2real translation register\`.

Refs #497"
```

---

### Task 4: `sim2real build` — all-BYO manifest guard

**Files:**
- Modify: `pipeline/sim2real.py` `_cmd_build` (around line 838, right after `exp_root` is derived)
- Test: `pipeline/tests/test_build.py` (append)

**Interfaces:**
- Consumes: manifest with `component:` absent
- Produces: exit code 2 + exact error message on stderr

- [ ] **Step 1: Write failing test**

Append to `pipeline/tests/test_build.py`. First, check the file's imports and any shared fixture — the existing `TestSim2realBuildPrereqs` class already uses `_make_workspace` and calls `sim2real.main([...])`. Follow that pattern:

```python
class TestSim2realBuildByoGuard:
    """Issue #497: `sim2real build` on an all-BYO manifest errors early —
    BYO images are pre-built, so there's nothing for `build` to do."""

    def test_all_byo_manifest_errors_without_component(self, tmp_path, capsys):
        _make_workspace(tmp_path)
        # Write an all-BYO transfer.yaml (no component:, all algos byo: true)
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "byo-scenario",
            "baselines": [{"name": "base", "scenario": "baseline-scenario"}],
            "algorithms": [
                {"name": "byoalgo", "defaults": "base", "byo": True},
            ],
            "context": {"text": "", "files": []},
        }
        (tmp_path / "transfer.yaml").write_text(yaml.safe_dump(manifest))
        with patch("pipeline.lib.build.shutil.which",
                   return_value="/usr/bin/skopeo"):
            rc = sim2real.main([
                "--experiment-root", str(tmp_path),
                "build", "--translation", "byo-scenario",
            ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "nothing to build" in err
        assert "all algorithms are BYO" in err
```

Then check `test_build.py`'s imports include `yaml` and `patch`:

Run: `.venv/bin/python -c "import ast; t=ast.parse(open('pipeline/tests/test_build.py').read()); print([n.names[0].name for n in ast.walk(t) if isinstance(n, ast.Import)])"`

If `yaml` isn't imported, add `import yaml` near the top of the file (alongside the existing `import json`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_build.py::TestSim2realBuildByoGuard -v`
Expected: FAIL — the current `_cmd_build` doesn't read `transfer.yaml` and will proceed to `translation_ref.resolve_translation_ref` which errors with a different, generic message.

- [ ] **Step 3: Implement the guard**

In `pipeline/sim2real.py` `_cmd_build`, insert AFTER the `exp_root` derivation and BEFORE `layout.set_experiment_root(...)`:

```python
def _cmd_build(args) -> int:
    """..."""
    from pipeline.lib import build, cluster_ops, translation_ref

    exp_root = (
        Path(args.experiment_root).resolve()
        if args.experiment_root
        else Path.cwd()
    )
    # BYO guard (issue #497): if a transfer.yaml is discoverable and
    # declares no `component:` (all algorithms marked `byo: true`), the
    # images are pre-built and there is nothing to build. Error early
    # rather than proceeding to build.check_skopeo / translation resolve.
    from pipeline.lib import manifest as _manifest
    manifest_path = exp_root / "transfer.yaml"
    if not manifest_path.exists():
        manifest_path = exp_root / "config" / "transfer.yaml"
    if manifest_path.exists():
        try:
            _mf = _manifest.load_manifest(manifest_path)
        except _manifest.ManifestError:
            # Malformed manifest: let the downstream translation path
            # produce its own error message rather than short-circuiting
            # here on a manifest-parse failure.
            _mf = None
        if _mf is not None and "component" not in _mf:
            print(
                "error: nothing to build — this transfer.yaml declares no "
                "component (all algorithms are BYO; images are pre-built).",
                file=sys.stderr,
            )
            return 2

    layout.set_experiment_root(str(exp_root))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_build.py::TestSim2realBuildByoGuard -v`
Expected: PASS.

- [ ] **Step 5: Run the whole `test_build.py` for regressions**

Run: `.venv/bin/python -m pytest pipeline/tests/test_build.py -v`
Expected: All pass. (Existing tests pass their own transfer.yaml with `component:` present — the guard is a no-op for them.)

- [ ] **Step 6: Commit**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-497-byo-marker-schema
git add pipeline/sim2real.py pipeline/tests/test_build.py
git commit -m "sim2real build: reject all-BYO manifests up front

If \`transfer.yaml\` is discoverable and declares no \`component:\`
(the all-BYO shape enabled by the prior schema commit), there is
nothing for \`build\` to do — BYO images are pre-built. Error early
with the message the issue mandates, before touching skopeo or
translation resolution.

If the manifest is unparseable, defer to the downstream path so its
error message wins.

Refs #497"
```

---

### Task 5: `sim2real translation register` / `assemble` BYO regression check

**Files:**
- Test: `pipeline/tests/test_manifest.py` (add one integration-style test)

**Interfaces:**
- Consumes: BYO manifest through `manifest.load_manifest`
- Produces: assurance that the loader returns a dict with no missing keys downstream register/assemble would dereference

- [ ] **Step 1: Write regression test**

Append to `pipeline/tests/test_manifest.py`:

```python
def test_byo_manifest_loads_cleanly_for_downstream(tmp_path):
    """A BYO manifest through `load_manifest` produces a dict whose
    downstream consumers (`translation register`, `assemble`) can access
    without dereferencing a field the BYO entry omits.

    Issue #497 acceptance: "regression test: load a BYO manifest
    through `manifest.load_manifest` and verify no field a BYO entry
    omits is dereferenced." The dereferenced fields today are
    `algorithms[i].name`, `algorithms[i].defaults`, and (for
    register-batched mode in a later PR) `algorithms[i].byo`. This
    test asserts those exist; `source:` and `component:` should be
    absent.
    """
    data = {k: v for k, v in MINIMAL_V3.items() if k != "component"}
    data["algorithms"] = [
        {"name": "byoa", "defaults": "baseline", "byo": True},
    ]
    path = _write_manifest(tmp_path, data)
    m = load_manifest(path)
    assert m["algorithms"][0]["name"] == "byoa"
    assert m["algorithms"][0]["defaults"] == "baseline"
    assert m["algorithms"][0]["byo"] is True
    assert "source" not in m["algorithms"][0]
    assert "component" not in m
    # cross-reference to baseline still enforced (regression):
    assert m["algorithms"][0]["defaults"] in {b["name"] for b in m["baselines"]}
```

- [ ] **Step 2: Run test to verify it passes (after Tasks 1+2)**

Run: `.venv/bin/python -m pytest pipeline/tests/test_manifest.py::test_byo_manifest_loads_cleanly_for_downstream -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-497-byo-marker-schema
git add pipeline/tests/test_manifest.py
git commit -m "test: BYO manifest loads cleanly for downstream register/assemble

Regression test called out in issue #497 acceptance criteria:
loading a BYO-shaped manifest through \`manifest.load_manifest\`
must not require the caller to dereference \`source:\` or
\`component:\`. Asserts the fields register/assemble actually read
(algorithm \`name\`, \`defaults\`, \`byo\`) are present and the
BYO-omitted fields are absent.

Refs #497"
```

---

### Task 6: README note about `byo:` marker

**Files:**
- Modify: `pipeline/README.md` — the v3 schema reference near line 37 or the manifest section near line 179

**Interfaces:**
- Consumes: nothing
- Produces: one-paragraph note operators can find via grep for `byo`

- [ ] **Step 1: Read the current manifest-reference area of `pipeline/README.md`**

Read: `pipeline/README.md` around lines 37 and 175-190 (the two places `manifest.py` and `component`/`algorithms` are named).

- [ ] **Step 2: Add a short prose note**

Append the following paragraph to the "Manifest & Layout" area (or immediately after line 37's schema reference — the exact anchor is left to the implementer, but the target is a place operators actually read). Suggested location: at the end of the paragraph that names the v3 schema fields.

```markdown
### Per-algorithm `byo:` marker

An algorithm entry may carry `byo: true` to indicate the image is
pre-built and the entry has no BLIS-format source. When present:

- `algorithms[i].source:` is optional for that entry (the loader
  otherwise requires it).
- Top-level `component:` is optional when every algorithm entry
  carries `byo: true` (BYO manifests have no submodule to pin).

`sim2real translate` and `sim2real build` refuse to run on a BYO
entry / all-BYO manifest respectively — use `sim2real translation
register` to attach the pre-built image directly. `sim2real
assemble` and `sim2real-check` treat BYO entries the same as BLIS
entries at the consumption boundary.
```

- [ ] **Step 3: Verify the paragraph is well-formed and slotted in the right place**

Run: `grep -n "byo:" pipeline/README.md`
Expected: at least one hit in a manifest-reference section, no orphan hits outside the schema-reference area.

- [ ] **Step 4: Commit**

```bash
cd /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real/.claude/worktrees/issue-497-byo-marker-schema
git add pipeline/README.md
git commit -m "docs(pipeline): document per-algorithm \`byo:\` marker

Short reference in \`pipeline/README.md\` covering the two schema
loosenings (per-entry \`source:\`, top-level \`component:\`) and
which downstream commands accept vs reject BYO shapes.

Refs #497"
```

---

### Task 7: Final verification

- [ ] **Step 1: Run the entire pipeline test suite**

Run: `.venv/bin/python -m pytest pipeline/ -v`
Expected: 0 failures. Note test count; must match pre-change count + the new tests added in Tasks 1-5 (approximately 7 new tests total).

- [ ] **Step 2: Lint check**

Run: `.venv/bin/ruff check pipeline/ --select F`
Expected: 0 findings.

- [ ] **Step 3: Sweep for stale references**

Run: `grep -rn "component is required when algorithms are specified" .claude/worktrees/issue-497-byo-marker-schema/ 2>/dev/null | grep -v -E "^\\.claude/worktrees/issue-497-byo-marker-schema/\\.git/"`
Expected: 0 hits (the error message was updated in Task 2; nothing else in the repo should have quoted the old wording).

Run: `grep -rn "algorithms\\[.\\]\\.source" .claude/worktrees/issue-497-byo-marker-schema/pipeline/ .claude/worktrees/issue-497-byo-marker-schema/docs/ 2>/dev/null | grep -v ".git/"`
Expected: hits are limited to places that discuss the BLIS shape or the translation-slice design (slicer.py, design doc); no incorrect "required" claims.

Grep for accidental worktree escapes:

Run: `git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status`
Expected: clean — only the worktree branch shows changes.

- [ ] **Step 4: Manual trace against issue acceptance criteria**

Walk each of the eight acceptance bullets in issue #497 and confirm the corresponding test or code change exists. If any bullet has no coverage, add it before pushing.
