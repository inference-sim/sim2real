"""Render the whole ``blis observe`` command line at assemble time.

Before issue #900 the command existed in three places: ``assemble`` emitted 12
scalar PipelineRun params, ``pipeline.yaml`` declared 17 of its 31 params purely
to forward them, and the Task's shell reassembled them into an argv. Adding one
flag meant editing three files in two repos, and the effective command existed
nowhere until the pod ran.

Now it is rendered here, once, into a single ``observeArgs`` param. The Task
(``run-workload-blis-observe-binary``, tektonc-data-collection#70) appends
``--server-url`` and runs it, and knows nothing about the workload kind.

WHAT THIS MODULE OWNS, because nothing downstream can:

* **The three mutual exclusions.** Corpus-mode vs spec-mode inputs;
  ``--record-itl`` vs ``--no-streaming`` (blis rejects the pair —
  ``validateITLStreamingFlags``); and ``--saturation-report`` without
  ``--detectors`` (blis errors: "requires --detectors"). All three were
  previously enforced by a shell conditional, by blis at runtime, or not at all.
* **Value validation.** Tekton substitutes params TEXTUALLY into the Task's
  ``OBSERVE_ARGS="$(params.observeArgs)"`` assignment, and the Task word-splits
  the result unquoted. A quote, ``$``, backtick or ``;`` in a rendered value
  therefore breaks out of the assignment before any shell guard can help. Only
  the producer can catch that, so it is caught here.

GLOB CHARACTERS (``*``, ``?``, ``[``) ARE DELIBERATELY NOT REJECTED. An unquoted
expansion also performs pathname expansion, so ``--spec a_*.yaml`` would split
into one argument per matching file — silently, and only when a matching file
happens to exist. The Task closes that with ``set -f`` (disables globbing, keeps
word-splitting), which is the right layer because it covers every producer. If
that ``set -f`` is ever removed, this rejection list becomes incomplete: add
``*?[`` here at the same time.

THE TWO PATHS BELOW ARE OWNED BY THE TASK and must match it exactly:
  * the ``data`` workspace mounts at ``/workspace/data``, so every
    data-PVC-relative path carries that prefix;
  * ``write-workload-spec`` writes the spec to ``/workspace/workload.yaml``.
Both are documented in the Task's ``observeArgs`` param description too, so the
coupling is discoverable from either end.
"""
import shlex

from pipeline.lib import corpus_schema
from pipeline.lib.errors import AssembleError

#: Mount point of the ``data`` workspace inside the observe pod. Task-owned.
_DATA_MOUNT = "/workspace/data"

#: Where ``write-workload-spec`` puts the generative WorkloadSpec. Task-owned.
_WORKLOAD_SPEC_PATH = "/workspace/workload.yaml"

#: Characters that would break out of the Task's ``OBSERVE_ARGS=`` assignment or
#: inject shell syntax, given Tekton's textual substitution. Rejected in EVERY
#: rendered value, including ``extraArgs`` — a ``;`` in a flag tail is command
#: injection into the Task's step and has no legitimate use.
FORBIDDEN_CHARS = "\"'$`;|&><\\\n\r"


class ObserveArgvError(AssembleError):
    """A rendered observe argv would be invalid or ambiguous."""


class _Flag:
    """One ``blis_observe`` key and the flag it drives.

    ``kind`` selects rendering:
      ``"value"``   -> ``--flag <value>``
      ``"present"`` -> ``--flag`` when truthy, nothing otherwise
      ``"absent"``  -> ``--flag`` when FALSY, nothing otherwise (inverted; blis
                       has no ``--streaming``, only ``--no-streaming``)
    """

    __slots__ = ("flag", "default", "kind", "choices", "describe")

    def __init__(self, flag, default, kind="value", choices=None, describe=""):
        self.flag = flag
        self.default = default
        self.kind = kind
        self.choices = choices
        self.describe = describe


#: Valid ``--detectors`` names, mirroring inference-sim's roster
#: (``sim/saturation/bank.go``). ``all`` and comma-lists are also accepted; an
#: EMPTY selection means off, which is blis's own vocabulary ("Empty = off").
DETECTOR_ROSTER = ("composite", "threshold", "backlog-drift", "peak-rate")

