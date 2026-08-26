# Pod CPU/Memory Resources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use `- [ ]` for tracking.

**Goal:** Emit `resources` for decode and prefill — from `config.md` when it states them, otherwise fixed generous limits with modest explicit requests.

**Spec:** Issue #850, narrowed by the operator to: *use config.md's cpu/memory req/limits when stated; otherwise generous defaults; decode needs more than prefill; `pd-infocomm-2/baselines/baseline.yaml` is a good starting point; start with fixed values and learn.*

**Scope:** the bootstrap skill only. Nothing in `pipeline/`.

## Facts established before writing code

> **CORRECTION — this plan was written before the code and two of its "facts" were
> wrong. They are struck through below rather than deleted, because the wrong
> reasoning leaked into the first implementation and into `SKILL.md`, and knowing
> that is more useful than a clean-looking plan. See "What changed after review" at
> the end for the full list of divergences.**

- Bootstrap emits **no** `resources` today (0 occurrences in either generator).
- ~~Framework default: `limits: {memory: 40Gi, cpu: "4"}`, **no** `requests`, both roles (`defaults.yaml:848-851`, `:994-997`).~~ **WRONG.** Those ranges stop one line short of the `requests` block, which exists and sets 40Gi/`"4"` as well. The correct ranges are `defaults.yaml:848-858` (decode) and `:993-1000` (prefill), and the framework sets **both** halves.
- ~~Kubernetes copies `limits` into `requests` when `requests` is absent, so "limits only" is not "no reservation".~~ **TRUE OF KUBERNETES, IRRELEVANT HERE.** The rule is real, but a scenario is deep-merged over `defaults.yaml`, which supplies `requests` — so the copy never fires in this pipeline, and no "trap" exists. The actual reason to emit both halves is that emitting only `limits` leaves `requests` at the inherited 40Gi/`"4"`, pairing a 128Gi limit with a 40Gi reservation.
- Upstream `pd-disaggregation.yaml` sizes decode `limits {128Gi, 32}` (`:410-416`) and prefill `limits {16Gi, 8}` (`:321-327`), both with `requests == limits`. **decode > prefill is upstream's own sizing**, not only this cluster's.
- `pd-infocomm-2` carries the same two figures (decode `:210-211`, prefill `:278-280`), decode without requests.
- `config.md`'s machine-read table is `| Parameter | Value | Notes |`; `extract_fields` reads column 1 as parameter, column 2 as value. No cpu/memory rows exist in the vocabulary today.

## Design

**D1 — Fixed per-role defaults, decode > prefill.** No scaling with GPU count: the operator chose to start fixed and learn.

| Role | limits | requests |
|---|---|---|
| decode | `memory: 128Gi`, `cpu: '32'` | `memory: 64Gi`, `cpu: '16'` |
| prefill | `memory: 16Gi`, `cpu: '8'` | `memory: 8Gi`, `cpu: '4'` |

Limits are upstream's and `pd-infocomm-2`'s observed-working figures verbatim. Requests are **half** the limit — explicit rather than omitted, because omitting them makes Kubernetes reserve the whole generous limit, which is the trap #850 documents. The halving is a stated default, not a measurement; D4's warning says so.

**D2 — Input vocabulary: shared keys with per-role overrides.** Mirrors the existing `hardware` / `decode_hardware` / `prefill_hardware` pattern and its `fields.get(role_field) or fields["hardware"]` resolution.

| Canonical | Accepted row labels (lowercased) |
|---|---|
| `cpu_limit` | `cpu limit`, `cpu_limit`, `cpu limits` |
| `memory_limit` | `memory limit`, `memory_limit`, `memory limits` |
| `cpu_request` | `cpu request`, `cpu_request`, `cpu requests` |
| `memory_request` | `memory request`, `memory_request`, `memory requests` |
| `decode_*` / `prefill_*` | the same four, prefixed `decode ` / `prefill ` |

Values pass through as **strings**, never parsed into numbers: `32`, `500m`, `1.5`, `128Gi`, `1536Mi` are all valid Kubernetes quantities and re-serializing risks changing them. CPU is emitted quoted (`cpu: '32'`) to match both the framework default and both source scenarios; memory unquoted.

**D3 — Stated wins, per key, per role.** Order per quantity: per-role row → shared row → role default. A `config.md` stating only `decode cpu limit` still gets defaults for the other three. Provenance records which arm fired.

**D4 — Warn that defaults are unmeasured**, in two places per #850: a stderr warning at generation time and a comment in the emitted YAML, both naming the starvation signal (`Reducing Torch parallelism from N threads to 1`). Emitted only when at least one value came from a default — a config.md stating all four gets no warning.

**D5 — Shared module.** Both generators import it, the `pd_plumbing.py` precedent from #848, so the two hand-rolled emitters cannot drift.

