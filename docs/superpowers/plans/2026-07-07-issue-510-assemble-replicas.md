# Issue #510 — `assemble` additive-merge + `replicas` consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow `pipeline/lib/assemble_run.py` from step-1's single-replica shape to `--replicas N` support with an additive-merge (grow-only) invariant. Every assemble emits canonical pipe-shape pair keys (`wl-<workload>|<package>|iN`); re-assemble at higher N adds new iterations while preserving existing ones; shrink refuses.

**Architecture:** Unified pipe shape everywhere. No `--replicas` defaults to `--replicas 1` and emits `|i1`. Prior `runs/<R>/manifest.assembly.yaml`'s `replicas` field is the single source of truth for current replica count on re-assemble (absent → legacy). `params_hash` excludes `replicas` so replica bumps don't trip drift detection.

**Tech Stack:** Python 3.10+, PyYAML, pytest, argparse. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-07-07-issue-510-assemble-replicas-design.md`](../specs/2026-07-07-issue-510-assemble-replicas-design.md).

## Global Constraints

- Base branch: `refactor/v2-step-5`. All commits + PR target this branch.
- Every assembled PipelineRun's `metadata.name` gains `-i{iteration}` suffix (always, including the default `iteration=1` case).
- Every PipelineRun filename becomes `pipelinerun-<workload-safe>|<package>|i<N>.yaml` (pipe separators). `<workload-safe>` still replaces `_` with `-`.
- `manifest.assembly.yaml` gains a `replicas: N` top-level field.
- `params_hash` is computed as SHA-256 of the YAML slice with the `replicas` field removed and `sort_keys=True` canonicalization.
- `_load_pairs` in `pipeline/deploy.py` is NOT touched in this PR — its existing filename-stem key derivation naturally produces canonical grammar-matching keys once filenames use pipes.
- `--force` bypasses drift detection but does NOT bypass the grow-only guard.
- Legacy-run refuse messages must reference `#506` (the shrink tracking issue).
- CI must pass: `ruff check pipeline/ .claude/skills/ --select F` and the pytest list in `.github/workflows/test.yml`.

**Expected TDD "red" window:** Task 2 changes the emitted filename and `metadata.name` shape. Existing tests in `test_assemble_run.py` and `test_tekton.py` assert on the old shape and will fail between the code change and the same-task test updates. **This is expected.** Task 2 commits code + test-update together so the branch is green after each task.

---

## Task 1: `_positive_int` helper + `--replicas` argparse argument

Add the CLI surface for `--replicas`. Do not wire it through to `assemble_run` yet — that's Task 4. This is a stand-alone argparse addition.

**Files:**
- Modify: `pipeline/sim2real.py` (add `_positive_int` helper near top of file; add `asm.add_argument(--replicas, ...)` inside `_build_parser`)
- Modify: `pipeline/tests/test_sim2real.py` (add argparse tests)

**Interfaces:**
- Produces: `_positive_int(s: str) -> int` — argparse `type=` callable. Raises `argparse.ArgumentTypeError` on non-positive or non-integer input.
- Produces: `--replicas N` argument on the `assemble` subparser, `type=_positive_int`, `default=1`, `metavar="N"`.

- [ ] **Step 1.1: Write failing argparse tests**

Add to `pipeline/tests/test_sim2real.py` (append, do not overwrite):

```python
import argparse
import pytest


class TestPositiveInt:
    def test_accepts_positive_integer(self):
        from pipeline.sim2real import _positive_int
        assert _positive_int("1") == 1
        assert _positive_int("42") == 42

    def test_rejects_zero(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError, match=">= 1"):
            _positive_int("0")

    def test_rejects_negative(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError, match=">= 1"):
            _positive_int("-1")

    def test_rejects_non_integer(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
            _positive_int("abc")


class TestAssembleReplicasArg:
    def test_replicas_defaults_to_one(self):
        from pipeline.sim2real import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "assemble", "--translation", "h", "--cluster", "c", "--run", "r",
        ])
        assert args.replicas == 1

    def test_replicas_accepts_positive_int(self):
        from pipeline.sim2real import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "assemble", "--translation", "h", "--cluster", "c", "--run", "r",
            "--replicas", "3",
        ])
        assert args.replicas == 3

    def test_replicas_rejects_zero(self):
        from pipeline.sim2real import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "assemble", "--translation", "h", "--cluster", "c", "--run", "r",
                "--replicas", "0",
            ])
```

If `pipeline/tests/test_sim2real.py` does not already import `pytest`, ensure the import is present.

- [ ] **Step 1.2: Run tests, verify they fail**

```bash
python -m pytest pipeline/tests/test_sim2real.py::TestPositiveInt -v
python -m pytest pipeline/tests/test_sim2real.py::TestAssembleReplicasArg -v
```

Expected: `ImportError` on `_positive_int` and `AttributeError` on `args.replicas` (or similar) — the code doesn't exist yet.

- [ ] **Step 1.3: Add `_positive_int` helper to `pipeline/sim2real.py`**

Add near the top of the file, after existing top-level imports and before the first CLI-building function. Ensure `import argparse` is present at the top of the file.

```python
def _positive_int(s: str) -> int:
    """argparse type= callable — accepts strings parsing to integers >= 1."""
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {s!r}"
        )
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {v}")
    return v
```

- [ ] **Step 1.4: Add `--replicas` argument to the assemble subparser**

Locate the `asm = subparsers.add_parser("assemble", ...)` block in `pipeline/sim2real.py` (around line 435). After the existing `--force` argument, add:

```python
    asm.add_argument(
        "--replicas",
        type=_positive_int,
        default=1,
        metavar="N",
        help="number of replica iterations per (workload, package) pair (default: 1)",
    )
```

- [ ] **Step 1.5: Run tests, verify they pass**

```bash
python -m pytest pipeline/tests/test_sim2real.py::TestPositiveInt pipeline/tests/test_sim2real.py::TestAssembleReplicasArg -v
```

Expected: all pass.

- [ ] **Step 1.6: Run full pipeline test suite + lint**

```bash
python -m pytest pipeline/ -v
ruff check pipeline/ --select F
```

Expected: no new failures; lint clean.

- [ ] **Step 1.7: Commit**

```bash
git add pipeline/sim2real.py pipeline/tests/test_sim2real.py
git commit -m "sim2real: add --replicas argparse arg and _positive_int helper (#510)"
```

---

## Task 2: Unify pipe shape in `make_pipelinerun_scenario` and `generate_pipelineruns`

Change the emitted `metadata.name` and PipelineRun filename shape to always include `|i<N>` / `-i<N>` suffixes. Update the four existing tests that assert on the old shape (three in `test_tekton.py`, five filename assertions in `test_assemble_run.py`) in the same commit so the tree is green.

**Files:**
- Modify: `pipeline/lib/tekton.py` (add `iteration: int = 1` param to `make_pipelinerun_scenario`, append `-i{iteration}` to `metadata.name`)
- Modify: `pipeline/lib/assemble_run.py` (add `iterations` param to `generate_pipelineruns`, emit N files per pair with pipe-shape filenames)
- Modify: `pipeline/tests/test_tekton.py` (update 3 `metadata.name` assertions + add new `iteration` param test)
- Modify: `pipeline/tests/test_assemble_run.py` (update 5 filename assertions + add pipe-shape/`|i1` assertion for default flow)