#: ``blis_observe`` key -> flag. Order here is the rendered flag order. It
#: reproduces the order today's Task builds, so the AC-1 golden comparison is a
#: straight string equality rather than a set comparison.
#:
#: ``detectors`` replaces the Task's hardcoded ``--post-hoc-detector composite``.
#: That flag was RENAMED to ``--detectors`` upstream (inference-sim #1516,
#: commit 18c2c926) and no longer exists at the pinned blis, so the Task's
#: current invocation dies at argument parsing. The key is named for the flag
#: that exists, not the one that was deleted.
OBSERVE_FLAGS: dict[str, _Flag] = {
    "maxConcurrency": _Flag("--max-concurrency", 10000,
                            describe="must be a positive int"),
    "timeout": _Flag("--timeout", 1800, describe="must be a positive int"),
    "prewarmDuration": _Flag("--prewarm-duration", "60s",
                             describe="must be a Go duration string"),
    "warmupRequests": _Flag("--warmup-requests", 50,
                            describe="must be an int >= 0"),
    "detectors": _Flag("--detectors", "composite",
                       describe=f"must be empty, 'all', or one or more of "
                                f"{', '.join(DETECTOR_ROSTER)}"),
    "apiFormat": _Flag("--api-format", "completions",
                       choices=("completions", "chat"),
                       describe="must be 'completions' or 'chat'"),
    "recordItl": _Flag("--record-itl", False, kind="present",
                       describe="must be a bool"),
    "streaming": _Flag("--no-streaming", True, kind="absent",
                       describe="must be a bool"),
}


def _validate_word(value: str, where: str, *, allow_space: bool) -> None:
    """Reject characters that would corrupt the Task's argv assembly."""
    bad = sorted({c for c in value if c in FORBIDDEN_CHARS})
    if bad:
        shown = ", ".join(repr(c) for c in bad)
        raise ObserveArgvError(
            f"{where} contains character(s) {shown}, which would break out of "
            f"the observe Task's OBSERVE_ARGS assignment (Tekton substitutes "
            f"params textually and the Task word-splits the result). Remove "
            f"them; there is no way to escape them from here"
        )
    if not allow_space and any(c.isspace() for c in value):
        raise ObserveArgvError(
            f"{where} contains whitespace ({value!r}). The Task word-splits "
            f"observeArgs on IFS, so a single flag value cannot contain a "
            f"space — it would arrive as two arguments"
        )


def _validate_detectors(value: str) -> None:
    if value == "":
        return
    if value == "none":
        raise ObserveArgvError(
            f"blis_observe.detectors: 'none' is not a valid selection. blis "
            f"spells 'off' as the EMPTY value, so omit the key or set it to "
            f"''. Valid selections: 'all', or one or more of "
            f"{', '.join(DETECTOR_ROSTER)}"
        )
    for name in value.split(","):
        name = name.strip()
        if name == "all":
            continue
        if name not in DETECTOR_ROSTER:
            raise ObserveArgvError(
                f"blis_observe.detectors: unknown detector {name!r}. Valid: "
                f"'all', or one or more of {', '.join(DETECTOR_ROSTER)}"
            )


def _resolved(observe: dict) -> dict:
    """Fill defaults for every flag key. Emit-always: a value equal to its
    default is still rendered (except the two presence flags, whose default IS
    absence). Suppressing at the default would expose us to blis DEFAULT drift,
    which changes measurements silently; rendering explicitly exposes us to
    FLAG-NAME drift instead, which fails loudly at argument parsing. The
    --post-hoc-detector break is the evidence: loud is the survivable one.
    """
    return {key: observe.get(key, flag.default) for key, flag in OBSERVE_FLAGS.items()}


def _check_workload_source(workload: dict, trace_path: str) -> bool:
    """Return True for corpus-mode. Raise if the request source is ambiguous.

    Exclusion 1. ``blis observe``'s ``--concurrent-sessions`` is mutually
    exclusive with ``--workload-spec``/``--rate``/``--concurrency``. Before #900
    that invariant was upheld in two coupled places — clearing ``workloadSpec``
    in trace mode here, plus a shell conditional in the Task. Now it is one
    function, and the three ways it can be violated get three distinct messages
    rather than one vague one.
    """
    is_corpus = corpus_schema.is_corpus_document(workload)
    generative_markers = sorted(
        k for k in ("clients", "cohorts") if k in workload
    )
    if is_corpus and generative_markers:
        raise ObserveArgvError(
            f"workload declares BOTH corpus-mode inputs ('corpus:') and "
            f"spec-mode inputs ({generative_markers}). blis observe's "
            f"--concurrent-sessions is mutually exclusive with "
            f"--workload-spec, so exactly one kind must be present"
        )
    if is_corpus and not trace_path:
        raise ObserveArgvError(
            "workload is a corpus document but no tracePath was supplied, so "
            "the corpus flags cannot be rendered and blis observe would have "
            "no request source"
        )
    if not is_corpus and trace_path:
        raise ObserveArgvError(
            f"workload is a generative WorkloadSpec but a tracePath "
            f"({trace_path!r}) was supplied. Rendering both --workload-spec and "
            f"the corpus flags would make the request source ambiguous"
        )
    return is_corpus


def _check_exclusions(resolved: dict) -> None:
    """Exclusions 2 and 3 — the pairs blis rejects at runtime."""
    # 2. blis rejects this pair itself (validateITLStreamingFlags): ITL needs
    #    per-chunk timestamps, which only exist for streaming responses. Caught
    #    here so it fails at assemble rather than after a pod is scheduled.
    if resolved["recordItl"] and not resolved["streaming"]:
        raise ObserveArgvError(
            "blis_observe sets recordItl: true with streaming: false. blis "
            "rejects --record-itl together with --no-streaming: ITL recording "
            "captures per-chunk timestamps, which only exist for streaming "
            "responses. Set streaming: true or recordItl: false"
        )


