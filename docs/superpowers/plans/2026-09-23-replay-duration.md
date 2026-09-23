# `replay.duration` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a corpus workload bound its replay by the clock (`replay.duration`) instead of by session count (`replay.total_sessions`), rendering `blis observe --duration`.

**Architecture:** `duration` joins `REPLAY_FIELDS` as a member of a new one-of group `REPLAY_ONE_OF` alongside `total_sessions`. A new `ONE_OF` sentinel marks group members so the group and the table cannot drift. `corpus_schema._validate_replay` enforces exactly-one-written; `observe_argv` renders whichever member is written and skips the other. Group membership keys on whether the KEY IS WRITTEN, never on truthiness, because `total_sessions: 0` is a meaningful value and blis keys its own exclusion on supplied-ness.

**Tech Stack:** Python >= 3.12, PyYAML, pytest. No new dependencies.

**Spec:** GitHub issue [#923](https://github.com/inference-sim/sim2real/issues/923). Read it alongside this plan — its ten acceptance criteria are the contract. The task-timeout interaction is explicitly OUT of scope (split out as #924).

## Global Constraints

- Python >= 3.12 (`pyproject.toml` `requires-python`); enforced at import by `pipeline/__init__.py` and the repo-root `conftest.py`.
- Run everything through the worktree venv: `.venv/bin/python`, `.venv/bin/ruff`. Never system Python.
- **Every path you pass to Read/Edit/Write/Bash must contain `.claude/worktrees/issue-923-replay-duration/`.** The parent repo's files exist at sibling paths and will accept writes silently.
- **Never stage `inference-sim`.** That submodule carries a local-only override in this checkout (fork URL, `duration` branch, HEAD `4fd7bc22` vs the recorded `602ae462`). It has no business in this diff. Verify with `git status --short` before every commit; if `inference-sim` appears, unstage it.
- Lint gate is `ruff check pipeline/ .claude/skills/ --select F` (pyflakes errors only).
- Test gate is the full `pipeline/` suite. Baseline on this branch before any change: **2708 passed, 5 skipped, 2 xfailed**. That number must not drop.
- `pipeline/README.md` must be updated in the same change as any CLI/schema change (CLAUDE.md "Contributing" rule).
- Commit messages end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## Review Focus

Five input classes #923 implies but does not name a test for. Each gets a pinning test in the task that owns the code.

1. **A non-string `duration` (`duration: 20`, `duration: true`).** YAML hands these through as int/bool; a unit-less number is exactly the silent-wrong-unit hazard `duration.py` exists to refuse. Expected: refused at assemble naming the unit rule. → Task 2, Step 7.
2. **An explicitly null member (`total_sessions:` with no value, alongside `duration: 20m`).** Commenting out a value leaves the key present with `None`. Written-ness keying must treat that as WRITTEN and refuse the pair, matching blis's supplied-ness rule. Expected: refused, naming both keys. → Task 2, Step 9.
3. **`duration` alongside `corpus.upstream.format: weka-jsonl`.** `WEKA_INERT_FIELDS` sits immediately beside this code and refuses three `corpus:` fields on that format. `replay:` is applied by `blis observe`, not by `build-otel`, so `duration` must stay LEGAL there. Expected: assembles cleanly. → Task 2, Step 11.
4. **A stray `replay:` on a generative workload (no `corpus:`).** `DOCUMENT_MARKERS` routes on the presence of `corpus` OR `replay`, so this already lands in corpus validation and is refused — but nothing pins it, and a one-of rewrite could reorder the checks so the refusal changes shape. Expected: still refused, naming `corpus`. → Task 2, Step 13.
5. **`measurement.extraArgs` naming either flag of the pair.** `extraArgs` is appended verbatim and its comment claims it can "override anything above"; for this pair that is false — a duplicate silently re-sizes the run and a contradiction is a hard blis fatal in-pod. Expected: refused at assemble. → Task 4 (owns this entirely).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `pipeline/lib/duration.py` | Go-duration grammar, one definition for every consumer | ADD `is_positive_go_duration` beside `is_go_duration`. The existing predicate accepts `'0'`/`'0s'` on purpose (`max_think_time` documents `0` as "no cap"), so `duration` needs a stricter sibling rather than a changed shared one. |
| `pipeline/lib/corpus_schema.py` | The corpus/replay document — legal keys, defaults, validation | ADD `ONE_OF` sentinel, `REPLAY_ONE_OF` group, the `duration` field; REWRITE `_validate_replay` for exactly-one. |
| `pipeline/lib/observe_argv.py` | Renders the whole `blis observe` argv | REWRITE the `REPLAY_FIELDS` loop to render the written group member; ADD the `extraArgs` conflict check. |
| `pipeline/README.md` | Operator-facing schema reference | Update the `replay:` block (~line 1287) and the `replay.*` params note (~line 1396). |
| `pipeline/tests/test_corpus_schema.py` | Schema tests, incl. the applied-vs-hashed invariant | ADD one-of tests; re-verify `test_missing_required_replay_field_rejected`. |
| `pipeline/tests/test_observe_argv.py` | Argv rendering tests | ADD duration-rendering and extraArgs-conflict tests. |
| `pipeline/tests/test_duration.py` | Go-duration predicate tests | ADD `is_positive_go_duration` tests. (Create if absent.) |

---

## Task 1: `is_positive_go_duration` in `duration.py`

A zero duration is the one value that passes every existing check and still means nothing: `is_go_duration('0')` is True by design, and blis treats `duration == 0` as "flag not set". So `replay.duration: "0"` would assemble, emit `--duration 0`, and produce a run bounded by neither the clock nor a session count. This task builds the predicate that catches it, reusing the validated grammar rather than re-deriving it.

**Files:**
- Modify: `pipeline/lib/duration.py` (append after `is_go_duration`)
- Test: `pipeline/tests/test_duration.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `duration.is_positive_go_duration(value) -> bool` — True iff `value` is a Go duration string blis accepts AND sums to strictly more than zero nanoseconds. Task 2 uses it as the `check` predicate for the `duration` field.

- [ ] **Step 1: Confirm the test file's existing shape**

Run: `ls pipeline/tests/test_duration.py && head -20 pipeline/tests/test_duration.py`

If the file does not exist, create it with:

```python
"""Tests for the Go duration-string predicates (issues #905, #923)."""
import pytest

from pipeline.lib import duration
```

- [ ] **Step 2: Write the failing tests**

Append to `pipeline/tests/test_duration.py`:

```python
# -- is_positive_go_duration (#923) ------------------------------------------


@pytest.mark.parametrize("value", ["20m", "1h30m", "1ns", "0.5s", "1h0m0s"])
def test_positive_durations_accepted(value):
    assert duration.is_positive_go_duration(value) is True


@pytest.mark.parametrize("value", ["0", "+0", "0s", "0m", "0h", "0ms", "0ns",
                                   "0.0s", "0h0m0s", ".0s"])
def test_zero_durations_refused(value):
    """is_go_duration accepts these on purpose (max_think_time documents '0' as
    'no cap'). --duration cannot: blis reads 0 as 'flag not set', so a zero
    would silently produce a run bounded by nothing."""
    assert duration.is_go_duration(value) is True
    assert duration.is_positive_go_duration(value) is False


@pytest.mark.parametrize("value", ["-5m", "60000", "", "20", 20, True, None,
                                   "9999999999999s", "20x"])
def test_invalid_durations_refused(value):
    """Everything is_go_duration already refuses stays refused."""
    assert duration.is_positive_go_duration(value) is False


def test_positive_is_strictly_narrower_than_is_go_duration():
    """The two predicates differ only in which side of zero they sit on."""
    for value in ["20m", "1h30m", "0", "0s", "0.0s", "-5m", "60000", "1ns"]:
        if duration.is_positive_go_duration(value):
            assert duration.is_go_duration(value), value
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_duration.py -q -k positive`
Expected: FAIL with `AttributeError: module 'pipeline.lib.duration' has no attribute 'is_positive_go_duration'`

- [ ] **Step 4: Implement the predicate**

Append to `pipeline/lib/duration.py`, immediately after `is_go_duration`:

```python
def is_positive_go_duration(value) -> bool:
    """Return True iff ``value`` is a duration blis will accept AND is non-zero.

    A strictly narrower sibling of :func:`is_go_duration`, which accepts ``"0"``
    and ``"0s"`` deliberately -- ``prepare-trace`` documents ``0`` as "no cap"
    for ``max_think_time``, so the shared predicate must keep taking it.

    ``blis observe --duration`` cannot: its Cobra ``DurationVar`` defaults to 0
    and the corpus validator keys its one-of against ``--total-sessions`` on
    SUPPLIED-NESS, reading ``duration == 0`` as "flag not set"
    (``cmd/observe_corpus.go:84``). So a zero passes every check on both sides
    and yields a run bounded by neither the clock nor a session count, while the
    workload document says a time bound was requested. That is the
    accept-and-ignore shape ``corpus_schema`` exists to refuse, so it is refused
    here instead -- the reason is "blis treats 0 as unset", not "blis rejects 0".

    Zero is SUMMED rather than pattern-matched because it has many spellings:
    ``0``, ``0s``, ``0.0s``, ``.0s``, ``0h0m0s``. Reusing the already-validated
    :data:`_TERM_RE` grammar keeps one parser for the whole module.
    """
    if not is_go_duration(value):
        return False
    if value in ("0", "+0"):
        return False
    return any(
        decimal.Decimal(num) > 0 for num, _unit in _TERM_RE.findall(value)
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_duration.py -q`
Expected: PASS, whole file.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest pipeline/ -q`
Then: `.venv/bin/ruff check pipeline/ --select F`
Expected: >= 2708 passed, 5 skipped, 2 xfailed; ruff clean.

- [ ] **Step 7: Commit**

Confirm `inference-sim` is absent from `git status --short`, then:

```bash
git add pipeline/lib/duration.py pipeline/tests/test_duration.py
git commit -m "feat(duration): add is_positive_go_duration for --duration (#923)

is_go_duration accepts '0' and '0s' deliberately, because prepare-trace
documents 0 as 'no cap' for max_think_time. blis observe --duration cannot
take a zero: it reads 0 as 'flag not set', so the value would assemble and
then bound the run by nothing at all. Add a strictly narrower sibling rather
than tightening the shared predicate.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: the one-of in `corpus_schema.py`

**Files:**
- Modify: `pipeline/lib/corpus_schema.py` — add `ONE_OF` beside `REQUIRED` (~line 44), add `REPLAY_ONE_OF` and the `duration` field (~line 218-231), rewrite `_validate_replay` (~line 461-481)
- Test: `pipeline/tests/test_corpus_schema.py`

**Interfaces:**
- Consumes: `duration.is_positive_go_duration` from Task 1.
- Produces:
  - `corpus_schema.ONE_OF` — sentinel object, the `default` of every one-of group member.
  - `corpus_schema.REPLAY_ONE_OF: frozenset[str]` — `{"total_sessions", "duration"}`.
  - `corpus_schema.REPLAY_FIELDS["duration"]` — `_Field(flag="--duration", default=ONE_OF, render=str, check=duration.is_positive_go_duration, ...)`.
  - Tasks 3 and 4 read both `REPLAY_ONE_OF` and `REPLAY_FIELDS`.

- [ ] **Step 1: Write the failing table-agreement and happy-path tests**

Append to `pipeline/tests/test_corpus_schema.py`:

```python
# -- replay one-of: total_sessions XOR duration (#923) -----------------------


def _replay(**over):
    """A valid corpus doc whose replay block is replaced wholesale."""
    d = copy.deepcopy(_VALID)
    d["replay"] = over
    return d


def test_one_of_group_and_table_cannot_drift():
    """Every field marked ONE_OF is in REPLAY_ONE_OF and vice versa. Without
    this, adding a third sizing flag and forgetting the group would silently
    make it required alongside the others."""
    marked = {name for name, f in corpus_schema.REPLAY_FIELDS.items()
              if f.default is corpus_schema.ONE_OF}
    assert marked == set(corpus_schema.REPLAY_ONE_OF)
    assert corpus_schema.REPLAY_ONE_OF == {"total_sessions", "duration"}


def test_one_of_members_are_never_also_REQUIRED():
    for name in corpus_schema.REPLAY_ONE_OF:
        assert (corpus_schema.REPLAY_FIELDS[name].default
                is not corpus_schema.REQUIRED)


def test_duration_accepted_in_place_of_total_sessions():
    corpus_schema.validate_corpus_document(
        _replay(concurrent_sessions=8, duration="20m"), "w.yaml")


def test_total_sessions_still_accepted_alone():
    corpus_schema.validate_corpus_document(
        _replay(concurrent_sessions=8, total_sessions=8), "w.yaml")


def test_total_sessions_zero_is_still_a_legal_value():
    """AC 3. 0 means 'replay each corpus session once' and is in real use
    (exgentic-agentic-multiturn-sess25 in pd-infocomm-4). The one-of keys on
    whether the KEY is written, never on truthiness, so 0 must survive."""
    corpus_schema.validate_corpus_document(
        _replay(concurrent_sessions=25, total_sessions=0), "w.yaml")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_corpus_schema.py -q -k "one_of or duration_accepted or still_accepted_alone or zero_is_still"`
Expected: FAIL — `AttributeError: module 'pipeline.lib.corpus_schema' has no attribute 'ONE_OF'`

- [ ] **Step 3: Add the sentinel**

In `pipeline/lib/corpus_schema.py`, immediately after the `REQUIRED` sentinel (~line 44):

```python
# Sentinel for a field that is required as one MEMBER OF A GROUP rather than on
# its own: exactly one member of the group must be written. Distinct from
# REQUIRED so a test can assert the marked fields and the group agree, and so
# _validate_replay can tell "you must write this" from "you must write one of
# these" without carrying a hardcoded name list.
ONE_OF = object()
```

- [ ] **Step 4: Add the group and the `duration` field**

Immediately before `REPLAY_FIELDS` (~line 218), add:

```python
#: ``replay:`` fields that SIZE the run. Exactly one must be written -- they are
#: the two answers to "when does this replay stop", and blis rejects the pair
#: outright (``cmd/observe_corpus.go:84``).
#:
#: MEMBERSHIP IS KEYED ON WHETHER THE KEY IS WRITTEN, never on its value. blis
#: does the same, deliberately: its check is
#: ``duration != 0 && totalSessionsSupplied``, so ``--total-sessions 0
#: --duration 20m`` is a conflict rather than a silent override. ``0`` is a
#: documented, meaningful value for ``total_sessions`` ("replay each corpus
#: session once") and is in real use, so a truthiness test here would silently
#: convert an exhaust-the-corpus cell into an unbounded one.
REPLAY_ONE_OF: frozenset[str] = frozenset({"total_sessions", "duration"})
```

Then change `total_sessions`'s `default` from `REQUIRED` to `ONE_OF`, and APPEND `duration` after it — appended, never inserted, because table order is rendered flag order and AC 2 requires a `total_sessions` cell's `observeArgs` to stay byte-identical:

```python
    "total_sessions": _Field(
        flag="--total-sessions", default=ONE_OF, render=str,
        check=_int_at_least(0),
        describe="must be an int >= 0 (0 = exhaust the corpus)",
    ),
    # Appended, not inserted: this table's order is the rendered flag order, and
    # a cell that sizes by total_sessions must keep producing byte-identical
    # observeArgs. See REPLAY_ONE_OF for why this is a one-of rather than a
    # third independent knob.
    "duration": _Field(
        flag="--duration", default=ONE_OF, render=str,
        check=duration.is_positive_go_duration,
        describe=(
            f"{duration.DESCRIBE}, and must be non-zero: blis reads "
            f"--duration 0 as 'flag not set', so a zero would leave the run "
            f"bounded by neither the clock nor a session count"
        ),
    ),
```

- [ ] **Step 5: Rewrite `_validate_replay`**

Replace the whole function (~line 461-481) with:

```python
def _validate_replay(replay, where: str) -> None:
    required = sorted(set(REPLAY_FIELDS) - REPLAY_ONE_OF)
    if not isinstance(replay, dict) or not replay:
        raise AssembleError(
            f"workload {where}: 'replay' is required and must be a mapping "
            f"declaring {required} plus exactly one of "
            f"{sorted(REPLAY_ONE_OF)}"
        )
    unknown = set(replay) - set(REPLAY_FIELDS)
    if unknown:
        raise AssembleError(
            f"workload {where}: unrecognized key(s) {sorted(unknown)} under "
            f"'replay'. Legal keys: {sorted(REPLAY_FIELDS)}"
        )
    # The one-of, checked on PRESENCE. An explicitly null value counts as
    # written: commenting a value out leaves the key behind, and blis keys its
    # own exclusion on supplied-ness, so treating null as absent here would emit
    # a flag pair blis fatals on.
    written = sorted(REPLAY_ONE_OF & set(replay))
    if len(written) > 1:
        raise AssembleError(
            f"workload {where}: replay declares {written}, but they are "
            f"mutually exclusive -- 'total_sessions' bounds the run by session "
            f"count and 'duration' bounds it by the clock. blis rejects the "
            f"pair on SUPPLIED-NESS, not value, so 'total_sessions: 0' "
            f"alongside 'duration' is a conflict rather than an override: "
            f"remove the key you do not want, do not set it to 0 or null"
        )
    if not written:
        raise AssembleError(
            f"workload {where}: replay must declare exactly one of "
            f"{sorted(REPLAY_ONE_OF)} to size the run -- 'total_sessions' by "
            f"session count, 'duration' by the clock"
        )
    for key, field in REPLAY_FIELDS.items():
        if key not in replay:
            if key in REPLAY_ONE_OF:
                continue          # the unwritten member of the one-of
            raise AssembleError(f"workload {where}: replay.{key} is required")
        value = replay[key]
        if not field.check(value):
            raise AssembleError(
                f"workload {where}: replay.{key} {field.describe}, "
                f"got {value!r}"
            )
```

- [ ] **Step 6: Run to verify the new tests pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_corpus_schema.py -q`
Expected: PASS. If `test_missing_required_replay_field_rejected` fails, go to Step 14 — do NOT loosen its assertion to make it green.

- [ ] **Step 7: Add the Review-Focus-1 test (non-string / zero duration)**

```python
@pytest.mark.parametrize("value", [20, True, None, "60000", "-5m", "0", "0s"])
def test_non_duration_values_refused(value):
    """Review Focus 1 + AC 6. A bare number is the silent wrong-unit hazard
    duration.py exists to refuse; 0 is the one that also passes
    is_go_duration."""
    with pytest.raises(AssembleError, match="replay.duration"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8, duration=value), "w.yaml")
```

- [ ] **Step 8: Run it**

Run: `.venv/bin/python -m pytest pipeline/tests/test_corpus_schema.py -q -k non_duration_values`
Expected: PASS.

- [ ] **Step 9: Add the Review-Focus-2 tests (both written; explicit null; neither)**

```python
def test_both_one_of_members_refused():
    """AC 4."""
    with pytest.raises(AssembleError, match="mutually exclusive"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8, total_sessions=8, duration="20m"),
            "w.yaml")


def test_explicit_null_member_counts_as_written():
    """Review Focus 2. Commenting out a value leaves 'total_sessions:' -> None.
    blis keys on supplied-ness, so this must refuse rather than quietly treat
    the key as absent and emit both flags."""
    with pytest.raises(AssembleError, match="mutually exclusive"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8, total_sessions=None,
                    duration="20m"), "w.yaml")


def test_neither_one_of_member_refused():
    """AC 5."""
    with pytest.raises(AssembleError, match="exactly one of"):
        corpus_schema.validate_corpus_document(
            _replay(concurrent_sessions=8), "w.yaml")
```

- [ ] **Step 10: Run them**

Run: `.venv/bin/python -m pytest pipeline/tests/test_corpus_schema.py -q -k "one_of_members or explicit_null"`
Expected: PASS.

- [ ] **Step 11: Add the Review-Focus-3 tests (weka-jsonl stays legal)**

```python
def test_duration_is_legal_on_weka_jsonl():
    """Review Focus 3. WEKA_INERT_FIELDS refuses three corpus: fields on this
    format because build-otel is skipped. replay: is applied by blis observe,
    not build-otel, so duration must stay legal here."""
    doc = copy.deepcopy(_VALID)
    doc["corpus"]["upstream"] = {"source": "hf:o/d", "format": "weka-jsonl"}
    doc["corpus"]["select"] = {"min_rounds": 2}
    doc["replay"] = {"concurrent_sessions": 8, "duration": "20m"}
    corpus_schema.validate_corpus_document(doc, "w.yaml")


def test_no_replay_field_is_weka_inert():
    assert not any(p.startswith("replay.")
                   for p in corpus_schema.WEKA_INERT_FIELDS)
```

- [ ] **Step 12: Run them**

Run: `.venv/bin/python -m pytest pipeline/tests/test_corpus_schema.py -q -k weka`
Expected: PASS, including the pre-existing weka tests.

- [ ] **Step 13: Add the Review-Focus-4 test (stray `replay:` on a generative doc)**

```python
def test_replay_only_document_still_refused():
    """Review Focus 4. DOCUMENT_MARKERS routes on the presence of corpus OR
    replay, so a generative doc carrying a stray replay block lands here and
    must be refused for the missing corpus -- not silently accepted."""
    with pytest.raises(AssembleError, match="corpus"):
        corpus_schema.validate_corpus_document(
            {"replay": {"concurrent_sessions": 8, "duration": "20m"}},
            "w.yaml")
```

- [ ] **Step 14: Re-verify the pre-existing parametrized test (AC 10)**

Run: `.venv/bin/python -m pytest "pipeline/tests/test_corpus_schema.py::test_missing_required_replay_field_rejected" -v`

`total_sessions` is no longer unconditionally required, so deleting it now trips the one-of branch instead. The message must still contain the literal `total_sessions` for `match=field` to hold — the `exactly one of ['duration', 'total_sessions']` wording does contain it, so this should pass unchanged. If it does not, split the parametrization:

```python
@pytest.mark.parametrize("field", ["concurrent_sessions"])
def test_missing_required_replay_field_rejected(field):
    d = _doc()
    del d["replay"][field]
    with pytest.raises(AssembleError, match=field):
        corpus_schema.validate_corpus_document(d, "w.yaml")


def test_missing_one_of_member_rejected_via_the_group():
    """total_sessions stopped being independently required in #923; deleting it
    now trips the one-of, which names it in the legal set."""
    d = _doc()
    del d["replay"]["total_sessions"]
    with pytest.raises(AssembleError, match="total_sessions"):
        corpus_schema.validate_corpus_document(d, "w.yaml")
```

- [ ] **Step 15: Add the AC 7 cache-key test**

```python
def test_duration_does_not_move_the_corpus_cache_key():
    """AC 7. replay: is applied, never hashed -- two cells differing only in the
    time bound must share one corpus build."""
    corpus = {"upstream": {"source": "hf:o/d"},
              "reconstruct": {"max_think_time": "60s"}}
    keys = set()
    for replay in ({"concurrent_sessions": 8, "total_sessions": 8},
                   {"concurrent_sessions": 8, "duration": "20m"},
                   {"concurrent_sessions": 8, "duration": "45m"}):
        doc = {"corpus": copy.deepcopy(corpus), "replay": replay}
        corpus_schema.validate_corpus_document(doc, "w.yaml")
        keys.add(corpus_schema.corpus_cache_key(doc["corpus"]))
    assert len(keys) == 1, "replay.duration must not move the corpus cache key"
```

- [ ] **Step 16: Run the full suite and lint**

Run: `.venv/bin/python -m pytest pipeline/ -q`
Then: `.venv/bin/ruff check pipeline/ --select F`
Expected: no failures, count >= 2708 passed.

- [ ] **Step 17: Commit**

Confirm `inference-sim` is absent from `git status --short`, then:

```bash
git add pipeline/lib/corpus_schema.py pipeline/tests/test_corpus_schema.py
git commit -m "feat(assemble): add replay.duration as a one-of against total_sessions (#923)

A corpus replay can now be sized by the clock instead of by session count.
The two are mutually exclusive in blis, which keys the exclusion on
SUPPLIED-NESS rather than value -- so the group is checked on whether the key
is written, never on truthiness. total_sessions: 0 stays a meaningful value
('replay each corpus session once') and is in real use; a truthiness test
would have silently converted such a cell into an unbounded one.

ONE_OF is a distinct sentinel from REQUIRED so a test can assert the marked
fields and REPLAY_ONE_OF agree, rather than the group being a hardcoded name
list that drifts from the table.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: render the written member in `observe_argv.py`

**Files:**
- Modify: `pipeline/lib/observe_argv.py` (~line 340-350, the corpus-mode branch; plus the module docstring)
- Test: `pipeline/tests/test_observe_argv.py`

**Interfaces:**
- Consumes: `corpus_schema.REPLAY_ONE_OF`, `corpus_schema.REPLAY_FIELDS` from Task 2.
- Produces: no new symbols. `render_observe_argv` gains the behavior AC 1 and AC 2 describe.

- [ ] **Step 1: Write the failing rendering tests**

Append to `pipeline/tests/test_observe_argv.py`:

```python
# -- replay one-of rendering (#923) ------------------------------------------


_CORPUS_DURATION = {
    "corpus": {"upstream": {"source": "hf:Org/ds"}},
    "replay": {"concurrent_sessions": 128, "duration": "20m"},
}


def test_duration_renders_and_total_sessions_is_absent():
    """AC 1."""
    argv = render_observe_argv(workload=_CORPUS_DURATION, observe=None,
                               model="m", results_dir=_RD,
                               trace_path="traces/x")
    assert "--duration 20m" in argv
    assert "--total-sessions" not in argv
    assert "--concurrent-sessions 128" in argv


def test_total_sessions_rendering_is_byte_identical_to_before():
    """AC 2. The pre-#923 cell must produce exactly the same argv, which is why
    duration is APPENDED to the table rather than inserted."""
    argv = render_observe_argv(workload=_CORPUS_WORKLOAD, observe=None,
                               model="m", results_dir=_RD,
                               trace_path="traces/x")
    assert "--concurrent-sessions 128 --total-sessions 192" in argv
    assert "--duration" not in argv


def test_total_sessions_zero_still_renders_the_flag():
    """AC 3. Rendering must not drop a falsy-but-written value."""
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 25, "total_sessions": 0}}
    argv = render_observe_argv(workload=wl, observe=None, model="m",
                               results_dir=_RD, trace_path="traces/x")
    assert "--total-sessions 0" in argv