## Files

| File | Change |
|---|---|
| `.claude/skills/sim2real-bootstrap/pod_resources.py` | **Create.** Default table, `resolve_resources`, `resource_lines`, `used_any_default`. |
| `generate_from_config.py` | 12 aliases; resolve per role in `build_scenario`; emit in `write_provenance_yaml`. |
| `generate_scenarios.py` | Same, reading `vllm_args` keys; add them to `KNOWN_FIELDS`. |
| `tests/test_pod_resources.py` | **Create.** Resolver + emitter units. |
| `tests/test_generate_from_config_prefill.py` | Resolution through the config.md path. |
| `tests/test_generate_scenarios.py` | Same through the JSON path, plus the cross-generator identity guard. |
| `SKILL.md` | Rows, defaults, resolution order, warning. |

---

### Task 1: `pod_resources.py`

**Produces** (used by Tasks 2-3):
- `DEFAULTS: dict[str, dict[str, str]]` — keyed `"decode"` / `"prefill"`, each with `cpu_limit`, `memory_limit`, `cpu_request`, `memory_request`
- `KEYS: tuple[str, ...]` — the four quantity names, in emission order
- `resolve_resources(role: str, stated: dict[str, str | None]) -> tuple[dict, dict]` → `(values, provenance)`
- `used_any_default(provenance: dict) -> bool`
- `resource_lines(values: dict, *, warn: bool) -> list[str]` — YAML at role-block indent: `    resources:` (4), `      limits:` (6), `        memory:` (8)
- `starvation_warning(role: str) -> str` — the stderr text, so both generators warn identically

- [ ] **Step 1: failing tests** — `tests/test_pod_resources.py`:

```python
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import pod_resources as pres


def parse(lines, role="decode"):
    """Parse emitter output inside the role block the caller has printed."""
    doc = f"scenario:\n- name: t\n  {role}:\n" + "\n".join(lines) + "\n"
    return yaml.safe_load(doc)["scenario"][0][role]


NONE = dict.fromkeys(pres.KEYS)


def test_keys_are_the_four_quantities():
    assert set(pres.KEYS) == {
        "cpu_limit", "memory_limit", "cpu_request", "memory_request"}


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_every_role_has_all_four_defaults(role):
    assert set(pres.DEFAULTS[role]) == set(pres.KEYS)


def test_decode_gets_more_than_prefill():
    """The operator's stated constraint, and upstream's own sizing."""
    d, p = pres.DEFAULTS["decode"], pres.DEFAULTS["prefill"]
    assert int(d["cpu_limit"]) > int(p["cpu_limit"])
    assert int(d["memory_limit"].removesuffix("Gi")) > int(
        p["memory_limit"].removesuffix("Gi"))


def test_defaults_match_the_observed_working_limits():
    """Verbatim from pd-disaggregation.yaml:410-416 / :321-327, which
    pd-infocomm-2 also carries."""
    assert pres.DEFAULTS["decode"]["cpu_limit"] == "32"
    assert pres.DEFAULTS["decode"]["memory_limit"] == "128Gi"
    assert pres.DEFAULTS["prefill"]["cpu_limit"] == "8"
    assert pres.DEFAULTS["prefill"]["memory_limit"] == "16Gi"


@pytest.mark.parametrize("role", ["decode", "prefill"])
def test_requests_are_below_limits(role):
    """limits without requests is NOT 'no reservation' -- Kubernetes copies the
    limit into the request, so requests must be emitted AND smaller (#850)."""
    v = pres.DEFAULTS[role]
    assert int(v["cpu_request"]) < int(v["cpu_limit"])
    assert int(v["memory_request"].removesuffix("Gi")) < int(
        v["memory_limit"].removesuffix("Gi"))


def test_defaults_used_when_nothing_stated():
    v, prov = pres.resolve_resources("decode", NONE)
    assert v == pres.DEFAULTS["decode"]
    assert all("default" in prov[k] for k in pres.KEYS)


def test_stated_value_wins_and_others_still_default():
    v, prov = pres.resolve_resources("decode", {**NONE, "cpu_limit": "64"})
    assert v["cpu_limit"] == "64"
    assert "config.md" in prov["cpu_limit"]
    assert v["memory_limit"] == pres.DEFAULTS["decode"]["memory_limit"]
    assert "default" in prov["memory_limit"]


def test_stated_values_pass_through_verbatim():
    """500m, 1.5 and Mi units must not be re-serialized into something else."""
    stated = {"cpu_limit": "500m", "memory_limit": "1536Mi",
              "cpu_request": "1.5", "memory_request": "512Mi"}
    v, _ = pres.resolve_resources("prefill", stated)
    assert v == stated


def test_used_any_default_distinguishes_the_two_cases():
    _, all_stated = pres.resolve_resources(
        "decode", {"cpu_limit": "1", "memory_limit": "2Gi",
                   "cpu_request": "3", "memory_request": "4Gi"})
    assert pres.used_any_default(all_stated) is False
    _, one_missing = pres.resolve_resources("decode", {**NONE, "cpu_limit": "1"})
    assert pres.used_any_default(one_missing) is True


def test_emitted_yaml_carries_both_limits_and_requests():
    v, _ = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, warn=True))["resources"]
    assert r["limits"] == {"memory": "128Gi", "cpu": "32"}
    assert r["requests"] == {"memory": "64Gi", "cpu": "16"}


def test_cpu_is_emitted_as_a_string():
    """Matches the framework default and both source scenarios; a bare int is a
    different YAML type than the chart's other cpu values."""
    v, _ = pres.resolve_resources("decode", NONE)
    r = parse(pres.resource_lines(v, warn=True))["resources"]
    assert isinstance(r["limits"]["cpu"], str)
    assert isinstance(r["requests"]["cpu"], str)


def test_prefill_emits_under_the_prefill_block():
    v, _ = pres.resolve_resources("prefill", NONE)
    r = parse(pres.resource_lines(v, warn=True), role="prefill")["resources"]
    assert r["limits"]["cpu"] == "8"


def test_warning_comment_names_the_starvation_signal():
    v, _ = pres.resolve_resources("decode", NONE)
    text = "\n".join(pres.resource_lines(v, warn=True))
    assert "Reducing Torch parallelism" in text


def test_no_warning_comment_when_everything_was_stated():
    stated = {"cpu_limit": "1", "memory_limit": "2Gi",
              "cpu_request": "3", "memory_request": "4Gi"}
    v, _ = pres.resolve_resources("decode", stated)
    text = "\n".join(pres.resource_lines(v, warn=False))
    assert "Reducing Torch parallelism" not in text
    assert parse(pres.resource_lines(v, warn=False))["resources"]["limits"]["cpu"] == "1"


def test_stderr_warning_names_the_role_and_the_signal():
    msg = pres.starvation_warning("prefill")
    assert "prefill" in msg
    assert "Reducing Torch parallelism" in msg
```

