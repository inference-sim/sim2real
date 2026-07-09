# Progress ConfigMap: Scope by Scenario Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Include the `scenario` field in the progress ConfigMap name so cross-experiment-root runs sharing a run name no longer collide (issue #551), with a one-shot legacy-name rename fallback so in-flight runs migrate invisibly.

**Architecture:** `sim2real assemble` already reads `scenario` from `transfer.yaml` — piggyback on that by writing `scenario` into `run_metadata.json` alongside the fields already there (`run_name`, `cluster_id`, `params_hash`, etc.). `deploy.py` learns `scenario` by reading `run_metadata.json` (a helper it already has for `cluster_id`), then threads it into `ConfigMapProgressStore(namespace, run_name=..., scenario=...)`. The ConfigMap name changes from `sim2real-progress-<run>` to `sim2real-progress-<scenario>-<run>`. On first `load()`, if the new-name CM is absent, look for the legacy `sim2real-progress-<run>` CM; if present, copy its data into the new-name CM and delete the legacy one. `assemble` needs no other change; `deploy.py` uses a single new helper `_make_progress_store(namespace, run_dir)` to centralize the 6 construction sites.

**Tech Stack:** Python 3.10+, pytest, subprocess/kubectl, YAML/JSON.

## Global Constraints

- ConfigMap name must remain a valid Kubernetes name: lowercase `[a-z0-9.\-]`, max 253 chars.
- Names collision-free per `(scenario, run_name)` — namespaced to `(kind, namespace, name)` in k8s.
- No change to `sim2real assemble`'s user-visible CLI or public schema outside of one added JSON key in `run_metadata.json`.
- Legacy migration is one-shot and idempotent (safe under crash between steps).
- Every file operation must target the worktree path `.claude/worktrees/issue-551-configmap-scenario/…` — not the parent repo.

---

## File Structure

- Modify: `pipeline/lib/progress.py`
  - Extend `ConfigMapProgressStore.__init__` with `scenario: str = ""` kwarg. Name formula becomes `sim2real-progress-<scenario>-<run>` when both are supplied.
  - Store the legacy name (`sim2real-progress-<run>`) as `_legacy_configmap_name` on the instance when `scenario` is provided AND `run_name` is provided.
  - Extend `load()` to fall back to the legacy CM on NotFound, migrate its data into the new CM, delete the legacy CM, and return the data.
  - Add labels `sim2real.scenario`, `sim2real.run` on `save()` when set.

- Modify: `pipeline/lib/assemble_run.py`
  - Add `scenario` (string) to the `run_metadata.json` dict written at `assemble_run.py:951-963`. The `scenario` string is read from `manifest` (the same manifest object already available in the caller `pipeline/sim2real.py:_cmd_assemble` and passed to `assemble_run`) — this is a new keyword arg to the `assemble_run` function.
  - Update the callsite in `pipeline/sim2real.py:_cmd_assemble` to pass `scenario=manifest["scenario"]`.