**Interfaces:**
- Produces: `make_pipelinerun_scenario(..., iteration: int = 1) -> dict` — new keyword-only param appended after `observe`. `metadata.name = f"{safe_phase}-{safe_name}-{run_name}-i{iteration}"`.
- Produces: `generate_pipelineruns(..., iterations: "range | list[int]" = range(1, 2)) -> None` — new keyword-only param appended at the end of the signature. Emits `len(iterations) × workloads × packages` files; filename shape `pipelinerun-<workload-safe>|<package>|i<N>.yaml` where N is each element of `iterations`.

- [ ] **Step 2.1: Write failing tests for `make_pipelinerun_scenario` iteration param**

Add to `pipeline/tests/test_tekton.py` (append):

```python
def test_make_pipelinerun_scenario_iteration_default_is_one():
    """When iteration is not passed, default is 1 and name gets '-i1' suffix."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
    )
    assert pr["metadata"]["name"] == "baseline-wl-r-i1"


def test_make_pipelinerun_scenario_iteration_explicit():
    """Explicit iteration=N produces '-i<N>' suffix on metadata.name."""
    pr = make_pipelinerun_scenario(
        phase="baseline", workload={"name": "wl"}, run_name="r",
        namespace="ns", pipeline_name="sim2real",
        scenario_content="scenario: []",
        iteration=5,
    )
    assert pr["metadata"]["name"] == "baseline-wl-r-i5"
```

- [ ] **Step 2.2: Run tests, verify they fail**

```bash
python -m pytest pipeline/tests/test_tekton.py::test_make_pipelinerun_scenario_iteration_default_is_one -v
python -m pytest pipeline/tests/test_tekton.py::test_make_pipelinerun_scenario_iteration_explicit -v
```

Expected: FAIL — `iteration` param not accepted or suffix missing.

- [ ] **Step 2.3: Update `make_pipelinerun_scenario` in `pipeline/lib/tekton.py`**

Locate the function signature (around line 50). Add `iteration: int = 1,` as a new keyword arg after `observe: dict | None = None,`. Change the `pr_name` line from:

```python
    pr_name = f"{safe_phase}-{safe_name}-{run_name}"
```

to:

```python
    pr_name = f"{safe_phase}-{safe_name}-{run_name}-i{iteration}"
```

- [ ] **Step 2.4: Update existing `test_tekton.py` assertions**

In `pipeline/tests/test_tekton.py`, update three assertion lines (line numbers approximate — grep to be sure):

- Line 20: `assert pr["metadata"]["name"] == "baseline-wl-smoke-ac"` → `assert pr["metadata"]["name"] == "baseline-wl-smoke-ac-i1"`
- Line 125: `assert pr["metadata"]["name"] == "b1-wl-smoke-test-run"` → `assert pr["metadata"]["name"] == "b1-wl-smoke-test-run-i1"`
- Line 140: `assert pr["metadata"]["name"] == "my-phase-wl-smoke-test-run"` → `assert pr["metadata"]["name"] == "my-phase-wl-smoke-test-run-i1"`

- [ ] **Step 2.5: Run tekton tests, verify they pass**

```bash
python -m pytest pipeline/tests/test_tekton.py -v
```

Expected: all pass (including the two new tests from Step 2.1).

- [ ] **Step 2.6: Write failing test for pipe-shape filenames in `generate_pipelineruns`**

Add to `pipeline/tests/test_assemble_run.py`, inside `class TestGeneratePipelineruns` (near line 287, alongside `test_one_pipelinerun_per_workload_x_package`):

```python
    def test_iterations_range_emits_pipe_shape_filenames(self, tmp_path):
        """iterations=range(1, 3) → 2 files per (workload, package) with |i1, |i2."""
        run_dir = tmp_path / "runs" / "trial-1"
        cluster_dir = run_dir / "cluster"
        cluster_dir.mkdir(parents=True)
        packages = [
            ("baseline", {"scenario": [{"name": "s", "model": {"name": "M"}}]}),
        ]
        workloads = [{"name": "wl-a", "num_requests": 10}]
        cluster_config = {"namespaces": ["ns-0"], "workspaces": {}}
        assemble_run.generate_pipelineruns(
            run_dir=run_dir,
            packages=packages,
            workloads=workloads,
            run_name="trial-1",
            cluster_config=cluster_config,
            pipeline_name="sim2real",
            observe={},
            model_name="M",
            submodule_shas={},
            submodule_urls={},
            iterations=range(1, 3),
        )
        yamls = sorted(p.name for p in cluster_dir.glob("pipelinerun-*.yaml"))
        assert yamls == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|baseline|i2.yaml",
        ]
        pr = yaml.safe_load(
            (cluster_dir / "pipelinerun-wl-a|baseline|i2.yaml").read_text()
        )
        assert pr["metadata"]["name"] == "baseline-wl-a-trial-1-i2"
```

- [ ] **Step 2.7: Run test, verify it fails**

```bash
python -m pytest pipeline/tests/test_assemble_run.py::TestGeneratePipelineruns::test_iterations_range_emits_pipe_shape_filenames -v
```

Expected: FAIL — `iterations` param not accepted / old filename shape emitted.

- [ ] **Step 2.8: Update `generate_pipelineruns` in `pipeline/lib/assemble_run.py`**

Locate the function (starts around line 333). Add `iterations: "range | list[int]" = range(1, 2),` as the last keyword-only arg. Change the double-loop body from:

```python
    for pkg_name, resolved in packages:
        scenario_content = yaml.dump(
            resolved, default_flow_style=False, allow_unicode=True
        )
        for wl in workloads:
            wl_name = wl.get("name", wl.get("workload_name", "unknown"))
            safe_wl = wl_name.replace("_", "-")
            pr = make_pipelinerun_scenario(
                phase=pkg_name,
                workload=wl,
                run_name=run_name,
                namespace=namespace,
                pipeline_name=pipeline_name,
                scenario_content=scenario_content,
                workspace_bindings=ws_bindings if ws_bindings else None,
                benchmark_git_commit=submodule_shas.get("llm-d-benchmark", ""),
                benchmark_git_repo_url=submodule_urls.get("llm-d-benchmark", ""),
                blis_git_commit=submodule_shas.get("inference-sim", ""),
                blis_git_repo_url=submodule_urls.get("inference-sim", ""),
                model=model_name,
                observe=observe,
            )
            (cluster_dir_ / f"pipelinerun-{safe_wl}-{pkg_name}.yaml").write_text(
                yaml.dump(pr, default_flow_style=False, allow_unicode=True)
            )
```

to:

```python
    for pkg_name, resolved in packages:
        scenario_content = yaml.dump(
            resolved, default_flow_style=False, allow_unicode=True
        )
        for wl in workloads:
            wl_name = wl.get("name", wl.get("workload_name", "unknown"))
            safe_wl = wl_name.replace("_", "-")
            for iteration in iterations:
                pr = make_pipelinerun_scenario(
                    phase=pkg_name,
                    workload=wl,
                    run_name=run_name,
                    namespace=namespace,
                    pipeline_name=pipeline_name,
                    scenario_content=scenario_content,
                    workspace_bindings=ws_bindings if ws_bindings else None,
                    benchmark_git_commit=submodule_shas.get("llm-d-benchmark", ""),
                    benchmark_git_repo_url=submodule_urls.get("llm-d-benchmark", ""),
                    blis_git_commit=submodule_shas.get("inference-sim", ""),
                    blis_git_repo_url=submodule_urls.get("inference-sim", ""),
                    model=model_name,
                    observe=observe,
                    iteration=iteration,
                )
                fname = f"pipelinerun-{safe_wl}|{pkg_name}|i{iteration}.yaml"
                (cluster_dir_ / fname).write_text(
                    yaml.dump(pr, default_flow_style=False, allow_unicode=True)
                )
```

- [ ] **Step 2.9: Update existing filename assertions in `test_assemble_run.py`**

Update all five filename assertions in `pipeline/tests/test_assemble_run.py` (lines approximate — grep to confirm):

- Line 312-317 (in `test_one_pipelinerun_per_workload_x_package`): the list of expected filenames changes from `pipelinerun-wl-a-baseline.yaml`-style to pipe-shape `pipelinerun-wl-a|baseline|i1.yaml`:

  ```python
          yamls = sorted(p.name for p in cluster_dir.glob("pipelinerun-*.yaml"))
          assert yamls == [
              "pipelinerun-wl-a|baseline|i1.yaml",
              "pipelinerun-wl-a|sr|i1.yaml",
              "pipelinerun-wl-b|baseline|i1.yaml",
              "pipelinerun-wl-b|sr|i1.yaml",
          ]
          pr = yaml.safe_load(
              (cluster_dir / "pipelinerun-wl-a|sr|i1.yaml").read_text()
          )
  ```

- Line 471-472 (in `test_end_to_end_produces_expected_files`):

  ```python
          assert (run_dir / "cluster" / "pipelinerun-wl-a|baseline|i1.yaml").exists()
          assert (run_dir / "cluster" / "pipelinerun-wl-a|sr|i1.yaml").exists()
  ```

- Line 523 (in `test_pipelinerun_params_include_framework_submodule_state`):

  ```python
          pr = yaml.safe_load(
              (fx["exp_root"] / "workspace/runs/trial-1/cluster/"
               "pipelinerun-wl-a|baseline|i1.yaml").read_text()
          )
  ```

- Line 571 (in `test_missing_submodule_falls_back_to_unknown_and_warns`):

  ```python
          pr = yaml.safe_load(
              (fx["exp_root"] / "workspace/runs/trial-1/cluster/"
               "pipelinerun-wl-a|baseline|i1.yaml").read_text()
          )
  ```

Do a final grep to ensure no stale `pipelinerun-<name>-<pkg>.yaml`-shaped strings remain in `pipeline/tests/`:

```bash
grep -rn 'pipelinerun-[a-z-]*-[a-z]*\.yaml' pipeline/tests/
```

Expected: no hits after updates (or only comments explicitly documenting the shape change).

- [ ] **Step 2.10: Run tests, verify they pass**

```bash
python -m pytest pipeline/tests/test_assemble_run.py pipeline/tests/test_tekton.py -v
```

Expected: all pass.

- [ ] **Step 2.11: Run full pipeline test suite + lint**

```bash
python -m pytest pipeline/ -v
ruff check pipeline/ --select F
```

Expected: all pass. If `test_sim2real.py` or other tests fail with `KeyError` / attribute errors, they're likely tests that spot-check filenames — update those too.

- [ ] **Step 2.12: Commit**

```bash
git add pipeline/lib/tekton.py pipeline/lib/assemble_run.py pipeline/tests/test_tekton.py pipeline/tests/test_assemble_run.py
git commit -m "assemble: unify pipe-shape PipelineRun filenames + -iN metadata.name (#510)"
```

---

## Task 3: `manifest.assembly.yaml` gains `replicas` field; `params_hash` excludes it

`write_manifest_assembly` accepts `replicas` and writes it into the emitted YAML. `compute_params_hash` parses YAML, drops `replicas`, canonicalizes, hashes. This decouples replica-count changes from drift detection.

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (`write_manifest_assembly` + `compute_params_hash`)
- Modify: `pipeline/tests/test_assemble_run.py` (extend `test_writes_yaml_snapshot_of_assembly_slice` to check `replicas`; rewrite `test_params_hash_is_sha256_of_bytes` because it hashed raw bytes before)

**Interfaces:**
- Produces: `write_manifest_assembly(run_dir: Path, manifest: dict, *, now_iso: str, replicas: int = 1) -> Path` — writes `replicas: N` at the top of the emitted YAML dict alongside the assembly slice.
- Produces: `compute_params_hash(manifest_assembly_path: Path) -> str` — SHA-256 over `yaml.dump(<slice with replicas removed>, sort_keys=True, default_flow_style=False, allow_unicode=True).encode("utf-8")`.

- [ ] **Step 3.1: Write failing tests**

Add to `pipeline/tests/test_assemble_run.py`, inside the class that contains `test_writes_yaml_snapshot_of_assembly_slice` (near line 153):

```python
    def test_writes_replicas_field_when_provided(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "b.yaml"}],
            "algorithms": [],
            "workloads": [],
            "defaults": {"disable": []},
        }
        run_dir = tmp_path / "runs" / "r"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-07-01T14:05:00Z", replicas=3
        )
        parsed = yaml.safe_load(p.read_text())
        assert parsed["replicas"] == 3

    def test_writes_replicas_defaults_to_one(self, tmp_path):
        manifest = {
            "kind": "sim2real-transfer",
            "version": 3,
            "scenario": "test",
            "component": {"repo": "acme/foo"},
            "context": {"text": "", "files": []},
            "baselines": [{"name": "baseline", "scenario": "b.yaml"}],
            "algorithms": [],
            "workloads": [],
            "defaults": {"disable": []},
        }
        run_dir = tmp_path / "runs" / "r"
        run_dir.mkdir(parents=True)
        p = assemble_run.write_manifest_assembly(
            run_dir, manifest, now_iso="2026-07-01T14:05:00Z"
        )
        parsed = yaml.safe_load(p.read_text())
        assert parsed["replicas"] == 1
```

Rewrite `test_params_hash_is_sha256_of_bytes` (existing test around line 182):

```python
    def test_params_hash_excludes_replicas_field(self, tmp_path):
        """params_hash is stable when only the `replicas` field changes."""
        p1 = tmp_path / "a.yaml"
        p2 = tmp_path / "b.yaml"
        p1.write_text(
            "replicas: 3\nworkloads:\n- w1\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        p2.write_text(
            "replicas: 5\nworkloads:\n- w1\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        assert (
            assemble_run.compute_params_hash(p1)
            == assemble_run.compute_params_hash(p2)
        )

    def test_params_hash_changes_when_content_changes(self, tmp_path):
        """params_hash differs when non-replicas content changes."""
        p1 = tmp_path / "a.yaml"
        p2 = tmp_path / "b.yaml"
        p1.write_text(
            "replicas: 3\nworkloads:\n- w1\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        p2.write_text(
            "replicas: 3\nworkloads:\n- w2\nbaselines:\n- {name: b, scenario: b.yaml}\n"
        )
        assert (
            assemble_run.compute_params_hash(p1)
            != assemble_run.compute_params_hash(p2)
        )
```

