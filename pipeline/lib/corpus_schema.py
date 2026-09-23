"""The corpus/replay workload document — one definition, three consumers.

A workload YAML is exactly one of two kinds:

* **CORPUS** — a non-empty top-level ``corpus:`` mapping. Requests are replayed
  from a recorded conversation corpus that ``prepare-trace`` builds.
* **GENERATIVE** — a bare blis ``WorkloadSpec`` (``clients:``/``cohorts:``/
  ``version:``). Unchanged by this module.

The discriminator is STRUCTURAL: the presence of ``corpus:``. There is
deliberately no ``kind:`` field (a label can contradict the structure it
labels) and no ``name:`` field (the name is the filename stem, which
``assemble_run._load_workload`` already derives).

This module is the single place the corpus document is defined. Three consumers
read the tables below and therefore cannot drift apart:

1. ``assemble_run._validate_workload`` — rejects anything not defined here.
2. ``tekton.make_pipelinerun_scenario`` — emits one PipelineRun param per field.
3. ``tests/test_corpus_schema.py`` — asserts the invariant that makes the
   whole arrangement worth having (see below).

THE INVARIANT (issue #901): every field under ``corpus:`` is **hashed** into the
corpus cache key AND **applied** to a PipelineRun param. Every field under
``replay:`` is applied and deliberately NOT hashed, because it is consumed at
replay time and does not change the corpus that gets built. A field that is
neither applied nor rejected is a defect: it changes the cache key without
changing the corpus, which is strictly worse than being refused.

That is why fields the ``prepare-trace`` Task does not yet honor live in
``DEFERRED_FIELDS`` and are REJECTED rather than accepted-and-ignored. They
become legal in the same change that makes them reach the Task — which is what
issue #905 did for ``upstream.revision``, ``upstream.format`` and
``reconstruct.max_think_time``, moving all three out of ``DEFERRED_FIELDS`` and
into the tables below in the same commit that declared their params in
``pipeline/pipeline.yaml``.
"""
import hashlib
import json

from pipeline.lib import duration
from pipeline.lib.errors import AssembleError

# Sentinel for a field with no default: the document MUST set it.
# ``None`` cannot serve here — it is a legitimate default for a nullable field.
REQUIRED = object()

# Sentinel for a field that is required as one MEMBER OF A GROUP rather than on
# its own: exactly one member of the group must be written. Distinct from
# REQUIRED so a test can assert the marked fields and the group agree, and so
# ``_validate_replay`` can tell "you must write this" from "you must write one of
# these" without carrying a hardcoded name list.
ONE_OF = object()


class _Field:
    """One legal field of the corpus/replay document.

    Exactly one of ``param`` / ``flag`` is set, and which one says where the
    field's value LANDS — a distinction issue #900 introduced:

    ``param``    — the PipelineRun param it drives (declared in
                   ``pipeline/pipeline.yaml``). Used by every ``corpus:`` field,
                   which feed the ``prepare-trace`` Task.
    ``flag``     — the ``blis observe`` CLI flag it drives, rendered into the
                   ``observeArgs`` param by ``observe_argv.py``. Used by every
                   ``replay:`` field: #900 collapsed ``concurrentSessions`` and
                   ``totalSessions`` from standalone PipelineRun params into the
                   rendered argv, so those params no longer exist and naming
                   them here would be a dangling reference.
    ``default``  — value used when the document omits it, or ``REQUIRED``.
    ``render``   — value → string (Tekton params and argv words are both strings).
    ``check``    — validity predicate, run at assemble time.
    ``describe`` — operator-facing phrase completing "corpus.x.y <describe>".
    """

    __slots__ = ("param", "flag", "default", "render", "check", "describe")

    def __init__(self, default, render, check, describe, param=None, flag=None):
        if (param is None) == (flag is None):
            raise ValueError("exactly one of param / flag must be set")
        self.param = param
        self.flag = flag
        self.default = default
        self.render = render
        self.check = check
        self.describe = describe


def _int_at_least(minimum: int | None):
    """Predicate for a non-bool int, optionally floored.

    ``bool`` is a subclass of ``int``, so YAML ``true``/``false`` would
    otherwise satisfy an int check.
    """
    def check(value) -> bool:
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        return minimum is None or value >= minimum
    return check