- Modify: `pipeline/deploy.py`
  - Add `_make_progress_store(namespace, run_dir)` helper that reads `run_metadata.json:scenario` and constructs `ConfigMapProgressStore(namespace, run_name=run_dir.name, scenario=scenario)`.
  - Replace the 6 `ConfigMapProgressStore(primary_ns, run_name=run_dir.name)` construction sites with `_make_progress_store(primary_ns, run_dir)`.
  - Import consolidation: keep the local `from pipeline.lib.progress import ConfigMapProgressStore` imports in place (they're already there), or make `_make_progress_store` do the import itself.

- Modify: `pipeline/tests/test_progress.py`
  - Extend `ConfigMapProgressStore` tests to cover:
    - `scenario` alone (no run_name) produces `sim2real-progress-<scenario>`
    - `scenario` + `run_name` produces `sim2real-progress-<scenario>-<run>`
    - `scenario` sanitization (underscore → hyphen, uppercase → lowercase)
    - Overlong `scenario` + `run_name` (>253 chars) raises
    - `save()` emits labels `sim2real.scenario`, `sim2real.run` when both supplied
    - `load()` legacy rename-on-first-read: new-name NotFound + legacy present → migration
    - `load()` legacy rename: new-name NotFound + legacy NotFound → returns {}
    - `load()` legacy rename: new-name present → does NOT check legacy

- Modify: `pipeline/tests/test_assemble_run.py`
  - Extend a `write_run_metadata`-covering test (or add one) to assert the new `scenario` field is written.

- Modify: `pipeline/README.md`
  - Update the `run_metadata.json` shape section (if the schema is documented there) to include `scenario`.

- Modify: `CLAUDE.md` (project instructions)
  - Update the `ConfigMap sim2real-progress-{run}` line in the workspace-artifacts table.

## Cross-System Dependency Trace

**Upstream (produces inputs):**
- `sim2real.py:_cmd_assemble` reads `transfer.yaml` → passes `scenario` to `assemble_run.assemble_run()` → written to `run_metadata.json`.
- `sim2real translation register` and `sim2real translate` — no interaction (scenario is not a translation-slice concern).

**Downstream (consumes outputs):**
- `deploy.py` reads `run_metadata.json:scenario` at each `_cmd_*` — used only for progress store name.
- No other consumer of `run_metadata.json` schema depends on it: `_load_run_cluster_config` reads `cluster_id`; `_ensure_image_ref_recorded` and `_cmd_build` read `component_image`, `registry`, `algorithms[]`. Adding an extra key is a superset — safe.
- `sim2real.py list runs` reads `run_metadata.json` fields (`translation_hash`, `cluster_id`, `assembled_at`) — extra key is ignored.

**Legacy migration blast radius:**
- Existing `sim2real-progress-<run>` ConfigMaps: rename-on-first-read handles them.
- Once migrated, the legacy CM is deleted; a second call becomes a plain NotFound → returns {}.

## Acceptance Criteria (traced from issue #551 verification checklist)

- [ ] AC1: `sim2real-progress-<scenario>-<run>` created with correct name on `deploy.py run` against a fresh scenario/run pair.
- [ ] AC2: Second experiment root with the same run name lands in a distinct ConfigMap (no bleed).
- [ ] AC3: `deploy.py status`, `collect`, `reset`, `wipe`, `run --remote` (all 6 store instantiations) resolve to the new name.
- [ ] AC4: Legacy-name migration — pre-existing `sim2real-progress-<run>` ConfigMap is picked up on first load, its data migrated into the new-name ConfigMap, and the legacy CM is deleted.
- [ ] AC5: `deploy.py` fails cleanly if `run_metadata.json:scenario` is missing/empty (defense-in-depth against a run that reached deploy without a scenario, e.g. an old run assembled before this change).

---

## Task 1: ConfigMapProgressStore — accept scenario, compose name, migrate legacy

**Files:**
- Modify: `pipeline/lib/progress.py:19-97`
- Test: `pipeline/tests/test_progress.py`

**Interfaces:**
- Consumes: nothing from prior tasks (foundational).
- Produces:
  - `ConfigMapProgressStore(namespace: str, *, run_name: str = "", scenario: str = "")` — constructor gains keyword `scenario`. When both `scenario` and `run_name` are supplied, `configmap_name` becomes `sim2real-progress-<scenario>-<run>`; when only `run_name` is supplied, the name remains `sim2real-progress-<run>` (unchanged, for backward-compat with existing tests and legacy call sites); when neither is supplied, the name is `sim2real-progress`.
  - `_legacy_configmap_name: str | None` — set to `sim2real-progress-<run>` when `scenario` and `run_name` are both supplied; `None` otherwise. `load()` falls back to this on NotFound.
  - `save()` emits labels `sim2real.scenario: <scenario>` and `sim2real.run: <run>` in the ConfigMap metadata when both are supplied.

- [ ] **Step 1: Read the current constructor and legacy name/sanitization logic**

Read `pipeline/lib/progress.py` — confirm the sanitize helper regex and the 253-char cap logic are what you'll extend.

- [ ] **Step 2: Write failing tests for the new scenario field (name composition)**

Append to `pipeline/tests/test_progress.py`:

```python
def test_configmap_name_includes_scenario_and_run():
    """scenario + run_name yields sim2real-progress-<scenario>-<run>."""
    store = ConfigMapProgressStore(
        "sim2real-ns", run_name="trial-1", scenario="softr"
    )
    assert store.configmap_name == "sim2real-progress-softr-trial-1"


def test_configmap_scenario_only_no_run_name():
    """scenario without run_name yields sim2real-progress-<scenario>."""
    store = ConfigMapProgressStore("sim2real-ns", scenario="softr")
    assert store.configmap_name == "sim2real-progress-softr"


def test_configmap_scenario_sanitized_lowercase_and_underscore():
    """scenario is sanitized like run_name: lowercase, underscore→hyphen."""
    store = ConfigMapProgressStore(
        "sim2real-ns", run_name="trial-1", scenario="Soft_R"
    )
    assert store.configmap_name == "sim2real-progress-soft-r-trial-1"


def test_configmap_scenario_plus_run_too_long_raises():
    """scenario + run combined that exceeds 253-char CM name limit is rejected."""
    with pytest.raises(ValueError, match="invalid ConfigMap name"):
        ConfigMapProgressStore(
            "sim2real-ns", run_name="r" * 200, scenario="s" * 100
        )


def test_configmap_legacy_name_recorded_when_scenario_and_run_supplied():
    """_legacy_configmap_name is sim2real-progress-<run> when scenario provided."""
    store = ConfigMapProgressStore(
        "sim2real-ns", run_name="trial-1", scenario="softr"
    )
    assert store._legacy_configmap_name == "sim2real-progress-trial-1"


def test_configmap_legacy_name_none_when_no_scenario():
    """_legacy_configmap_name is None when scenario is omitted (nothing to migrate from)."""
    store = ConfigMapProgressStore("sim2real-ns", run_name="trial-1")
    assert store._legacy_configmap_name is None
```

- [ ] **Step 3: Run tests, confirm they fail**

```
python -m pytest pipeline/tests/test_progress.py -v -k "scenario or legacy_name"
```

Expected: failures (missing `scenario` kwarg, missing `_legacy_configmap_name`).

- [ ] **Step 4: Extend the constructor**

Replace the current `__init__` body with:

```python
def __init__(
    self,
    namespace: str,
    *,
    run_name: str = "",
    scenario: str = "",
) -> None:
    if not namespace:
        raise ValueError("ConfigMapProgressStore requires a non-empty namespace")
    self._namespace = namespace
    self._run_name = run_name
    self._scenario = scenario

    sanitized_run = self._sanitize(run_name) if run_name else ""
    sanitized_scenario = self._sanitize(scenario) if scenario else ""

    parts = [self.BASE_NAME]
    if sanitized_scenario:
        parts.append(sanitized_scenario)
    if sanitized_run:
        parts.append(sanitized_run)
    candidate = "-".join(parts)

    if len(candidate) > 253 or not self._K8S_NAME_RE.match(candidate):
        raise ValueError(
            f"scenario={scenario!r}, run_name={run_name!r} produces "
            f"invalid ConfigMap name {candidate!r} — must be lowercase "
            f"alphanumeric, hyphens, or dots, max 253 chars"
        )
    self.configmap_name = candidate

    # Legacy name (pre-#551): sim2real-progress-<run>. Only set when
    # scenario is supplied AND run_name is supplied — otherwise there is
    # no legacy to migrate from (the new-name and legacy-name would
    # collide or both be BASE_NAME).
    if sanitized_scenario and sanitized_run:
        self._legacy_configmap_name = f"{self.BASE_NAME}-{sanitized_run}"
    else:
        self._legacy_configmap_name = None


@staticmethod
def _sanitize(value: str) -> str:
    """Sanitize a name fragment for a Kubernetes resource name."""
    return re.sub(r"[^a-z0-9.\-]", "-", value.lower()).strip("-")
```

Also add the class-level constant at the top of the class body (unchanged, but noted):
```python
_K8S_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")
```

Delete the old inline sanitize in `__init__` (replaced by the static method).

- [ ] **Step 5: Run tests, confirm name composition tests pass**

```
python -m pytest pipeline/tests/test_progress.py -v -k "scenario or legacy_name"
```

Expected: all pass.

- [ ] **Step 6: Ensure old tests still pass (run_name-only compat)**

```
python -m pytest pipeline/tests/test_progress.py -v
```

Expected: all pass, including `test_configmap_name_includes_run_name` (which uses `run_name="experiment-1"` and expects `sim2real-progress-experiment-1`).

- [ ] **Step 7: Write failing tests for legacy rename-on-first-read**

Append to `pipeline/tests/test_progress.py`:

```python
def test_load_new_name_present_does_not_check_legacy():
    """When the new-name CM exists, load() returns it without touching legacy."""
    data = '{"wl-smoke|baseline|i1": {"status": "done"}}'
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout=data)
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        result = store.load()
    assert result == {"wl-smoke|baseline|i1": {"status": "done"}}
    # Only one kubectl call — get on the new name
    assert mock.call_count == 1


def test_load_new_name_notfound_legacy_notfound_returns_empty():
    """When both new-name and legacy CMs are absent, load() returns {}."""
    notfound = MagicMock(
        returncode=1, stdout="",
        stderr='Error from server (NotFound): configmaps "sim2real-progress-softr-trial-1" not found',
    )
    notfound_legacy = MagicMock(
        returncode=1, stdout="",
        stderr='Error from server (NotFound): configmaps "sim2real-progress-trial-1" not found',
    )
    with patch("subprocess.run", side_effect=[notfound, notfound_legacy]) as mock:
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        result = store.load()
    assert result == {}
    assert mock.call_count == 2


def test_load_migrates_legacy_when_new_name_notfound():
    """When new-name NotFound and legacy present, migrate then return legacy's data."""
    legacy_data = '{"wl-smoke|baseline|i1": {"status": "done"}}'
    calls = [
        # 1st: get new-name → NotFound
        MagicMock(
            returncode=1, stdout="",
            stderr='Error from server (NotFound): configmaps "sim2real-progress-softr-trial-1" not found',
        ),
        # 2nd: get legacy → returns data
        MagicMock(returncode=0, stdout=legacy_data),
        # 3rd: apply new-name (migration write) → success
        MagicMock(returncode=0),
        # 4th: delete legacy → success
        MagicMock(returncode=0),
    ]
    with patch("subprocess.run", side_effect=calls) as mock:
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        result = store.load()
    assert result == {"wl-smoke|baseline|i1": {"status": "done"}}
    # Verify migration write targeted the new-name CM
    apply_args = mock.call_args_list[2]
    cmd = apply_args[0][0]
    assert cmd == ["kubectl", "apply", "-f", "-"]
    import json as _json
    cm = _json.loads(apply_args[1]["input"])
    assert cm["metadata"]["name"] == "sim2real-progress-softr-trial-1"
    # Verify legacy delete
    delete_args = mock.call_args_list[3]
    assert delete_args[0][0][:3] == ["kubectl", "delete", "configmap"]
    assert "sim2real-progress-trial-1" in delete_args[0][0]


def test_load_no_legacy_name_skips_migration():
    """When scenario is omitted (legacy_name is None), load() does NOT fall back."""
    notfound = MagicMock(
        returncode=1, stdout="",
        stderr='Error from server (NotFound): configmaps "sim2real-progress-trial-1" not found',
    )
    with patch("subprocess.run", side_effect=[notfound]) as mock:
        # No scenario → _legacy_configmap_name is None → single kubectl get
        store = ConfigMapProgressStore("sim2real-ns", run_name="trial-1")
        result = store.load()
    assert result == {}
    assert mock.call_count == 1
```

- [ ] **Step 8: Run legacy tests, confirm they fail**

```
python -m pytest pipeline/tests/test_progress.py -v -k "load_new_name or load_migrates or load_no_legacy"
```

Expected: failures (behavior not implemented).

- [ ] **Step 9: Implement legacy rename-on-first-read in `load()`**

Replace the current `load()` with:

```python
def load(self) -> dict:
    raw = self._get_cm(self.configmap_name)
    if raw is not None:
        # ConfigMap exists (may be empty data)
        return self._parse_data(raw, self.configmap_name)

    # New-name CM is NotFound. Fall back to legacy if applicable (#551).
    if self._legacy_configmap_name:
        legacy_raw = self._get_cm(self._legacy_configmap_name)
        if legacy_raw is not None:
            data = self._parse_data(legacy_raw, self._legacy_configmap_name)
            # Migrate: write new-name CM, then delete legacy.
            # Order matters — if the delete fails, the next load() will
            # still find the new-name CM and skip the legacy check.
            self.save(data)
            self._delete_cm(self._legacy_configmap_name)
            return data

    return {}


def _get_cm(self, name: str) -> str | None:
    """Return raw data-key contents, or None if the ConfigMap is NotFound.

    Raises RuntimeError on any other kubectl error.
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "configmap", name,
             "-n", self._namespace,
             "-o", f"jsonpath={{.data.{self.DATA_KEY}}}"],
            check=False, text=True, capture_output=True,
        )
    except OSError as exc:
        raise RuntimeError(f"kubectl not available: {exc}") from exc
    if result.returncode != 0:
        if "(NotFound)" in result.stderr:
            return None
        raise RuntimeError(
            f"kubectl get configmap {name} failed: {result.stderr.strip()}"
        )
    return result.stdout


def _parse_data(self, raw: str, source_name: str) -> dict:
    raw = raw.strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Corrupt ConfigMap {source_name} in {self._namespace}"
        ) from exc


def _delete_cm(self, name: str) -> None:
    """Best-effort delete of a ConfigMap. Ignores NotFound (race)."""
    try:
        result = subprocess.run(
            ["kubectl", "delete", "configmap", name,
             "-n", self._namespace, "--ignore-not-found"],
            check=False, text=True, capture_output=True,
        )
    except OSError as exc:
        raise RuntimeError(
            f"kubectl delete configmap {name} failed: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"kubectl delete configmap {name} failed: {result.stderr.strip()}"
        )
```

- [ ] **Step 10: Run all progress tests**

```
python -m pytest pipeline/tests/test_progress.py -v
```

Expected: all pass (including the legacy-rename tests).

- [ ] **Step 11: Write failing test for discovery labels on save()**

Append:

```python
def test_save_emits_discovery_labels_when_scenario_and_run_supplied():
    """save() sets sim2real.scenario and sim2real.run labels for kubectl -l filtering."""
    data = {"wl-x|y|i1": {"status": "done"}}
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0)
        store = ConfigMapProgressStore(
            "sim2real-ns", run_name="trial-1", scenario="softr"
        )
        store.save(data)
    import json as _json
    cm = _json.loads(mock.call_args[1]["input"])
    labels = cm["metadata"].get("labels") or {}
    assert labels.get("sim2real.scenario") == "softr"
    assert labels.get("sim2real.run") == "trial-1"


def test_save_no_labels_when_scenario_missing():
    """save() omits labels block when scenario is not supplied."""
    data = {"wl-x|y|i1": {"status": "done"}}
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0)
        store = ConfigMapProgressStore("sim2real-ns", run_name="trial-1")
        store.save(data)
    import json as _json
    cm = _json.loads(mock.call_args[1]["input"])
    # No labels metadata at all (or empty) — either is acceptable
    labels = cm["metadata"].get("labels") or {}
    assert labels == {}
```

- [ ] **Step 12: Run label tests, confirm failure**

```
python -m pytest pipeline/tests/test_progress.py -v -k "labels"
```

Expected: failure (labels not being written).

- [ ] **Step 13: Add labels to save()**

Modify `save()` — replace the `cm = { ... }` dict with:

```python
metadata = {
    "name": self.configmap_name,
    "namespace": self._namespace,
}
if self._scenario and self._run_name:
    metadata["labels"] = {
        "sim2real.scenario": self._sanitize(self._scenario),
        "sim2real.run": self._sanitize(self._run_name),
    }
cm = {
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": metadata,
    "data": {
        self.DATA_KEY: json.dumps(data, indent=2),
    },
}
```

- [ ] **Step 14: Run all progress tests**

```
python -m pytest pipeline/tests/test_progress.py -v
```

Expected: all pass.

- [ ] **Step 15: Commit**

```
git add pipeline/lib/progress.py pipeline/tests/test_progress.py
git commit -m "feat(progress): scope ConfigMap by (scenario, run) with legacy rename fallback (#551)"
```

---

## Task 2: assemble writes scenario into run_metadata.json

**Files:**
- Modify: `pipeline/lib/assemble_run.py:951-963` (the `write_run_metadata` call) and the `assemble_run()` signature.
- Modify: `pipeline/sim2real.py` — the `_cmd_assemble` call to `assemble_run(...)`.
- Test: `pipeline/tests/test_assemble_run.py`

**Interfaces:**
- Consumes: `scenario: str` from `manifest["scenario"]` in `_cmd_assemble` (already read and validated at `pipeline/sim2real.py:831-833`).
- Produces:
  - `run_metadata.json` gains a top-level `"scenario": <string>` field, non-null when assemble is called with a valid scenario.
  - `assemble_run()` gains a keyword-only `scenario: str` parameter, forwarded into `run_metadata.json`.

- [ ] **Step 1: Locate the assemble_run() signature and its call site**

```
grep -n "^def assemble_run" pipeline/lib/assemble_run.py
grep -n "assemble_run(" pipeline/sim2real.py
```

Confirm signature and the single caller. If there are multiple callers (e.g. tests), note them — the new param must be plumbed at each.

- [ ] **Step 2: Write failing test for the new run_metadata.json field**

In `pipeline/tests/test_assemble_run.py`, add a test that exercises `write_run_metadata` (or the end-to-end assemble flow if the file uses fixtures). If there's an existing test that runs a full assemble against a temp dir, extend it; otherwise add a focused one:

```python
def test_write_run_metadata_includes_scenario(tmp_path):
    """run_metadata.json carries the scenario field."""
    from pipeline.lib.assemble_run import write_run_metadata
    write_run_metadata(
        tmp_path,
        {
            "version": 1,
            "run_name": "trial-1",
            "translation_hash": "abc",
            "cluster_id": "c1",
            "params_hash": "def",
            "image_tag": "",
            "replicas": 1,
            "assembled_at": "2026-07-09T00:00:00Z",
            "scenario": "softr",
        },
    )
    import json
    meta = json.loads((tmp_path / "run_metadata.json").read_text())
    assert meta["scenario"] == "softr"
```

- [ ] **Step 3: Run the test, confirm it passes (write_run_metadata is schema-agnostic)**

```
python -m pytest pipeline/tests/test_assemble_run.py -v -k "scenario"
```

Expected: PASS immediately — `write_run_metadata` just serializes the dict it's given. This confirms the storage layer needs no code change.

- [ ] **Step 4: Write failing test for `assemble_run` plumbing scenario through**

If the test suite has an end-to-end assemble test, extend it to assert `scenario` is present in `run_metadata.json`. If not, add a targeted assertion where `assemble_run` is invoked directly. Prefer extending an existing test to avoid rebuilding fixtures.

Look for a test that already invokes `assemble_run(...)` and reads the produced `run_metadata.json`. Extend it with:

```python
meta = json.loads((run_dir / "run_metadata.json").read_text())
assert meta["scenario"] == "<scenario-in-the-fixture>"
```

- [ ] **Step 5: Run the test, confirm it fails**

Expected: `scenario` key is absent from the written meta.

- [ ] **Step 6: Plumb scenario into `assemble_run()`**

Update the `assemble_run(...)` signature to accept `scenario: str` (keyword-only), and add it to the `write_run_metadata(...)` dict at `pipeline/lib/assemble_run.py:951-963`:

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
        "scenario": scenario,
    },
)
```

- [ ] **Step 7: Update the caller in `pipeline/sim2real.py:_cmd_assemble`**

Find the `assemble_run(...)` call in `_cmd_assemble` and pass `scenario=scenario` (the `scenario` local is already computed and validated a few lines earlier at `pipeline/sim2real.py:831-833` or the assemble-command equivalent — verify the variable name).

- [ ] **Step 8: Run the end-to-end test, confirm pass**

```
python -m pytest pipeline/tests/test_assemble_run.py -v
```

Expected: all pass.

- [ ] **Step 9: Run all assemble/sim2real tests**

```
python -m pytest pipeline/tests/test_assemble_run.py pipeline/tests/test_sim2real.py pipeline/tests/test_resolve.py -v
```

Expected: all pass. `test_resolve.py:96` writes a run_metadata.json fixture without `scenario` — the resolve consumer must not require it (verify), or update that fixture to include a `scenario` field.

- [ ] **Step 10: Commit**

```
git add pipeline/lib/assemble_run.py pipeline/sim2real.py pipeline/tests/test_assemble_run.py
git commit -m "feat(assemble): record scenario in run_metadata.json (#551)"
```

---

## Task 3: deploy.py — read scenario, thread it into every store

**Files:**
- Modify: `pipeline/deploy.py` — add `_make_progress_store(namespace, run_dir)` helper; replace 6 direct `ConfigMapProgressStore(...)` construction sites.

**Interfaces:**
- Consumes:
  - `ConfigMapProgressStore(namespace, run_name, scenario=...)` from Task 1.
  - `run_metadata.json:scenario` from Task 2.
- Produces:
  - `_make_progress_store(namespace: str, run_dir: Path) -> ConfigMapProgressStore` — reads `run_metadata.json`, extracts `scenario`, returns a store scoped to `(scenario, run_dir.name)`. Emits the acceptance-criterion error string and exits when `scenario` is missing/empty (AC5).

- [ ] **Step 1: Read the 6 store construction sites and confirm they all have `run_dir` in scope**

```
grep -n "ConfigMapProgressStore" pipeline/deploy.py
```

Expected sites: `pipeline/deploy.py:690, 1621, 2714, 3158, 3213, 3516`.

- [ ] **Step 2: Write failing test for `_make_progress_store` behavior**

Add to `pipeline/tests/test_deploy_run.py` (or a small new file — check the convention):

```python
def test_make_progress_store_reads_scenario_from_run_metadata(tmp_path, monkeypatch):
    """_make_progress_store reads scenario from run_metadata.json."""
    import json
    from pipeline import deploy
    run_dir = tmp_path / "runs" / "trial-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(json.dumps({
        "version": 1,
        "run_name": "trial-1",
        "scenario": "softr",
        "cluster_id": "c1",
    }))
    store = deploy._make_progress_store("sim2real-ns", run_dir)
    assert store.configmap_name == "sim2real-progress-softr-trial-1"