Delete the old `test_params_hash_is_sha256_of_bytes` — it hashes raw bytes and no longer matches semantics.

- [ ] **Step 3.2: Run new tests, verify they fail**

```bash
python -m pytest pipeline/tests/test_assemble_run.py::TestWriteManifestAssembly -v
```

(Class name is whatever contains the manifest+hash tests — grep to confirm and run the actual class.) Expected: the two new `test_writes_replicas_*` fail (either accept kwarg not recognized or `replicas` field missing); the two new `test_params_hash_*` fail because current impl hashes raw bytes.

- [ ] **Step 3.3: Update `write_manifest_assembly` in `pipeline/lib/assemble_run.py`**

Change the signature and body (around line 229):

```python
def write_manifest_assembly(
    run_dir: Path, manifest: dict, *, now_iso: str, replicas: int = 1,
) -> Path:
    """Serialize ``slicer.assembly_slice(manifest)`` + ``replicas: N`` to
    ``manifest.assembly.yaml``.

    Prepends a one-line comment header naming the tool and timestamp.
    Returns the written path.
    """
    slice_ = slicer.assembly_slice(manifest)
    # Emit replicas at the top of the file for human readability, before the
    # rest of the assembly slice.
    out_dict = {"replicas": replicas, **slice_}
    body = yaml.dump(
        out_dict, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    text = f"# generated by sim2real assemble at {now_iso}; do not edit\n" + body
    out = run_dir / "manifest.assembly.yaml"
    out.write_text(text)
    return out
```

- [ ] **Step 3.4: Update `compute_params_hash` in `pipeline/lib/assemble_run.py`**

Change the function body (around line 247):

```python
def compute_params_hash(manifest_assembly_path: Path) -> str:
    """SHA-256 over the canonical assembly slice, with ``replicas`` excluded.

    Excluding ``replicas`` is deliberate: bumping ``--replicas N`` must not
    trip drift detection on re-assemble. Canonical form uses
    ``sort_keys=True`` so the hash is deterministic across YAML formatter
    ordering differences.
    """
    data = yaml.safe_load(manifest_assembly_path.read_text()) or {}
    if isinstance(data, dict):
        data.pop("replicas", None)
    canonical = yaml.dump(
        data, sort_keys=True, default_flow_style=False, allow_unicode=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
```

- [ ] **Step 3.5: Run tests, verify they pass**

```bash
python -m pytest pipeline/tests/test_assemble_run.py -v
```

Expected: all pass (new tests + existing tests unaffected since defaults preserve prior behavior for callers).

- [ ] **Step 3.6: Run full pipeline test suite + lint**

```bash
python -m pytest pipeline/ -v
ruff check pipeline/ --select F
```

Expected: all pass.

- [ ] **Step 3.7: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_run.py
git commit -m "assemble: write replicas into manifest.assembly.yaml; params_hash excludes replicas (#510)"
```

---

## Task 4: Wire `--replicas` through `assemble_run` for the fresh-run path

Thread the `--replicas N` value from CLI down to `write_manifest_assembly` and `generate_pipelineruns`. Only the fresh-run (`run_dir` doesn't exist) code path is updated here — additive-merge on existing runs is Task 5.

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (`assemble_run` signature + fresh-run body)
- Modify: `pipeline/sim2real.py` (`_cmd_assemble` passes `replicas=args.replicas`)
- Modify: `pipeline/tests/test_assemble_run.py` (extend `test_end_to_end_produces_expected_files` or add a new test asserting on N=3 fresh run)

**Interfaces:**
- Produces: `assemble_run(..., replicas: int = 1)` — new keyword-only param with default `1`. Existing tests that don't pass `replicas` continue to work.
- Consumes: `args.replicas` from Task 1.

- [ ] **Step 4.1: Write failing test — fresh run at N=3**

Add to `pipeline/tests/test_assemble_run.py`, inside the class holding `test_end_to_end_produces_expected_files` (near line 450):

```python
    def test_fresh_run_with_replicas_3_produces_three_files_per_pair(self, tmp_path):
        fx = _make_experiment(
            tmp_path,
            algo_names_registered=["sr"],
            algo_names_manifest=["sr"],
        )
        assemble_run.assemble_run(
            translation_hash=fx["translation_hash"],
            translation_ref=fx["translation_hash"],
            cluster_id=fx["cluster_id"],
            run_name="trial-1",
            experiment_root=fx["exp_root"],
            manifest_path=fx["manifest_path"],
            force=False,
            replicas=3,
            now_iso="2026-07-01T14:05:00Z",
        )
        cluster = fx["exp_root"] / "workspace" / "runs" / "trial-1" / "cluster"
        names = sorted(p.name for p in cluster.glob("pipelinerun-*.yaml"))
        assert names == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|baseline|i2.yaml",
            "pipelinerun-wl-a|baseline|i3.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
            "pipelinerun-wl-a|sr|i2.yaml",
            "pipelinerun-wl-a|sr|i3.yaml",
        ]
        # manifest.assembly.yaml records replicas
        manifest_assembly = yaml.safe_load(
            (fx["exp_root"] / "workspace/runs/trial-1/manifest.assembly.yaml")
            .read_text()
        )
        assert manifest_assembly["replicas"] == 3
```

- [ ] **Step 4.2: Run test, verify it fails**

```bash
python -m pytest pipeline/tests/test_assemble_run.py -k test_fresh_run_with_replicas_3 -v
```

Expected: `TypeError` — `assemble_run` doesn't accept `replicas` kwarg.

- [ ] **Step 4.3: Update `assemble_run` signature in `pipeline/lib/assemble_run.py`**

At the `def assemble_run(...)` signature (around line 418), add `replicas: int = 1,` as a new keyword-only arg after `force: bool,` and before `now_iso: str,`:

```python
def assemble_run(
    *,
    translation_hash: str,
    translation_ref: str,
    cluster_id: str,
    run_name: str,
    experiment_root: Path,
    manifest_path: Path,
    force: bool,
    replicas: int = 1,
    now_iso: str,
) -> None:
```

- [ ] **Step 4.4: Thread `replicas` into `write_manifest_assembly` and `generate_pipelineruns`**

In the body of `assemble_run` (around line 528 for the write call), change:

```python
    manifest_assembly_path = write_manifest_assembly(
        run_dir, manifest, now_iso=now_iso
    )
```

to:

```python
    manifest_assembly_path = write_manifest_assembly(
        run_dir, manifest, now_iso=now_iso, replicas=replicas
    )
```

And in the `generate_pipelineruns` call (around line 622), add `iterations=range(1, replicas + 1)`:

```python
    generate_pipelineruns(
        run_dir=run_dir,
        packages=packages,
        workloads=workloads,
        run_name=run_name,
        cluster_config=cluster_config,
        pipeline_name=pipeline_name,
        observe=observe,
        model_name=model_name,
        submodule_shas=submodule_shas,
        submodule_urls=submodule_urls,
        iterations=range(1, replicas + 1),
    )
```

- [ ] **Step 4.5: Update `_cmd_assemble` in `pipeline/sim2real.py`**

At the `_assemble_run_lib.assemble_run(...)` call (around line 1129), add the `replicas` kwarg:

```python
        _assemble_run_lib.assemble_run(
            translation_hash=translation_hash,
            translation_ref=args.translation,
            cluster_id=args.cluster,
            run_name=args.run,
            experiment_root=exp_root,
            manifest_path=manifest_path,
            force=args.force,
            replicas=args.replicas,
            now_iso=now_iso,
        )