- [ ] **Step 2** `python -m pytest .claude/skills/sim2real-bootstrap/tests/test_pod_resources.py -v` → `ModuleNotFoundError`.
- [ ] **Step 3** Implement to satisfy exactly those assertions. Docstring must state: the Kubernetes limits→requests copy rule and why requests are explicit; that limits are upstream's observed figures and requests are a stated half; that nothing here is measured.
- [ ] **Step 4** Re-run → pass. `ruff check .claude/skills/sim2real-bootstrap/ --select F` → clean.
- [ ] **Step 5** Fault-inject and confirm each fails a test: drop the `requests` block; make prefill's limit exceed decode's; unquote cpu; remove the warning comment; change a default figure.
- [ ] **Step 6** Commit.

---

### Task 2: wire `generate_from_config.py`

- [ ] **Step 1** Tests appended to `tests/test_generate_from_config_prefill.py`, asserting on **emitted text** parsed back with `yaml.safe_load` (the emitter is a hand-rolled line appender — a dict key proves nothing):
  - no rows → decode and prefill each carry their own role defaults
  - `| cpu limit | 64 |` → both roles use 64; other three default
  - `| decode cpu limit | 64 |` → decode 64, prefill still default
  - both shared and per-role present → per-role wins
  - all four stated → no warning comment, no stderr warning
  - any default used → stderr warning naming `Reducing Torch parallelism` (capture with `capsys`)
  - `resources` nests inside each role block
  - a scenario with no prefill pool emits `resources` for decode only
- [ ] **Step 2** Run → fail.
- [ ] **Step 3** Add the 12 aliases to `PARAMETER_ALIASES`. Resolve per role in `build_scenario` with `fields.get(f"{role}_{key}") or fields.get(key)`.
- [ ] **Step 4** Emit at the end of each role block in `write_provenance_yaml`, where #848's `init_container_lines` sit.
- [ ] **Step 5** Whole skill suite → pass.
- [ ] **Step 6** Emit one scenario by hand; read the YAML and confirm nesting and figures.
- [ ] **Step 7** Commit.

---

### Task 3: wire `generate_scenarios.py`

Reads `vllm_args` keys (`cpu_limit`, `decode_cpu_limit`, …). They must be added to `KNOWN_FIELDS` or `check_unknown_fields` warns on them.