def test_make_progress_store_missing_scenario_exits(tmp_path, capsys):
    """_make_progress_store exits with error when scenario is missing."""
    import json
    from pipeline import deploy
    run_dir = tmp_path / "runs" / "trial-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(json.dumps({
        "version": 1,
        "run_name": "trial-1",
        "cluster_id": "c1",
    }))
    with pytest.raises(SystemExit):
        deploy._make_progress_store("sim2real-ns", run_dir)
    err = capsys.readouterr().err
    assert "scenario" in err.lower()
    assert "re-assemble" in err.lower() or "assemble" in err.lower()


def test_make_progress_store_missing_run_metadata_exits(tmp_path, capsys):
    """_make_progress_store exits with error when run_metadata.json is missing."""
    from pipeline import deploy
    run_dir = tmp_path / "runs" / "trial-1"
    run_dir.mkdir(parents=True)
    with pytest.raises(SystemExit):
        deploy._make_progress_store("sim2real-ns", run_dir)
```

- [ ] **Step 3: Run test, confirm failure**

```
python -m pytest pipeline/tests/test_deploy_run.py -v -k "make_progress_store"
```

Expected: failure — `_make_progress_store` does not exist.

- [ ] **Step 4: Add `_make_progress_store` helper**

Add near the existing `_load_run_cluster_config` (around line 174) in `pipeline/deploy.py`:

```python
def _make_progress_store(namespace: str, run_dir: Path):
    """Construct a ConfigMapProgressStore scoped to this run's (scenario, run_name).

    Reads ``run_metadata.json:scenario`` — written by ``sim2real assemble``.
    Exits with an operator-friendly error when the field is missing (e.g. a
    run assembled before issue #551, or a corrupt metadata file). Callers get
    a store whose ConfigMap name is unique across experiment roots.
    """
    from pipeline.lib.progress import ConfigMapProgressStore
    meta_path = run_dir / "run_metadata.json"
    if not meta_path.exists():
        err(f"run_metadata.json not found at {meta_path} — "
            f"run 'sim2real assemble --run {run_dir.name}' first.")
        sys.exit(1)
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        err(f"run_metadata.json is not valid JSON: {exc}. "
            f"Re-run 'sim2real assemble --run {run_dir.name}'.")
        sys.exit(1)
    scenario = (meta.get("scenario") or "").strip() if isinstance(meta, dict) else ""
    if not scenario:
        err(f"run_metadata.json is missing 'scenario' — re-run "
            f"'sim2real assemble --run {run_dir.name}' to record it "
            f"(added in issue #551).")
        sys.exit(1)
    return ConfigMapProgressStore(
        namespace, run_name=run_dir.name, scenario=scenario
    )