```

- [ ] **Step 4.6: Update `run_metadata.json` write to include replicas**

In `assemble_run`, at the `write_run_metadata` call (near the end of the function), add `replicas` to the dict:

```python
    write_run_metadata(
        run_dir,
        {
            "version": 1,
            "run_name": run_name,
            "translation_hash": translation_hash,
            "cluster_id": cluster_id,
            "params_hash": params_hash,
            "image_tag": run_meta_image_tag,
            "replicas": replicas,
            "assembled_at": now_iso,
        },
    )
```

- [ ] **Step 4.7: Run tests, verify they pass**

```bash
python -m pytest pipeline/tests/test_assemble_run.py -v
```

Expected: all pass (new test + existing tests unaffected because `replicas` defaults to 1).

- [ ] **Step 4.8: Run full pipeline test suite + lint**

```bash
python -m pytest pipeline/ -v
ruff check pipeline/ --select F
```

Expected: all pass.

- [ ] **Step 4.9: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/sim2real.py pipeline/tests/test_assemble_run.py
git commit -m "assemble: thread --replicas N through fresh-run path (#510)"
```

---

## Task 5: Additive-merge decision tree + new test file

Implement the full existing-run decision tree in `assemble_run`: legacy detection, drift detection, grow-only guard, no-op idempotence, and additive-merge write path. Add a new test file with 10 tests covering every branch of the table.

**Files:**
- Modify: `pipeline/lib/assemble_run.py` (rewrite the `if run_dir.exists():` block around line 486)
- Create: `pipeline/tests/test_assemble_replicas.py`

**Interfaces:**
- Consumes: `assemble_run(...)` with `replicas` kwarg from Task 4.
- Consumes: `write_manifest_assembly`, `compute_params_hash` from Task 3.

- [ ] **Step 5.1: Write the 10 tests in a new file**

Create `pipeline/tests/test_assemble_replicas.py`. Structure: one class `TestAssembleReplicas`, one test per row of the decision table plus argparse-boundary. Each test uses the same `_make_experiment` fixture from `test_assemble_run.py` — import it, don't duplicate.

```python
"""Integration tests for issue #510 — additive-merge assemble.

Tests the decision tree implemented in ``pipeline/lib/assemble_run.py``:
legacy detection, drift detection, grow-only guard, no-op idempotence,
and additive-merge write path.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pytest
import yaml

from pipeline.lib import assemble_run
from pipeline.tests.test_assemble_run import _make_experiment  # reuse fixture


def _run_dir_of(fx: dict, run: str = "trial-1") -> Path:
    return fx["exp_root"] / "workspace" / "runs" / run


def _cluster_dir_of(fx: dict, run: str = "trial-1") -> Path:
    return _run_dir_of(fx, run) / "cluster"


def _pipelinerun_files(cluster_dir: Path) -> list[str]:
    return sorted(p.name for p in cluster_dir.glob("pipelinerun-*.yaml"))


def _mtimes(paths: list[Path]) -> list[float]:
    return [p.stat().st_mtime_ns for p in paths]


def _assemble(fx: dict, *, replicas: int = 1, force: bool = False,
              now_iso: str = "2026-07-01T00:00:00Z") -> None:
    assemble_run.assemble_run(
        translation_hash=fx["translation_hash"],
        translation_ref=fx["translation_hash"],
        cluster_id=fx["cluster_id"],
        run_name="trial-1",
        experiment_root=fx["exp_root"],
        manifest_path=fx["manifest_path"],
        force=force,
        replicas=replicas,
        now_iso=now_iso,
    )


class TestAssembleReplicas:
    def test_fresh_run_replicas_3_emits_three_iterations(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert names == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|baseline|i2.yaml",
            "pipelinerun-wl-a|baseline|i3.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
            "pipelinerun-wl-a|sr|i2.yaml",
            "pipelinerun-wl-a|sr|i3.yaml",
        ]

    def test_grow_from_3_to_5_preserves_i1_i3_and_adds_i4_i5(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        cluster = _cluster_dir_of(fx)
        keep = sorted(cluster.glob("pipelinerun-*|i[123].yaml"))
        keep_bytes_before = [p.read_bytes() for p in keep]
        keep_mtimes_before = _mtimes(keep)
        # Sleep briefly to ensure any rewrite would change mtime (ns resolution
        # varies by filesystem; 10ms is safe).
        time.sleep(0.01)
        _assemble(fx, replicas=5, now_iso="2026-07-02T00:00:00Z")
        # i1..i3 preserved byte-for-byte AND by mtime.
        keep_bytes_after = [p.read_bytes() for p in sorted(
            cluster.glob("pipelinerun-*|i[123].yaml"))]
        keep_mtimes_after = _mtimes(sorted(
            cluster.glob("pipelinerun-*|i[123].yaml")))
        assert keep_bytes_before == keep_bytes_after
        assert keep_mtimes_before == keep_mtimes_after
        # i4, i5 added.
        names = _pipelinerun_files(cluster)
        assert "pipelinerun-wl-a|baseline|i4.yaml" in names
        assert "pipelinerun-wl-a|baseline|i5.yaml" in names
        assert "pipelinerun-wl-a|sr|i4.yaml" in names
        assert "pipelinerun-wl-a|sr|i5.yaml" in names
        # manifest.assembly.yaml records new replicas.
        ma = yaml.safe_load(
            (_run_dir_of(fx) / "manifest.assembly.yaml").read_text()
        )
        assert ma["replicas"] == 5

    def test_shrink_from_3_to_2_refuses_with_error_naming_count_and_506(
        self, tmp_path,
    ):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        with pytest.raises(assemble_run.AssembleError) as exc:
            _assemble(fx, replicas=2)
        msg = str(exc.value)
        assert "3" in msg  # current count named
        assert "#506" in msg  # shrink tracking issue referenced

    def test_reassemble_at_same_replica_count_is_noop(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3, now_iso="2026-07-01T00:00:00Z")
        run_dir = _run_dir_of(fx)
        all_paths = sorted(list(run_dir.rglob("*")))
        mtimes_before = _mtimes([p for p in all_paths if p.is_file()])
        time.sleep(0.01)
        _assemble(fx, replicas=3, now_iso="2026-07-02T00:00:00Z")
        mtimes_after = _mtimes([p for p in all_paths if p.is_file()])
        assert mtimes_before == mtimes_after

    def test_legacy_run_with_replicas_gt_1_refuses(self, tmp_path):
        """Existing run with no `replicas` field in manifest.assembly.yaml."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        # Simulate a legacy run: strip replicas field from manifest.assembly.yaml.
        ma_path = _run_dir_of(fx) / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        ma.pop("replicas", None)
        ma_path.write_text(yaml.dump(ma, sort_keys=False))
        with pytest.raises(assemble_run.AssembleError, match="legacy"):
            _assemble(fx, replicas=3)

    def test_legacy_run_with_force_rebuilds(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=1)
        ma_path = _run_dir_of(fx) / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        ma.pop("replicas", None)
        ma_path.write_text(yaml.dump(ma, sort_keys=False))
        _assemble(fx, replicas=3, force=True)
        ma = yaml.safe_load(ma_path.read_text())
        assert ma["replicas"] == 3
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert "pipelinerun-wl-a|baseline|i3.yaml" in names

    def test_drift_with_force_rmtree_rebuilds(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        # Introduce drift: rewrite manifest.assembly.yaml with a different
        # workload set to simulate a transfer.yaml change.
        ma_path = _run_dir_of(fx) / "manifest.assembly.yaml"
        ma = yaml.safe_load(ma_path.read_text())
        # Simulate drift by patching prior params_hash to a different value.
        # Simpler: overwrite the stored params_hash in run_metadata.
        rm_path = _run_dir_of(fx) / "run_metadata.json"
        import json
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        _assemble(fx, replicas=5, force=True)
        # After force-rebuild, the params_hash matches the current content.
        rm = json.loads(rm_path.read_text())
        assert rm["params_hash"] != "0" * 64
        assert rm["replicas"] == 5

    def test_drift_without_force_refuses(self, tmp_path):
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx, replicas=3)
        rm_path = _run_dir_of(fx) / "run_metadata.json"
        import json
        rm = json.loads(rm_path.read_text())
        rm["params_hash"] = "0" * 64
        rm_path.write_text(json.dumps(rm))
        with pytest.raises(assemble_run.AssembleError, match="content changed"):
            _assemble(fx, replicas=5)

    def test_replicas_arg_rejects_zero_and_negative(self):
        from pipeline.sim2real import _positive_int
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int("0")
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_int("-1")

    def test_default_flow_no_replicas_flag_emits_pipe_shape_i1(self, tmp_path):
        """No --replicas → default 1 → still pipe-shape with |i1 suffix."""
        fx = _make_experiment(tmp_path, algo_names_registered=["sr"],
                              algo_names_manifest=["sr"])
        _assemble(fx)  # no replicas kwarg → default 1
        names = _pipelinerun_files(_cluster_dir_of(fx))
        assert names == [
            "pipelinerun-wl-a|baseline|i1.yaml",
            "pipelinerun-wl-a|sr|i1.yaml",
        ]
        ma = yaml.safe_load(
            (_run_dir_of(fx) / "manifest.assembly.yaml").read_text()
        )
        assert ma["replicas"] == 1
```