def _one_of(*allowed: str):
    def check(value) -> bool:
        return value in allowed
    return check


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value)


def _is_bool(value) -> bool:
    return isinstance(value, bool)


def _flag(value) -> str:
    """Render a bool as the ``1``/``0`` the prepare-trace shell steps compare."""
    return "1" if value else "0"


#: Upstream corpus formats, mirroring the ``traceFormat`` guard step in
#: ``tektonc-data-collection/tekton/tasks/prepare-trace.yaml``
#: (``case`` arm ``otel-parquet|weka-jsonl``). The guard is a deliberate in-pod
#: BACKSTOP; this tuple is the real gate, because refusing here costs nothing
#: while refusing there has already consumed a namespace slot. A test parses that
#: ``case`` arm and compares it against this tuple, so the two cannot drift.
#:
#: Each value selects the WHOLE chain, not just a parser: ``otel-parquet``
#: discovers ``.parquet``, runs build-otel, and converts with ``blis convert
#: otel``; ``weka-jsonl`` discovers ``.jsonl``, SKIPS build-otel (JSONL is
#: already blis's native Weka shape), and converts with ``blis convert weka``.
CORPUS_FORMATS = ("otel-parquet", "weka-jsonl")


#: ``corpus:`` — everything that determines the bytes of the built corpus, and
#: therefore everything the cache key covers. Section order and field order set
#: PipelineRun param order, so keep them stable for diffable output.
CORPUS_FIELDS: dict[str, dict[str, _Field]] = {
    # Where the raw corpus comes from.
    "upstream": {
        "source": _Field(
            param="traceSource", default=REQUIRED, render=str,
            check=_nonempty_str,
            describe="must be a non-empty string (e.g. 'hf:<org>/<dataset>')",
        ),
        "revision": _Field(
            param="traceRevision", default="", render=str,
            check=_nonempty_str,
            describe="must be a non-empty string (a commit SHA, tag, or branch)",
        ),
        "format": _Field(
            param="traceFormat", default="otel-parquet", render=str,
            check=_one_of(*CORPUS_FORMATS),
            describe=f"must be one of {', '.join(CORPUS_FORMATS)}",
        ),
        "shards": _Field(
            param="traceShards", default=39, render=str,
            check=_int_at_least(0),
            describe="must be an int >= 0 (0 = every shard)",
        ),
    },
    # Which sessions survive into the corpus.
    "select": {
        "partition": _Field(
            param="traceSplit", default="test", render=str,
            check=_one_of("test", "train", "all"),
            describe="must be one of 'test', 'train', 'all'",
        ),
        "min_rounds": _Field(
            param="traceMinRounds", default=2, render=str,
            check=_int_at_least(1),
            describe="must be an int >= 1",
        ),
        "dedup_by_conversation": _Field(
            param="traceDedupByConversation", default=True, render=_flag,
            check=_is_bool,
            describe="must be a bool",
        ),
        "shuffle_seed": _Field(
            param="traceShuffleSeed", default=42, render=str,
            check=_int_at_least(None),
            describe="must be an int",
        ),
    },
    # How each session's request stream is rebuilt from the raw records.
    "reconstruct": {
        "context_growth": _Field(
            param="traceContextGrowth", default="accumulate", render=str,
            check=_one_of("accumulate", "independent"),
            describe="must be 'accumulate' or 'independent'",
        ),
        # REQUIRED, uniquely among corpus fields, and deliberately so. Every
        # candidate default is wrong somewhere, silently:
        #
        # * Omitting the flag (the Task's own default) lets each converter apply
        #   its own, and they differ in OPPOSITE directions — `convert otel` caps
        #   at 15s, `convert weka` does not cap at all. One descriptor would then
        #   mean two different things depending on `format`.
        # * Any fixed value is a guess about a distribution this module cannot
        #   see. Measured on the exgentic corpus, inter-round gaps are BIMODAL —
        #   p50 8.0s, p90 27.6s, p99 14406s — so a 15s cap distorts 22.4% of
        #   gaps, cutting into the normal within-task band, while anything from
        #   60s to 3600s clamps the same ~2% outlier cluster. The right value
        #   comes from the corpus, and the two corpora we have disagree.
        # * No cap is not an option: uncapped, exgentic's worst single session
        #   idles 5.03h against a 4h pipeline timeout, so the run cannot finish.
        #
        # Requiring it costs almost nothing — `reconstruct` exists only for
        # corpus workloads, and no corpus descriptor has ever executed a measured
        # run — and it makes the cap auditable in the PipelineRun instead of
        # implied by whichever converter happened to run.
        "max_think_time": _Field(
            param="traceMaxThinkTime", default=REQUIRED, render=str,
            check=duration.is_go_duration,
            describe=(
                f"{duration.DESCRIBE}. '0' means no cap, which is only safe on a "
                f"corpus whose longest session fits the pipeline timeout"
            ),
        ),
    },
}