```

- [ ] **Step 5: Run the new tests, confirm they pass**

```
python -m pytest pipeline/tests/test_deploy_run.py -v -k "make_progress_store"
```

Expected: all pass.

- [ ] **Step 6: Replace each of the 6 direct constructions**

For each site (`deploy.py:690, 1621, 2714, 3158, 3213, 3516`), replace:

```python
store = ConfigMapProgressStore(primary_ns, run_name=run_dir.name)
```

with:

```python
store = _make_progress_store(primary_ns, run_dir)
```

(For `deploy.py:3516`, replace `namespace` for `primary_ns`.)

Leave the `from pipeline.lib.progress import ConfigMapProgressStore` local imports in place — they're now unused, but ruff `--select F` will flag them (see next step).

- [ ] **Step 7: Remove the now-unused local imports**

At each of the 6 sites, delete the `from pipeline.lib.progress import ConfigMapProgressStore` line above the (now removed) construction call. Do not remove the type — it's still used in the module's overall closure.

- [ ] **Step 8: Run lint to catch dangling imports**

```
ruff check pipeline/ .claude/skills/ --select F
```

Expected: clean.

- [ ] **Step 9: Run the deploy test suite**

```
python -m pytest pipeline/tests/test_deploy_run.py pipeline/tests/test_deploy_reset.py pipeline/tests/test_deploy_collect.py pipeline/tests/test_deploy_status.py pipeline/tests/test_deploy_remote.py pipeline/tests/test_deploy_pairs.py -v
```

Expected: all pass. Failing tests likely mean an existing fixture writes a `run_metadata.json` without `scenario`; update the fixture. Do NOT weaken the guard.

- [ ] **Step 10: Commit**

```
git add pipeline/deploy.py pipeline/tests/test_deploy_run.py
git commit -m "feat(deploy): thread scenario into ConfigMapProgressStore via run_metadata.json (#551)"
```

---

## Task 4: Documentation sweep

**Files:**
- Modify: `pipeline/README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- No code changes. Purely doc updates so the README's `run_metadata.json` schema section and the CLAUDE.md workspace-artifacts table match the new state.