- [ ] **Step 5.2: Run tests, verify most fail**

```bash
python -m pytest pipeline/tests/test_assemble_replicas.py -v
```

Expected: `test_fresh_run_replicas_3_emits_three_iterations` and `test_default_flow_no_replicas_flag_emits_pipe_shape_i1` and `test_replicas_arg_rejects_zero_and_negative` pass (they rely only on Task 1–4 work). All others fail with `AssembleError: run directory already exists` — the pre-Task-5 fresh-only path refuses re-assemble.

- [ ] **Step 5.3: Implement the decision tree in `assemble_run`**

In `pipeline/lib/assemble_run.py`, locate the existing block (around line 486):

```python
    run_dir = layout.runs_dir() / run_name
    if run_dir.exists():
        if not force:
            raise AssembleError(
                f"run directory already exists: {run_dir} — pass --force to overwrite"
            )
        shutil.rmtree(run_dir)
```

Replace with the full decision tree. The additive-merge path (grow) writes only the new iterations plus updates `manifest.assembly.yaml` and `run_metadata.json`; it does NOT re-run scenario resolution. Existing tests exercise the fresh-run path, which is unchanged.

Replacement code (paste in place of the block above):

```python
    run_dir = layout.runs_dir() / run_name
    additive_grow_from: int | None = None  # None → fresh full assemble.
    if run_dir.exists():
        prior_ma_path = run_dir / "manifest.assembly.yaml"
        prior_rm_path = run_dir / "run_metadata.json"
        if not (prior_ma_path.exists() and prior_rm_path.exists()):
            raise AssembleError(
                f"run directory '{run_name}' is missing manifest.assembly.yaml or "
                f"run_metadata.json — pass --force to rebuild"
            )
        prior_ma = yaml.safe_load(prior_ma_path.read_text()) or {}
        prior_rm = json.loads(prior_rm_path.read_text())
        prior_replicas = prior_ma.get("replicas") if isinstance(prior_ma, dict) else None

        # Legacy detection first: a run without the `replicas` field predates
        # this PR and cannot be additively merged.
        if prior_replicas is None:
            if not force:
                raise AssembleError(
                    f"run '{run_name}' is in legacy single-replica shape "
                    "(pre-step-5); create a fresh run to use --replicas, "
                    "or pass --force to rebuild."
                )
            shutil.rmtree(run_dir)
            # Fall through to fresh-assemble.
        else:
            # Non-legacy: compare content hashes.
            # New content_hash is computed from a temporary manifest.assembly.yaml
            # snapshot written to a scratch path, hashed the same way. To avoid
            # double-writing, we materialize the assembly slice in memory and
            # hash its canonical bytes directly.
            new_slice = slicer.assembly_slice(manifest)
            new_canonical = yaml.dump(
                new_slice, sort_keys=True, default_flow_style=False,
                allow_unicode=True,
            ).encode("utf-8")
            new_content_hash = hashlib.sha256(new_canonical).hexdigest()
            prior_params_hash = prior_rm.get("params_hash", "")

            if new_content_hash != prior_params_hash:
                if not force:
                    raise AssembleError(
                        f"manifest content changed since last assemble for "
                        f"run '{run_name}'; pass --force to overwrite."
                    )
                # --force + drift: rmtree + fresh rebuild.
                shutil.rmtree(run_dir)
            else:
                # No drift. Apply grow-only guard.
                if replicas < prior_replicas:
                    raise AssembleError(
                        f"run '{run_name}' already has {prior_replicas} "
                        f"replicas; refusing to shrink to {replicas}. "
                        "Replica shrink is tracked in #506."
                    )
                if replicas == prior_replicas:
                    # True no-op. Reset side-band attrs to defaults and return.
                    assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
                    assemble_run.missing_submodules = []  # type: ignore[attr-defined]
                    return
                # replicas > prior_replicas: additive grow.
                additive_grow_from = prior_replicas
```

Then handle the two entry points:

1. **Additive-grow path** (`additive_grow_from is not None`): skip the full assemble body and instead run a minimal write that emits only new iterations + updates manifest.assembly.yaml + run_metadata.json. To keep this contained, factor a helper at the top of the file:

```python
def _additive_grow(
    run_dir: Path,
    manifest: dict,
    *,
    prior_replicas: int,
    new_replicas: int,
    now_iso: str,
    experiment_root: Path,
    translation_hash: str,
    cluster_id: str,
    translation_dir: Path,
    cluster_config: dict,
) -> None:
    """Grow an existing run's replica count from ``prior_replicas`` to
    ``new_replicas`` (``new_replicas > prior_replicas``).

    Preserves existing PipelineRun files (i1..i{prior_replicas}) byte-for-byte
    and by mtime. Emits new files for i{prior_replicas+1}..i{new_replicas}.
    Rewrites ``manifest.assembly.yaml`` with the new replica count. Rewrites
    ``run_metadata.json`` with the new replica count and a new
    ``assembled_at`` timestamp; ``params_hash`` byte-identical to prior
    (drift check already ran and passed).
    """
    # Re-run scenario resolution to obtain workloads + packages + submodule
    # discovery. This is safe because the drift check already established
    # that resolved content is unchanged from the prior assemble.
    layout.set_experiment_root(experiment_root)
    exp_root = layout.experiment_root()
    defaults_dir = exp_root / "baselines" / "defaults"
    framework_defaults = load_defaults_overlay(
        defaults_dir if defaults_dir.exists() else None,
        disable=(manifest.get("defaults") or {}).get("disable") or [],
    )
    tout_path = layout.translation_output_path(translation_hash)
    tout = _translation_ref.read_translation_output(tout_path)
    translated_algos = {a.get("name"): a for a in tout.get("algorithms", []) or []}
    translated_names = set(translated_algos.keys())
    kept_algos, _skipped = filter_algorithms(
        manifest.get("algorithms", []) or [],
        translated_names=translated_names,
    )
    generated_root = translation_dir / "generated"

    packages: list[tuple[str, dict]] = []
    resolved_baselines: dict[str, dict] = {}
    for bl in manifest.get("baselines", []):
        bl_name = bl["name"]
        bundle_path = _resolve_scenario_path(exp_root, bl.get("scenario"), "baseline.yaml")
        overlay_path = generated_root / f"baseline_{bl_name}" / "baseline_config.yaml"
        if not overlay_path.exists():
            legacy_overlay = generated_root / "baseline_config.yaml"
            overlay_path = legacy_overlay if legacy_overlay.exists() else None
        resolved = resolve_baseline(
            bundle_path=bundle_path, overlay_path=overlay_path,
            framework_defaults=framework_defaults,
        )
        resolved_baselines[bl_name] = resolved
        packages.append((bl_name, resolved))
    for algo in kept_algos:
        algo_name = algo["name"]
        base_name = algo["defaults"]
        diffs_path = _resolve_scenario_path(exp_root, algo.get("scenario"), "treatment.yaml")
        overlay_path = generated_root / algo_name / f"{algo_name}_config.yaml"
        resolved = resolve_treatment(
            baseline_resolved=resolved_baselines[base_name],
            diffs_path=diffs_path,
            overlay_path=overlay_path,
        )
        algo_image_ref = translated_algos[algo_name]["image_ref"]
        inject_image_tag(resolved, algo_image_ref)
        packages.append((algo_name, resolved))

    hf_secret = (cluster_config.get("secret_names") or {}).get("hf_token", "hf-secret")
    for _, resolved in packages:
        inject_hf_secret_name(resolved, hf_secret)

    workloads = [_load_workload(exp_root, wl) for wl in manifest.get("workloads", [])]
    pipeline_name = (manifest.get("pipeline") or {}).get("name", "sim2real")
    observe = manifest.get("blis_observe") or {}
    first_baseline = next(
        (resolved for name, resolved in packages if name in resolved_baselines),
        packages[0][1] if packages else {},
    )
    scenarios_list = first_baseline.get("scenario", [])
    model_name = (
        scenarios_list[0].get("model", {}).get("name", "") if scenarios_list else ""
    )
    submodule_shas, submodule_urls, _missing = discover_framework_submodules(_REPO_ROOT)

    generate_pipelineruns(
        run_dir=run_dir,
        packages=packages,
        workloads=workloads,
        run_name=run_dir.name,
        cluster_config=cluster_config,
        pipeline_name=pipeline_name,
        observe=observe,
        model_name=model_name,
        submodule_shas=submodule_shas,
        submodule_urls=submodule_urls,
        iterations=range(prior_replicas + 1, new_replicas + 1),
    )

    # Rewrite manifest.assembly.yaml with new replicas count.
    write_manifest_assembly(run_dir, manifest, now_iso=now_iso, replicas=new_replicas)

    # Rewrite run_metadata.json. params_hash is preserved (drift check passed).
    rm_path = run_dir / "run_metadata.json"
    rm = json.loads(rm_path.read_text())
    rm["replicas"] = new_replicas
    rm["assembled_at"] = now_iso
    rm_path.write_text(json.dumps(rm, indent=2, sort_keys=True) + "\n")
```

Now wire the helper into `assemble_run`. After the decision-tree block, before the `# 2. Load manifest + translation index` step, add:

```python
    if additive_grow_from is not None:
        _additive_grow(
            run_dir,
            manifest,
            prior_replicas=additive_grow_from,
            new_replicas=replicas,
            now_iso=now_iso,
            experiment_root=experiment_root,
            translation_hash=translation_hash,
            cluster_id=cluster_id,
            translation_dir=layout.translation_dir(translation_hash),
            cluster_config=cluster_config,
        )
        # skipped_algorithms/missing_submodules unchanged from prior assemble.
        assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
        assemble_run.missing_submodules = []  # type: ignore[attr-defined]
        return
```

**Important**: `manifest` is loaded early in the decision tree (used for `slicer.assembly_slice(manifest)`), but the code originally loads it in step 2 (`load_manifest`). Move the `load_manifest` call to BEFORE the `run_dir.exists()` block so `manifest` is available for the drift comparison. Concretely, the reordered top of `assemble_run` becomes:

```python
    layout.set_experiment_root(experiment_root)
    assemble_run.skipped_algorithms = []  # type: ignore[attr-defined]
    assemble_run.missing_submodules = []  # type: ignore[attr-defined]

    # 1. Validation (translation + cluster) --------------------------------
    tdir = layout.translation_dir(translation_hash)
    tout_path = layout.translation_output_path(translation_hash)
    if not tdir.exists() or not tout_path.exists():
        raise AssembleError(...)  # unchanged message

    cluster_config = cluster_ops.read_cluster_config(cluster_id)
    if not cluster_config:
        raise AssembleError(...)  # unchanged message

    # 2. Load manifest early (needed for drift comparison on re-assemble)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        raise AssembleError(f"cannot load manifest {manifest_path}: {exc}") from exc

    # 3. Existing-run decision tree (uses `manifest` for drift check)
    run_dir = layout.runs_dir() / run_name
    additive_grow_from: int | None = None
    if run_dir.exists():
        # ... decision-tree block from earlier in this step ...

    if additive_grow_from is not None:
        # ... additive-grow branch from earlier in this step ...
        return
```

The rest of the function (load translation output, filter algos, resolve baselines/treatments, inject, write scenarios, generate PipelineRuns, write run_metadata) becomes the fresh-assemble path, unchanged in behavior for the fresh-run case.

- [ ] **Step 5.4: Run new tests, verify they pass**

```bash
python -m pytest pipeline/tests/test_assemble_replicas.py -v
```

Expected: all 10 tests pass.

- [ ] **Step 5.5: Run full pipeline test suite + lint**

```bash
python -m pytest pipeline/ -v
ruff check pipeline/ --select F
```

Expected: all pass.

- [ ] **Step 5.6: Commit**

```bash
git add pipeline/lib/assemble_run.py pipeline/tests/test_assemble_replicas.py
git commit -m "assemble: additive-merge decision tree (legacy/drift/grow-only/no-op) (#510)"
```

---

## Task 6: CI workflow + stale-reference sweep + PR

Wire the new test file into CI. Sweep for stale references to the old filename shape or single-replica assumptions in docs and comments. Push and open PR.