- [ ] **Step 1** Tests in `tests/test_generate_scenarios.py`: role defaults; stated wins; per-role beats shared; both roles carry `resources`; the new keys do not trip the unknown-field warning.
- [ ] **Step 2** Run → fail.
- [ ] **Step 3-4** Wire `build_scenario` and `write_commented_yaml` against the same module.
- [ ] **Step 5** Add the cross-generator guard: both paths emit character-identical `resources` text for equivalent input (#848's `test_both_generators_emit_identical_plumbing_text` precedent).
- [ ] **Step 6** Full suite → pass. Inject a divergence into the JSON path; confirm the cross-generator guard catches it.
- [ ] **Step 7** Commit.

---

### Task 4: SKILL.md and sweep

- [ ] **Step 1** Document in the baseline-generation section: accepted row labels, resolution order (per-role → shared → default), the per-role default table, and why requests are explicit-and-smaller (the Kubernetes copy rule).
- [ ] **Step 2** Note that the values are unmeasured and name the starvation signal.
- [ ] **Step 3** Sweep. `grep -rn "resources" --include=*.md .` excluding submodules; check whether `CLAUDE.md`, `pipeline/README.md`, or `/sim2real-check` enumerate expected scenario keys and would now see a new one. Report what was swept and what changed.
- [ ] **Step 4** Full CI locally: lint, then the five test paths with the coverage gate.
- [ ] **Step 5** Commit.

## Acceptance criteria

| Requirement (operator's restatement) | Where satisfied |
|---|---|
| config.md states cpu/memory req/limits → use them | D2/D3; `test_stated_value_wins_and_others_still_default` + Task 2 per-role tests |
| not stated → generous defaults | D1; `test_defaults_used_when_nothing_stated` |
| decode needs more than prefill | `test_decode_gets_more_than_prefill` |
| pd-infocomm-2 as starting point | `test_defaults_match_the_observed_working_limits` |
| fixed values, not derived | D1 — no GPU-count input to the defaults at all |
| bootstrap skill issue | only `.claude/skills/sim2real-bootstrap/` changes |
| (#850) requests explicit, not omitted | `test_requests_are_below_limits` |
| (#850) warn the values are unmeasured | D4; three tests plus a `capsys` stderr assertion |

## Risks

- **Every generated scenario changes.** Unlike #848 and #853 there is no byte-identity escape — a bundle regenerated after this gains a `resources` block. Intended, but the PR must say so plainly rather than let it surprise someone re-bootstrapping.
- **The figures are unmeasured** on any cluster but the one they came from, at one TP and one model. The emitted comment and the stderr warning are the whole mitigation. Fixed-and-learn is the operator's explicit choice; revisit when there is data.
- **`resources` is a dict, not a list**, so `_merge_lists` tiering does not apply: a downstream baseline setting `resources` deep-merges key by key rather than replacing. An operator overriding only `limits.cpu` keeps the emitted `requests`. Worth stating in the emitted comment, since it differs from the scalar-list behaviour documented elsewhere in this skill.

---

## What changed after review

This plan is kept as the record of what was intended. The shipped code differs in
five ways, all of them corrections found by review rather than changes of mind.

**1. Two "facts" in the header were wrong** — see the correction banner there. The
framework default sets `requests` as well as `limits`, and the limits/requests "trap"
this plan built its rationale on cannot fire in this pipeline. The design decision
(emit both halves) survives; the reason changed.

**2. D2's per-role vocabulary was never implemented, then removed.** The plan
proposed four shared keys plus eight `decode_*` / `prefill_*` overrides. The first
implementation shipped all twelve; the operator judged it over-engineered, and the
per-role rows were deleted along with the `InputStyle` machinery built around them.
Four shared keys remain. This plan's D3 wording is what leaked into a `SKILL.md`
sentence describing a three-level precedence that never existed.

**3. Memory is emitted QUOTED.** D1 said cpu quoted, memory unquoted. That was the
bug: an unquoted `-` placeholder produced an unparseable `baseline.yaml` while the
generator exited 0, and `128` / `yes` / `null` were silently re-typed. Both are
quoted now.

**4. The limit/request pair is reconciled per role.** The plan resolved the four
quantities independently, which let a request exceed its limit — an invalid pod spec.
Because the rows are shared while the defaults differ 4–8× between roles, a request
sized for decode inverted prefill unconditionally. `resolve_resources` now derives an
unstated request from the stated limit and clamps any request to its own role's
limit, so an invalid pair cannot be expressed.

**5. Prefill's request equals its limit.** The plan set every request to half its
limit. Issue #848 mounts a 16Gi `medium: Memory` tmpfs at `/dev/shm` in `vllmCommon`,
and tmpfs charges against pod memory, so a prefill request of 8Gi under a 16Gi
ceiling made the pod evictable mid-run. Upstream uses request == limit there;
so does this.

Also added, none of it in the plan: a Kubernetes quantity validator (an invalid
quantity is now a hard error rather than a pod the cluster rejects at admission),
and the four keys registered with `warn_role_rows_outside_vllm_table` so a resource
row stated in a non-machine-read table is reported instead of silently dropped.