#: ``replay:`` fields that SIZE the run. Exactly one must be written — they are
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

#: ``replay:`` — consumed by ``blis observe`` at replay time. Applied, never
#: hashed: two cells differing only here build the identical corpus and share
#: one cache entry, which is the property ``corpus_cache_key`` exists to give.
REPLAY_FIELDS: dict[str, _Field] = {
    "concurrent_sessions": _Field(
        flag="--concurrent-sessions", default=REQUIRED, render=str,
        check=_int_at_least(1),
        describe="must be an int >= 1",
    ),
    "total_sessions": _Field(
        flag="--total-sessions", default=ONE_OF, render=str,
        check=_int_at_least(0),
        describe="must be an int >= 0 (0 = exhaust the corpus)",
    ),
    # Appended, not inserted: this table's order is the rendered flag order, and
    # a cell that sizes by total_sessions must keep producing byte-identical
    # observeArgs. See REPLAY_ONE_OF for why this is a one-of rather than a third
    # independent knob.
    "duration": _Field(
        flag="--duration", default=ONE_OF, render=str,
        check=duration.is_positive_go_duration,
        describe=(
            f"{duration.DESCRIBE}, and must be non-zero: blis reads "
            f"--duration 0 as 'flag not set', so a zero would leave the run "
            f"bounded by neither the clock nor a session count"
        ),
    ),
}

#: Fields an operator may plausibly reach for that NOTHING downstream honors
#: yet. Each is refused with its own reason, so the error explains the gap
#: instead of reading like a typo. Moving one of these into the tables above is
#: the whole content of the change that makes it work.
#:
#: Wording constraint: these must not contain the word "unknown" — the strict
#: unknown-key branch is a different error and a test keeps the two distinct.
DEFERRED_FIELDS: dict[str, str] = {
    "corpus.select.partition_pct": (
        "is not a Task parameter: prepare-trace hard-codes the split "
        "percentage as a constant (TEST_PCT = 30). Parameterizing it needs a "
        "change to inference-sim/tektonc-data-collection first"
    ),
    "corpus.reconstruct.max_context": (
        "has no support anywhere: neither the prepare-trace Task nor "
        "'blis convert otel'/'blis convert weka' accepts a context ceiling. "
        "Capping accumulated context needs that capability to exist first"
    ),
}

#: Fields that are legal on the otel-parquet path and INERT on weka-jsonl, so
#: writing one alongside ``format: weka-jsonl`` is refused.
#:
#: ``build-otel`` is what applies all three, and ``build-otel`` is SKIPPED for
#: weka-jsonl (JSONL is already blis's native Weka shape). ``blis convert weka``
#: has no flag for any of them and does no internal equivalent, so on a weka
#: corpus they change nothing — while still being hashed into the cache key,
#: which is the accept-and-ignore shape this module exists to refuse. The pinned
#: prepare-trace Task marks exactly these three params "OTEL-PARQUET ONLY" and
#: says the rejection "has to live at assemble time", because the Task cannot
#: tell an operator asking for a shuffle from sim2real emitting its default —
#: both arrive as a non-empty param. Here the difference IS visible: only a
#: field the document actually WROTE is refused.
#:
#: ``min_rounds`` and ``context_growth`` are deliberately absent: the convert
#: step threads both to ``blis convert weka`` (``--min-rounds``,
#: ``--context-growth``), so they stay live on the weka path. ``max_think_time``
#: likewise reaches the converter on both paths.
#:
#: A test derives this set from the Task's own "OTEL-PARQUET ONLY" markers, so if
#: tektonc ever gives weka a split or shuffle, the two cannot silently disagree.
WEKA_INERT_FIELDS: dict[str, str] = {
    "corpus.select.partition": (
        "selects a deterministic SHA1 split, which build-otel applies — and "
        "build-otel is skipped for weka-jsonl. 'blis convert weka' has no split "
        "flag, so the WHOLE corpus would be converted (train sessions included) "
        "while the cache key claimed a split"
    ),
    "corpus.select.dedup_by_conversation": (
        "keeps one session per conversation, which build-otel applies — and "
        "build-otel is skipped for weka-jsonl. 'blis convert weka' has no "
        "equivalent, so every session would be kept regardless of the value"
    ),
    "corpus.select.shuffle_seed": (
        "seeds the shuffle that build-otel applies — and build-otel is skipped "
        "for weka-jsonl. A weka corpus therefore stays in its on-disk, "
        "conversation-contiguous order, so a replay pool taking the first N "
        "sessions draws a CORRELATED sample rather than one spanning the corpus. "
        "That is a measurement-validity problem, not a cosmetic gap"
    ),
}