**Files:**
- Modify: `.github/workflows/test.yml` (add `pipeline/tests/test_assemble_replicas.py` to the pytest arg list)
- Possibly modify: docs/skills that reference old filename shape or single-replica assumptions.

- [ ] **Step 6.1: Add new test file to CI**

Read `.github/workflows/test.yml`; locate the pytest args block; append the new test path. The line list order should match the current CLAUDE.md-documented shape.

```yaml
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_assemble_replicas.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  -v
```

Confirm ordering matches the surrounding YAML style — copy the existing indentation exactly.

- [ ] **Step 6.2: Stale-reference sweep**

Search for references to the old filename shape and single-replica assumptions:

```bash
grep -rn 'pipelinerun-[a-z][a-z0-9-]*-[a-z][a-z0-9-]*\.yaml' pipeline/ docs/ .claude/skills/ README* 2>/dev/null
grep -rn 'single-replica\|one PipelineRun per (workload, package)\|per (workload, package)' pipeline/ docs/ .claude/skills/ README* 2>/dev/null
grep -rn '|i1\||iN\|\.assembly.yaml' docs/ .claude/skills/ 2>/dev/null
```

For each hit: decide **stale** (update in this PR), **still accurate** (skip), or **unrelated** (skip). Common expected hits:

- `pipeline/README.md` — may reference the old assemble output shape. If so, note it in the PR body as deferred to PR 6 per epic ordering, and leave unchanged (PR 6 is the docs PR for this epic per the design doc).
- `pipeline/lib/assemble_run.py` docstring — `generate_pipelineruns` docstring currently says "Filename shape: `pipelinerun-<workload-safe>-<package>.yaml`". Update to reflect the new shape.
- `pipeline/deploy.py:_load_pairs` docstring — currently mentions the legacy shape via the "deviation" comment. Now that runs will consistently emit pipe shape, is that comment still valid? **Yes** — the comment documents that the loader tolerates legacy shape during the rollout. Leave unchanged.

For every hit that's touched, commit in the same PR.

- [ ] **Step 6.3: Update `generate_pipelineruns` docstring**

In `pipeline/lib/assemble_run.py`, update the docstring on `generate_pipelineruns` (around line 346):

```python
    """Emit one PipelineRun YAML per (workload, package, iteration) tuple
    under ``cluster/``.

    Filename shape: ``pipelinerun-<workload-safe>|<package>|i<N>.yaml``,
    where ``<workload-safe>`` is the workload name with ``_`` replaced by
    ``-`` and ``N`` is each element of ``iterations``. The ``|`` separators
    make the derived pair key match the canonical grammar in
    ``pipeline/lib/pairkey.py``.
    """
```

- [ ] **Step 6.4: Run full test suite one more time + lint**

```bash
python -m pytest pipeline/ -v
ruff check pipeline/ .claude/skills/ --select F
```

Expected: all pass.

- [ ] **Step 6.5: Confirm no leaks to parent repo**

```bash
git status
git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
```

Expected: worktree shows the commits from Tasks 1–6; parent repo shows no unexpected modifications (pre-existing modifications listed in the initial `gitStatus` may remain — those are the user's in-progress work on other branches, out of scope).

- [ ] **Step 6.6: Commit CI + docstring changes**

```bash
git add .github/workflows/test.yml pipeline/lib/assemble_run.py
git commit -m "ci: register test_assemble_replicas.py; docstring reflects pipe-shape (#510)"
```

- [ ] **Step 6.7: Push branch**

```bash
git push -u origin refactor/v2-step-5-issue-510-assemble-replicas
```

- [ ] **Step 6.8: Open the PR**

```bash
gh pr create --base refactor/v2-step-5 \
  --title "assemble: additive-merge + --replicas consumption (grow-only) (#510)" \
  --body-file - <<'EOF'
Closes #510.

## Summary

Grows `pipeline/lib/assemble_run.py` from step-1's single-replica shape to
`--replicas N` support with the additive-merge (grow-only) invariant.

- New `--replicas N` argparse arg on `sim2real assemble` (default: 1).
- `manifest.assembly.yaml` gains a `replicas: N` top-level field.
- `params_hash` excludes `replicas` so bumps don't trip drift detection.
- PipelineRun filename shape becomes `pipelinerun-<workload-safe>|<package>|i<N>.yaml`.
- k8s `metadata.name` gains `-i<N>` suffix (always).
- Existing-run decision tree: legacy detection, drift detection, grow-only
  guard, no-op idempotence, additive-merge write.

## Scope decisions (from design doc)

1. **Unified pipe shape.** No `--replicas` defaults to `--replicas 1`,
   which emits `|i1`. Legacy hyphen shape is not preserved as a first-class
   output (rationale: refactored code hasn't shipped; unifying now avoids
   forked shapes downstream).
2. **`manifest.assembly.yaml.replicas` is the source of truth** for current
   replica count on re-assemble (single source; matches step-0's schema).
3. **`params_hash` excludes `replicas`** so drift detection is orthogonal
   to replica-count changes. Preserves "drift detection unchanged" from the
   design.
4. **`_load_pairs` untouched.** Pipe-shape filenames naturally produce
   canonical grammar-matching keys via its existing filename-stem logic.
   No changes needed for PR 2.
5. **`--force` bypasses drift but NOT grow-only.** Follows the design doc's
   "No flag bypasses grow-only in step-5". Shrink is tracked in #506.

## Stale-reference sweep

- `pipeline/lib/assemble_run.py` — `generate_pipelineruns` docstring updated
  to reflect pipe shape.
- `pipeline/README.md` — old filename shape referenced in one place; leaving
  unchanged per epic PR 6 (docs PR) ordering.
- `pipeline/deploy.py:_load_pairs` — "deviation" comment about legacy shape
  is still accurate during the rollout; left unchanged.

## Design doc

See `docs/superpowers/specs/2026-07-07-issue-510-assemble-replicas-design.md`
for full decision-tree table and error messages.

## Base

Base: `refactor/v2-step-5`. Parent epic: #502.
EOF
```

If `gh pr create` fails with a token-scope error, retry with `unset GITHUB_TOKEN GH_TOKEN;` prefixed. Print the PR URL.

---

## Self-Review Checklist

Before handing to executor:

- [ ] Spec's decision-table rows all have a corresponding test in Task 5. ✓ (10 tests cover 10 rows.)
- [ ] `_positive_int` is defined before its first use (Task 1 Step 1.3 places it above `_build_parser`). ✓
- [ ] `iteration` param and `iterations` param naming consistent. ✓ (`iteration` singular for `make_pipelinerun_scenario`; `iterations` plural for `generate_pipelineruns`.)
- [ ] `replicas` default value is `1` everywhere (`_positive_int`, `write_manifest_assembly`, `assemble_run`). ✓
- [ ] No task references types/symbols not defined in earlier tasks. ✓
- [ ] Additive-grow path preserves `params_hash` byte-identical. ✓ (Task 5's `_additive_grow` doesn't recompute; reuses prior.)
- [ ] Existing tests updated in the same task that breaks them (Task 2 fixes filenames/names atomically). ✓
- [ ] CI workflow update included. ✓ (Task 6.)
- [ ] PR body covers scope decisions from the spec. ✓