- [ ] **Step 1: Grep for `sim2real-progress` and `run_metadata.json` references**

```
grep -rn "sim2real-progress\|run_metadata.json\|ConfigMap.*progress" --include="*.md" --include="*.py" .
```

For each hit outside `pipeline/lib/progress.py` and its tests, decide: stale (update), still accurate (leave), or unrelated (leave).

- [ ] **Step 2: Update `pipeline/README.md`**

Find the `run_metadata.json` schema section (search for `"assembled_at"` or `"params_hash"` in a schema block). Add `"scenario"` to the field list. If the ConfigMap naming is documented, update `sim2real-progress-<run>` to `sim2real-progress-<scenario>-<run>`.

- [ ] **Step 3: Update `CLAUDE.md`**

The workspace-artifacts table (search for `sim2real-progress-{run}`) has one row for the progress ConfigMap. Update to `sim2real-progress-{scenario}-{run}` and refresh the description.

- [ ] **Step 4: Commit**

```
git add pipeline/README.md CLAUDE.md
git commit -m "docs: reflect (scenario, run) ConfigMap scoping in README + CLAUDE.md (#551)"
```

---

## Task 5: Full-suite verification

**Files:**
- No file changes.

**Interfaces:**
- Consumes: everything from Tasks 1-4.

- [ ] **Step 1: Run the full test suite (CI-parity)**