def test_both_one_of_members_raise_at_render_time():
    """Defence in depth: validation should have caught this, so reaching the
    renderer with both means validation was skipped. Fail loudly rather than
    emitting a pair blis fatals on."""
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 4, "total_sessions": 8,
                     "duration": "20m"}}
    with pytest.raises(ObserveArgvError, match="mutually exclusive"):
        render_observe_argv(workload=wl, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")


def test_neither_one_of_member_raises_at_render_time():
    wl = {"corpus": {"upstream": {"source": "hf:o/d"}},
          "replay": {"concurrent_sessions": 4}}
    with pytest.raises(ObserveArgvError, match="exactly one of"):
        render_observe_argv(workload=wl, observe=None, model="m",
                            results_dir=_RD, trace_path="traces/x")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_observe_argv.py -q -k "duration or one_of or zero_still"`
Expected: FAIL — `test_duration_renders_and_total_sessions_is_absent` raises `ObserveArgvError: replay.total_sessions is required to render corpus-mode argv`.

Note: the pre-existing `test_missing_replay_field_raises_rather_than_rendering_a_sentinel` (line ~246) asserts `match="total_sessions"` for a doc with only `concurrent_sessions`. After Step 3 that doc trips the one-of instead, and the message `exactly one of ['duration', 'total_sessions']` still contains `total_sessions`, so it stays green. Confirm rather than assume.

- [ ] **Step 3: Rewrite the corpus-mode replay loop**

In `pipeline/lib/observe_argv.py`, replace the loop inside the `if corpus_mode:` branch:

```python
        replay = workload.get("replay") or {}
        # Unconditionally required fields keep the old behavior: a missing one
        # means validation was skipped, so raise rather than render a sentinel.
        for name, field in corpus_schema.REPLAY_FIELDS.items():
            if name in corpus_schema.REPLAY_ONE_OF:
                continue
            if name not in replay:
                raise ObserveArgvError(
                    f"replay.{name} is required to render corpus-mode argv; "
                    f"validate the workload document first"
                )
            argv += [field.flag, field.render(replay[name])]
        # Then exactly one member of the sizing one-of. Iterated in TABLE order,
        # not set order, so the rendered flag position is deterministic. Keyed on
        # presence, matching blis's supplied-ness rule -- a written 0 or null is
        # written.
        written = [name for name in corpus_schema.REPLAY_FIELDS
                   if name in corpus_schema.REPLAY_ONE_OF and name in replay]
        if len(written) > 1:
            raise ObserveArgvError(
                f"replay declares {sorted(written)}, which are mutually "
                f"exclusive; validate the workload document first"
            )
        if not written:
            raise ObserveArgvError(
                f"replay must declare exactly one of "
                f"{sorted(corpus_schema.REPLAY_ONE_OF)} to size the run; "
                f"validate the workload document first"
            )
        sizing = corpus_schema.REPLAY_FIELDS[written[0]]
        argv += [sizing.flag, sizing.render(replay[written[0]])]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_observe_argv.py -q`
Expected: PASS, whole file.

- [ ] **Step 5: Add the fourth exclusion to the module docstring**

`observe_argv.py`'s docstring lists what the module owns "because nothing downstream can", naming three mutual exclusions. Add a fourth bullet after the `--saturation-report` one:

```
* **The replay sizing one-of.** ``--total-sessions`` and ``--duration`` answer
  the same question -- when does the replay stop -- and blis rejects the pair on
  SUPPLIED-NESS rather than value, so ``--total-sessions 0 --duration 20m`` is a
  conflict. Only the producer chooses which to emit, so only the producer can
  keep the pair from reaching blis together (#923).
```

Also update the count in the sentence above the list if it says "three".

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest pipeline/ -q`
Then: `.venv/bin/ruff check pipeline/ --select F`
Expected: clean.

- [ ] **Step 7: Commit**

Confirm `inference-sim` is absent from `git status --short`, then:

```bash
git add pipeline/lib/observe_argv.py pipeline/tests/test_observe_argv.py
git commit -m "feat(assemble): render whichever replay sizing flag is written (#923)

The replay loop demanded every field and rendered them all, which is why
--total-sessions was unconditional and no time bound could be expressed.
Render the unconditional fields as before, then exactly one member of the
sizing one-of, iterated in TABLE order so the flag position is deterministic.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: refuse `extraArgs` that names a sizing flag

#923 asks for this decision explicitly and says not to leave it unasked. The decision taken here is to REFUSE, narrowly, for one reason: `extraArgs` is documented as a tail that can "override anything above", and for this pair that is false. A duplicate `--total-sessions` silently re-sizes the run while the workload document claims otherwise (Cobra takes the last occurrence), and naming the other member collides with what was already rendered, which is a hard `logrus.Fatalf` after a namespace slot and a corpus download have been spent. Every other flag `extraArgs` can restate really is an override; these two are not.

**Files:**
- Modify: `pipeline/lib/observe_argv.py` (the `extraArgs` block near the end of `render_observe_argv`)
- Test: `pipeline/tests/test_observe_argv.py`

**Interfaces:**
- Consumes: `corpus_schema.REPLAY_FIELDS` and `REPLAY_ONE_OF` (for the flag spellings) from Task 2.
- Produces: no new symbols.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("extra", ["--total-sessions 5", "--duration 5m",
                                   "--record-itl --total-sessions 5"])
def test_extra_args_naming_a_sizing_flag_is_refused(extra):
    """#923. extraArgs is a documented override for everything else, but for the
    sizing pair an override is either a silent re-size (Cobra takes the last
    occurrence) or a hard blis fatal."""
    with pytest.raises(ObserveArgvError, match="extraArgs"):
        render_observe_argv(workload=_CORPUS_DURATION,
                            observe={"extraArgs": extra}, model="m",
                            results_dir=_RD, trace_path="traces/x")


def test_extra_args_unrelated_to_sizing_still_allowed():
    argv = render_observe_argv(workload=_CORPUS_DURATION,
                               observe={"extraArgs": "--shuffle-corpus"},
                               model="m", results_dir=_RD,
                               trace_path="traces/x")
    assert argv.endswith("--shuffle-corpus")


def test_sizing_flag_check_does_not_fire_on_a_generative_cell():
    """A spec-mode cell renders no sizing flag, so extraArgs naming one is the
    operator's business and collides with nothing this module emitted."""
    argv = _render(observe={"extraArgs": "--num-requests 10"})
    assert "--num-requests 10" in argv
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest pipeline/tests/test_observe_argv.py -q -k "extra_args or sizing_flag_check"`
Expected: the three parametrized cases FAIL (no error raised); the other two pass.

- [ ] **Step 3: Implement the check**

In `render_observe_argv`, inside the existing `if extra:` block, after `_validate_word(...)` and before `argv += shlex.split(extra)`:

```python
        # extraArgs is a general override tail, but not for the sizing one-of.
        # Restating one of those flags is never an override: a duplicate
        # silently re-sizes the run (Cobra takes the last occurrence) while the
        # workload document says otherwise, and the other member collides with
        # what was already rendered, which blis rejects outright. Only refuse
        # when THIS cell actually rendered a sizing flag -- a generative cell
        # renders none, so there is nothing to contradict.
        if corpus_mode:
            words = set(shlex.split(extra))
            clashing = sorted(
                corpus_schema.REPLAY_FIELDS[name].flag
                for name in corpus_schema.REPLAY_ONE_OF
                if corpus_schema.REPLAY_FIELDS[name].flag in words
            )
            if clashing:
                raise ObserveArgvError(
                    f"{SOURCE_LABEL}.extraArgs names {clashing}, which size "
                    f"the replay. Those come from the workload cell's "
                    f"'replay:' block, not from the measurement protocol: "
                    f"restating one here either silently re-sizes the run or "
                    f"collides with the flag already rendered, which blis "
                    f"rejects. Set replay.total_sessions or replay.duration "
                    f"instead"
                )
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest pipeline/tests/test_observe_argv.py -q`
Expected: PASS, whole file.

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/python -m pytest pipeline/ -q`
Then: `.venv/bin/ruff check pipeline/ --select F`
Expected: clean.

- [ ] **Step 6: Commit**

Confirm `inference-sim` is absent from `git status --short`, then:

```bash
git add pipeline/lib/observe_argv.py pipeline/tests/test_observe_argv.py
git commit -m "feat(assemble): refuse extraArgs that names a replay sizing flag (#923)

extraArgs is documented as a tail that can override anything above it. That
holds for every flag except the sizing pair: a duplicate --total-sessions
silently re-sizes the run because Cobra takes the last occurrence, and naming
the other member collides with what was already rendered and is a hard blis
fatal in-pod. Refuse at assemble, and only for a corpus cell -- a generative
cell renders no sizing flag, so there is nothing to contradict.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: document the one-of and close out the acceptance criteria

**Files:**
- Modify: `pipeline/README.md` (~line 1287 the `replay:` block; ~line 1396 the params note)
- Modify: `CLAUDE.md` (the `corpus_schema.py` row of the Pipeline Library table)

**Interfaces:**
- Consumes: the finished behavior from Tasks 1-4.
- Produces: nothing code-facing.

- [ ] **Step 1: Update the `replay:` schema block**

Replace the three-line `replay:` block at ~1287 with:

```markdown
replay:                         # REQUIRED — consumed by blis observe at replay time
  concurrent_sessions: 128      # REQUIRED, int >= 1  → --concurrent-sessions
  total_sessions: 192           # int >= 0  → --total-sessions (0 = exhaust the corpus)
  # duration: 20m               # Go duration, non-zero → --duration
```

Then add, immediately after the block:

```markdown
**`total_sessions` and `duration` are a one-of: write exactly one.** They are the
two answers to "when does this replay stop" — by session count, or by the clock.
`duration` bounds the *measured window*: once that much has elapsed, blis stops
sending (no new sessions, no further rounds of running ones) and then drains
requests already on the wire, so the output trace contains partial sessions. The
window is measured from the start of the dispatch loop, so it excludes
`measurement.prewarmDuration` and tokenizer calibration.

The choice is keyed on **whether the key is written**, not on its value, because
blis keys its own exclusion the same way: `--total-sessions 0 --duration 20m` is
a conflict, not an override. `total_sessions: 0` is a meaningful value ("replay
each corpus session once"), so to switch a cell to a time bound you must *remove*
the `total_sessions` key — setting it to `0` or `null` is refused. A zero
`duration` is refused too: blis reads `--duration 0` as "flag not set", which
would leave the run bounded by neither the clock nor a session count.

`measurement.extraArgs` may not name `--total-sessions` or `--duration`; those
come from the workload cell. See #924 for the interaction between a long
`duration` and the observe task's Tekton timeout, which assemble does not yet
check.
```

- [ ] **Step 2: Update the params note at ~1396**

Replace it with:

```markdown
`replay.*` emits **no** params of its own: #900 folded `concurrent_sessions` / `total_sessions` into the rendered `observeArgs` as `--concurrent-sessions` / `--total-sessions`, so the standalone `concurrentSessions` / `totalSessions` params no longer exist. #923 added `duration` the same way, as `--duration` — a new replay field needs no Pipeline or Task change, only a table entry and a renderer that knows when to emit it.
```

- [ ] **Step 3: Update the `corpus_schema.py` row in `CLAUDE.md`**

Append to that row's prose:

```
`REPLAY_ONE_OF` (#923) makes `total_sessions` / `duration` a one-of — exactly one written, keyed on presence rather than truthiness because blis keys its `--duration` / `--total-sessions` exclusion on supplied-ness and `total_sessions: 0` is a meaningful value. Members carry the `ONE_OF` sentinel rather than `REQUIRED`, and a test asserts the marked set equals the group.
```

- [ ] **Step 4: Sweep for stale references**

Run each and judge every hit stale / accurate / unrelated:

```bash
grep -rn "total_sessions" --include="*.md" .
grep -rn "total-sessions" --include="*.md" --include="*.yaml" .
grep -rn "REPLAY_FIELDS" --include="*.md" --include="*.py" .
```

Expected stale hits: `pipeline/README.md` (fixed in Steps 1-2) and `CLAUDE.md` (Step 3). The `.claude/skills/` tree and `tektonc-data-collection/` should have none — if a Task YAML mentions `--total-sessions`, that is a finding, because #900 moved the argv out of the Task.

- [ ] **Step 5: Walk the ten acceptance criteria against the code**

For each of #923's AC 1-10, name the test that proves it, and write the mapping into the PR body. Any AC without a test is a gap to close before pushing.

- [ ] **Step 6: Run the full CI gate exactly as CI runs it**

```bash
.venv/bin/ruff check pipeline/ .claude/skills/ --select F
```

```bash
.venv/bin/python -m pytest pipeline/ .claude/skills/sim2real-analyze/tests/ .claude/skills/sim2real-bootstrap/tests/ .claude/skills/sim2real-translate/tests/ .claude/skills/sim2real-check/tests/ --cov=pipeline --cov-report=term-missing --cov-fail-under=90 -q
```

Expected: ruff clean; tests pass; coverage >= 90%.

- [ ] **Step 7: Commit**

Confirm `inference-sim` is absent from `git status --short`, then:

```bash
git add pipeline/README.md CLAUDE.md docs/superpowers/plans/2026-09-23-replay-duration.md
git commit -m "docs: document the replay sizing one-of (#923)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage** — every acceptance criterion maps to a task and a named test:

| AC | Task | Test |
|---|---|---|
| 1 duration renders, no `--total-sessions` | 3 | `test_duration_renders_and_total_sessions_is_absent` |
| 2 total_sessions path byte-identical | 3 | `test_total_sessions_rendering_is_byte_identical_to_before` |
| 3 `total_sessions: 0` survives | 2, 3 | `test_total_sessions_zero_is_still_a_legal_value`, `test_total_sessions_zero_still_renders_the_flag` |
| 4 both written → refused | 2 | `test_both_one_of_members_refused` |
| 5 neither written → refused | 2 | `test_neither_one_of_member_refused` |
| 6 unitless / negative / zero refused | 1, 2 | `test_zero_durations_refused`, `test_invalid_durations_refused`, `test_non_duration_values_refused` |
| 7 cache key unmoved | 2 | `test_duration_does_not_move_the_corpus_cache_key` |
| 8 applied-not-hashed invariant holds | 2 | pre-existing `test_replay_fields_are_applied_but_not_hashed` + Step 16 full run |
| 9 README documents the one-of | 5 | Steps 1-2 |
| 10 pre-existing parametrized test still passes | 2 | Step 14 |

Plus the `extraArgs` decision #923 required be made explicitly → Task 4.

**Placeholder scan** — no TBDs, no "add appropriate validation", no "similar to Task N". Every code step carries the actual code. Task 2 Step 14 and Task 3 Step 2 give a conditional ("if it does not pass, do X") with both branches spelled out, which is a real instruction rather than a placeholder.

**Type consistency** — `ONE_OF`, `REPLAY_ONE_OF`, `is_positive_go_duration`, and `REPLAY_FIELDS["duration"].flag` are spelled identically in every task that references them. Tasks 3 and 4 both read `REPLAY_ONE_OF` and `REPLAY_FIELDS`; neither introduces a variant name. `_replay(**over)` is defined once (Task 2 Step 1) and used by Task 2's later steps only; Task 3 and 4 use `_CORPUS_DURATION`, defined in Task 3 Step 1 and reused in Task 4 — Task 4 must run after Task 3.

**Review Focus** — all five lines have an owning task and a named test: Task 2 Steps 7, 9, 11, 13 and Task 4 Step 1.

**Task ordering** — Tasks 1 → 2 → 3 → 4 are strictly sequential (each consumes the previous task's symbols; Task 4 reuses Task 3's fixture). Task 5 is last because its AC walk needs all four.