def render_observe_argv(
    *,
    workload: dict,
    observe: dict | None,
    model: str,
    results_dir: str,
    trace_path: str = "",
) -> str:
    """Return the complete ``blis observe`` argv, EXCLUDING ``--server-url``.

    ``--server-url`` is a runtime Task result (the standup task's endpoint), so
    the assembler cannot know it and the Task appends it.

    ``results_dir`` must be the same concrete path ``pipeline.yaml`` passes as
    the Task's ``resultsDir`` — pass ``tekton.build_results_dir(...)`` output so
    the two cannot drift. ``trace_path`` is the corpus cache path for a corpus
    workload and empty for a generative one.

    Raises :class:`ObserveArgvError` on any invalid value or exclusion.
    """
    observe = observe or {}
    unknown = sorted(set(observe) - set(OBSERVE_FLAGS) - {"extraArgs"})
    if unknown:
        raise ObserveArgvError(
            f"blis_observe contains keys the renderer does not know: {unknown}. "
            f"Valid keys: {sorted(set(OBSERVE_FLAGS) | {'extraArgs'})}"
        )

    resolved = _resolved(observe)
    corpus_mode = _check_workload_source(workload, trace_path)
    _check_exclusions(resolved)
    _validate_detectors(str(resolved["detectors"]))

    if resolved["apiFormat"] not in OBSERVE_FLAGS["apiFormat"].choices:
        raise ObserveArgvError(
            f"blis_observe.apiFormat: {resolved['apiFormat']!r} is not valid. "
            f"{OBSERVE_FLAGS['apiFormat'].describe}"
        )

    argv: list[str] = []
    for key, spec in OBSERVE_FLAGS.items():
        value = resolved[key]
        if spec.kind == "present":
            if value:
                argv.append(spec.flag)
            continue
        if spec.kind == "absent":
            if not value:
                argv.append(spec.flag)
            continue
        rendered = str(value)
        # detectors == "" means off: emit neither the flag nor the report that
        # depends on it (exclusion 3 — blis errors on
        # "--saturation-report requires --detectors").
        if key == "detectors" and rendered == "":
            continue
        _validate_word(rendered, f"blis_observe.{key}", allow_space=False)
        argv += [spec.flag, rendered]

    _validate_word(model, "model", allow_space=False)
    argv += ["--model", model]

    if corpus_mode:
        _validate_word(trace_path, "tracePath", allow_space=False)
        argv += [
            "--corpus-header", f"{_DATA_MOUNT}/{trace_path}.yaml",
            "--corpus-data", f"{_DATA_MOUNT}/{trace_path}.csv",
        ]
        replay = workload.get("replay") or {}
        for name, field in corpus_schema.REPLAY_FIELDS.items():
            if name not in replay:
                raise ObserveArgvError(
                    f"replay.{name} is required to render corpus-mode argv; "
                    f"validate the workload document first"
                )
            argv += [field.flag, field.render(replay[name])]
    else:
        argv += ["--workload-spec", _WORKLOAD_SPEC_PATH]

    _validate_word(results_dir, "resultsDir", allow_space=False)
    base = f"{_DATA_MOUNT}/{results_dir}"
    argv += [
        "--trace-header", f"{base}/trace_header.yaml",
        "--trace-data", f"{base}/trace_data.csv",
    ]
    if str(resolved["detectors"]) != "":
        argv += ["--saturation-report", f"{base}/saturation.json"]

    # extraArgs is a flag TAIL, rendered last so it can override anything above
    # (matching its position in the Task's command before #900). Multiple words
    # are its purpose, so whitespace is allowed — but the metacharacter rule
    # still applies, since a ';' here is injection into the Task's step.
    extra = str(observe.get("extraArgs", "") or "").strip()
    if extra:
        _validate_word(extra, "blis_observe.extraArgs", allow_space=True)
        argv += shlex.split(extra)

    # An EMPTY argv must never leave here. Tekton's "required param" means
    # SUPPLIED, not non-empty, so an empty string satisfies the Task's `no
    # default` declaration and reaches blis as `observe --server-url <ep>` —
    # no workload source, and a failure message that names blis's complaint
    # rather than the actual cause. The Task guards this too, but only the
    # renderer can turn it into an assemble-time error naming the cell.
    # Unreachable today (the flag table always renders at least the tuning
    # defaults, --model and a workload source), so this exists to keep a future
    # refactor from silently opening the hole.
    if not argv:
        raise ObserveArgvError(
            "rendered observe argv is empty, so blis observe would run with no "
            "workload source. This is a renderer bug — every cell must resolve "
            "to at least a workload source and the tuning defaults"
        )
    return " ".join(argv)