#: Top-level keys a corpus document may carry. ``workload_name`` is INJECTED by
#: ``assemble_run._load_workload`` from the filename stem before validation
#: runs, so it must be tolerated here even though no document writes it.
_LEGAL_TOP_LEVEL = frozenset({"corpus", "replay", "workload_name"})

#: Top-level keys that mark a generative WorkloadSpec. Beside ``corpus:`` they
#: make the document's kind ambiguous rather than merely over-specified.
_GENERATIVE_MARKERS = ("clients", "cohorts")

#: Keys whose mere PRESENCE declares the document's intent to be a corpus
#: workload, whatever their value. ``assemble_run._validate_workload`` routes on
#: these rather than on :func:`is_corpus_document`, so a degenerate value
#: (``corpus: {}``, ``corpus:`` null, ``corpus: "str"``) is REFUSED rather than
#: falling through to the generative path and reaching blis as a "WorkloadSpec".
#:
#: Neither key exists on a blis WorkloadSpec (inference-sim
#: ``sim/workload/spec.go``), so presence is unambiguous and generative
#: documents are never captured by this test.
DOCUMENT_MARKERS = ("corpus", "replay")


def is_corpus_document(doc: dict) -> bool:
    """Return True iff ``doc`` declares a non-empty top-level ``corpus:`` map."""
    corpus = doc.get("corpus")
    return isinstance(corpus, dict) and bool(corpus)