```
python -m pytest pipeline/ \
  pipeline/tests/test_layout.py \
  pipeline/tests/test_cluster_ops.py \
  pipeline/tests/test_cluster_py.py \
  pipeline/tests/test_slicer.py \
  pipeline/tests/test_sim2real.py \
  pipeline/tests/test_assemble_run.py \
  pipeline/tests/test_translation_ref.py \
  pipeline/tests/test_translate.py \
  pipeline/tests/test_build.py \
  pipeline/tests/test_pairkey.py \
  pipeline/tests/test_load_pairs.py \
  .claude/skills/sim2real-analyze/tests/ \
  .claude/skills/sim2real-bootstrap/tests/ \
  .claude/skills/sim2real-translate/tests/ \
  .claude/skills/sim2real-check/tests/ \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run lint (CI-parity)**

```
ruff check pipeline/ .claude/skills/ --select F
```

Expected: clean.

- [ ] **Step 3: Trace each acceptance criterion end-to-end**

For each AC1-AC5 in this plan's "Acceptance Criteria" section, point to the test that covers it. If any AC has no test, add one before proceeding.

- [ ] **Step 4: Confirm parent repo has no leaked changes**

```
git -C /Users/kalantar/projects/go.workspace/src/github.com/inference-sim/sim2real status
```

Expected: only untracked files present at session start (`docs/superpowers/plans/2026-06-03-*.md`, `graphify-out/`, `scratch/`, etc.) — no changes to tracked files. If the parent shows modifications to tracked files, an edit leaked out of the worktree.

## Self-Review

**1. Spec coverage:**
- AC1 (fresh CM has new name) — Task 3 tests + Task 1 name-composition tests.
- AC2 (no bleed across experiment roots) — implicit in Task 1 name uniqueness; the name formula makes bleed impossible.
- AC3 (all 6 call sites resolve to new name) — Task 3 replaces all 6 sites via `_make_progress_store`.
- AC4 (legacy migration) — Task 1 legacy rename-on-first-read tests.
- AC5 (loud failure on missing scenario) — Task 3 `_make_progress_store` guard + test.
- Optional labels — Task 1 label tests.

**2. Placeholder scan:** No TBDs, no "add appropriate error handling", no "similar to Task N" references without inlined code.

**3. Type consistency:** `ConfigMapProgressStore(namespace, run_name=..., scenario=...)` signature matches across Tasks 1 and 3. `_make_progress_store` return type is documented. `run_metadata.json:scenario` is a `str`, non-empty when written, and `_make_progress_store` guards with `.strip()`.