def corpus_cache_key(corpus: dict) -> str:
    """Return the 12-hex content address of a ``corpus:`` mapping.

    The key covers the ``corpus:`` mapping ALONE — not the workload name (so
    two cells with byte-identical corpus content share one build instead of
    building it twice) and not ``replay:`` (a replay-time parameter that cannot
    change the corpus).

    The hash is over the mapping AS WRITTEN, with no default-filling. An
    explicitly written ``shards: 39`` therefore keys differently from an
    omitted ``shards``, even though both build the same corpus. That is a
    deliberate, documented conservatism: filling defaults first would make the
    key depend on this module's default values, so bumping a default would
    silently re-point every existing cache entry. Rebuilding once after an
    operator spells out a default is the cheaper failure.
    """
    canonical = json.dumps(corpus, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def validate_corpus_document(doc: dict, where: str) -> None:
    """Validate a corpus workload document strictly. Raise on any violation.

    Strict means: a key this module does not define is an ERROR, not something
    to ignore. ``where`` is the workload path, used verbatim in messages so an
    operator knows which file to edit. Raises :class:`AssembleError`.
    """
    _validate_top_level(doc, where)
    if not is_corpus_document(doc):
        # Reached whenever a document declares corpus:/replay: intent but the
        # corpus value is absent, null, empty, or not a mapping. Name what was
        # actually found — a bare "must be a mapping" leaves an operator staring
        # at a `corpus:` line that looks fine until they notice it has no body.
        found = doc["corpus"] if "corpus" in doc else None
        detail = (
            "the key is absent" if "corpus" not in doc
            else "it is empty" if isinstance(found, dict)
            else f"it is {type(found).__name__} ({found!r})"
        )
        raise AssembleError(
            f"workload {where}: 'corpus' must be a non-empty mapping, but "
            f"{detail}. A corpus workload declares corpus.upstream.source at "
            f"minimum — see pipeline/README.md#corpus-workload-schema"
        )
    _validate_corpus_keys(doc["corpus"], where)
    _validate_corpus_values(doc["corpus"], where)
    _validate_format_conditional(doc["corpus"], where)
    _validate_replay(doc.get("replay"), where)


def _validate_top_level(doc: dict, where: str) -> None:
    extra = set(doc) - _LEGAL_TOP_LEVEL
    mixed = sorted(k for k in extra if k in _GENERATIVE_MARKERS)
    if mixed:
        raise AssembleError(
            f"workload {where} declares both 'corpus' and {mixed}; a workload "
            f"must be exactly one kind (corpus or generative)"
        )
    if extra:
        raise AssembleError(
            f"workload {where}: unrecognized top-level key(s) {sorted(extra)}. "
            f"A corpus workload declares only 'corpus' and 'replay'. In "
            f"particular there is no 'name:' — the workload name is the "
            f"filename stem — and no 'kind:', since 'corpus:' itself is the "
            f"discriminator"
        )


def _validate_corpus_keys(corpus: dict, where: str) -> None:
    """Reject unknown sections, then unknown or deferred keys within them."""
    unknown_sections = set(corpus) - set(CORPUS_FIELDS)
    if unknown_sections:
        raise AssembleError(
            f"workload {where}: unrecognized corpus section(s) "
            f"{sorted(unknown_sections)}. Legal sections: "
            f"{sorted(CORPUS_FIELDS)}"
        )
    for section, fields in CORPUS_FIELDS.items():
        if section not in corpus:
            continue
        written = corpus[section]
        if not isinstance(written, dict):
            raise AssembleError(
                f"workload {where}: corpus.{section} must be a mapping"
            )
        for key in written:
            path = f"corpus.{section}.{key}"
            # Deferred BEFORE unknown: a known-but-unhonored field deserves its
            # own explanation rather than being reported as a typo.
            if path in DEFERRED_FIELDS:
                raise AssembleError(
                    f"workload {where}: {path} {DEFERRED_FIELDS[path]}"
                )
            if key not in fields:
                raise AssembleError(
                    f"workload {where}: unrecognized key {path}. Legal keys "
                    f"under corpus.{section}: {sorted(fields)}"
                )


def _validate_corpus_values(corpus: dict, where: str) -> None:
    for section, fields in CORPUS_FIELDS.items():
        written = corpus.get(section) or {}
        for key, field in fields.items():
            path = f"corpus.{section}.{key}"
            if key not in written:
                if field.default is REQUIRED:
                    raise AssembleError(f"workload {where}: {path} is required")
                continue
            value = written[key]
            if not field.check(value):
                raise AssembleError(
                    f"workload {where}: {path} {field.describe}, got {value!r}"
                )


def _validate_format_conditional(corpus: dict, where: str) -> None:
    """Refuse fields that the selected ``format`` renders inert.

    Runs AFTER value validation, so the format itself is already known good.
    Only a field the document actually WROTE is refused — an omitted field is a
    default, not a request, and refusing those would make ``weka-jsonl``
    unreachable without also spelling out three fields that do nothing.
    """
    written_format = (corpus.get("upstream") or {}).get("format")
    if written_format != "weka-jsonl":
        return
    for path, reason in WEKA_INERT_FIELDS.items():
        _, section, key = path.split(".")
        if key in (corpus.get(section) or {}):
            raise AssembleError(
                f"workload {where}: {path} has no effect when "
                f"corpus.upstream.format is 'weka-jsonl'. It {reason}. Remove "
                f"the field, or use format 'otel-parquet' if you need it. Note "
                f"that removing it does NOT give the weka path the behaviour: "
                f"the capability is absent there, not merely unset"
            )


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
            f"mutually exclusive — 'total_sessions' bounds the run by session "
            f"count and 'duration' bounds it by the clock. blis rejects the "
            f"pair on SUPPLIED-NESS, not value, so 'total_sessions: 0' "
            f"alongside 'duration' is a conflict rather than an override: "
            f"remove the key you do not want, do not set it to 0 or null"
        )
    if not written:
        raise AssembleError(
            f"workload {where}: replay must declare exactly one of "
            f"{sorted(REPLAY_ONE_OF)} to size the run — 'total_sessions' by "
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
