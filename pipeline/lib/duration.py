"""Go duration-string validation — one definition, two consumers.

Several values this repo emits land on a Cobra ``DurationVar`` in blis, which
parses them with Go's ``time.ParseDuration``:

* ``corpus.reconstruct.max_think_time`` → ``--max-think-time`` on
  ``blis convert otel`` / ``blis convert weka`` (``cmd/convert_otel.go:185``,
  ``cmd/convert_weka.go:132``).
* the measurement protocol's ``prewarmDuration`` → ``--prewarm-duration`` on
  ``blis observe`` (``cmd/observe_cmd.go:154``).

Both are validated by :func:`is_go_duration` so the two cannot drift apart.

WHY A REAL PARSER-SHAPED CHECK AND NOT ``isinstance(value, str)``. A type check
catches a bare YAML integer, which is worth catching but is not the dangerous
case, because Go rejects an unitless number itself::

    "15000000"    -> ERROR: time: missing unit in duration
    "15000000ns"  -> 15ms        <- the dangerous one: silent, 1000x too tight
    "15000000us"  -> 15s         <- right, by accident
    "-5s"         -> -5s         <- Go accepts; blis then refuses it
    "9999999999s" -> ERROR: out of range (exceeds int64 nanoseconds)

So the unitless form fails LOUDLY but LATE — inside a cluster pod, after a
namespace slot and a corpus download have been spent. Refusing it here moves
that failure into the repo. The genuinely silent error is a WRONG UNIT: TraceV2's
column is ``think_time_us``, so an operator transcribing a number out of it
reaches for a unit, and ``ns`` is 1000x too tight while nothing anywhere fails.
No predicate can catch that one — what this module can do is force the unit to be
WRITTEN, so the mistake is visible in review instead of implied by a bare number.

DELIBERATE DIVERGENCES from ``time.ParseDuration``, both narrowing:

1. A NEGATIVE duration is refused, though Go parses it. Both converters
   (``convert_otel.go:47``, ``convert_weka.go:49``) and ``blis observe``
   (``observe_cmd.go:355``) reject ``< 0`` themselves, so accepting one here only
   defers the same failure onto the cluster. ``"-0"`` is refused with the rest:
   Go calls it a valid zero, but a minus sign in a duration is worth a second
   look and ``"0"`` says the same thing unambiguously.
2. Int64-nanosecond OVERFLOW is refused, as Go refuses it — the check is here
   rather than left to Go for the same fail-early reason as (1).

Verified differentially against Go's own ``time.ParseDuration`` over 66 inputs
(valid forms, unitless forms, negatives, both ``us`` spellings, multi-unit,
fractional, malformed, and overflow), with ``"-0"`` the only intended
divergence. The Go harness is not committed because CI has no Go toolchain; the
table in :data:`_UNIT_NS` and the grammar below are what it validated.
"""
import decimal
import re

#: Nanoseconds per unit, exactly the units ``time.ParseDuration`` accepts.
#: ``us`` has three legal spellings — plain ``us``, MICRO SIGN (U+00B5), and
#: GREEK SMALL LETTER MU (U+03BC) — and Go accepts all three, so refusing any of
#: them would reject a value blis would have taken.
_UNIT_NS: dict[str, int] = {
    "ns": 1,
    "us": 1_000,
    "µs": 1_000,
    "μs": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60 * 1_000_000_000,
    "h": 3600 * 1_000_000_000,
}

#: Longest-first so ``ms`` is not shadowed by ``s``, and ``ns``/``us`` are not
#: shadowed by a bare unit. Python's ``|`` is first-match, not longest-match, so
#: this ordering is load-bearing rather than cosmetic.
_UNITS = "|".join(sorted(_UNIT_NS, key=len, reverse=True))

#: One numeric term: ``5``, ``5.``, ``5.5``, or ``.5`` — the same shapes Go's
#: ``[0-9]*(\.[0-9]*)?`` admits, minus the all-empty case it rejects anyway.
_NUM = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"

#: A whole duration: one or more number+unit terms, optional leading ``+``. No
#: ``-`` branch — see divergence (1) in the module docstring.
_DURATION_RE = re.compile(rf"^\+?(?:{_NUM}(?:{_UNITS}))+$")

#: One term, captured, for summing nanoseconds once the whole string is known
#: well-formed. Never run against unvalidated input: it matches substrings.
_TERM_RE = re.compile(rf"({_NUM})({_UNITS})")

#: Go stores a Duration as int64 nanoseconds, so this is the representable
#: ceiling (~292 years).
_MAX_INT64 = 2**63 - 1

#: The unit rule, shared by every ``describe`` below so one phrasing explains it
#: everywhere it is enforced.
_DESCRIBE_UNIT = (
    "must be a Go duration STRING with an explicit unit (e.g. '60s', '15m', "
    "'1h30m')"
)
_DESCRIBE_WHY = (
    "A bare number is refused because the unit is what distinguishes 15s from "
    "15ms"
)

#: For consumers where zero is MEANINGFUL (``max_think_time``: "no cap").
DESCRIBE = (
    f"{_DESCRIBE_UNIT}; '0' is the one accepted unitless value. {_DESCRIBE_WHY}"
)

#: For consumers where zero is REFUSED (``replay.duration``). The ``'0' is the
#: one accepted unitless value`` clause is dropped rather than contradicted: an
#: operator who wrote ``0`` would otherwise read that clause first and conclude
#: the value is fine, which is the opposite of the rule being enforced on them.
DESCRIBE_NONZERO = f"{_DESCRIBE_UNIT}, and must be non-zero. {_DESCRIBE_WHY}"


def is_go_duration(value) -> bool:
    """Return True iff ``value`` is a duration string blis will accept.

    That is: parseable by Go's ``time.ParseDuration``, non-negative, and within
    int64 nanoseconds. ``bool`` is checked before ``str`` for the usual reason —
    it is an ``int`` subclass — even though a bool could not match the grammar,
    so the rejection reason stays "wrong type" rather than "wrong shape".
    """
    if isinstance(value, bool) or not isinstance(value, str):
        return False
    # Go special-cases a lone zero as the only legal unitless duration, and
    # prepare-trace documents "0" as the way to say "no cap".
    if value in ("0", "+0"):
        return True
    if not _DURATION_RE.match(value):
        return False
    # Decimal, not float: at the int64 ceiling a float comparison rounds and
    # would admit a value Go refuses as out of range.
    total = decimal.Decimal(0)
    for num, unit in _TERM_RE.findall(value):
        total += decimal.Decimal(num) * _UNIT_NS[unit]
        if total > _MAX_INT64:
            return False
    return True


def is_positive_go_duration(value) -> bool:
    """Return True iff ``value`` is a duration blis will accept AND is non-zero.

    A strictly narrower sibling of :func:`is_go_duration`, which accepts ``"0"``
    and ``"0s"`` deliberately — ``prepare-trace`` documents ``0`` as "no cap" for
    ``max_think_time``, so the shared predicate must keep taking it.

    ``blis observe --duration`` cannot. Its Cobra ``DurationVar`` defaults to 0,
    and the corpus validator keys its one-of against ``--total-sessions`` on
    SUPPLIED-NESS, reading ``duration == 0`` as "flag not set"
    (``cmd/observe_corpus.go:84``). So a zero passes every check on both sides
    and yields a run bounded by neither the clock nor a session count, while the
    workload document says a time bound was requested. That is the
    accept-and-ignore shape ``corpus_schema`` exists to refuse, so it is refused
    here instead — and note the reason is "blis treats 0 as unset", NOT "blis
    rejects 0". Only a NEGATIVE duration is something blis rejects itself
    (``observe_cmd.go:355``), and :func:`is_go_duration` already refuses those.

    Zero is SUMMED IN NANOSECONDS rather than pattern-matched, because it has
    many spellings and not all of them look like zero. ``0``, ``0s``, ``0.0s``,
    ``.0s`` and ``0h0m0s`` do — but ``0.4ns`` does not, and Go truncates a
    Duration to integer nanoseconds, so it parses to exactly 0 and blis reads it
    as unset. A per-term test on the unscaled COEFFICIENT would accept it
    (``0.4 > 0``); only the scaled sum catches it. The threshold is therefore
    ``>= 1`` nanosecond, the smallest value Go can represent as set.

    Reusing the already-validated :data:`_TERM_RE` and :data:`_UNIT_NS` keeps one
    parser and one unit table for the whole module.
    """
    if not is_go_duration(value):
        return False
    # The two unitless zeros never reach _TERM_RE: is_go_duration short-circuits
    # them before the grammar runs, so findall() would see no terms and the sum
    # would be 0 by accident rather than by decision. Say it explicitly.
    if value in ("0", "+0"):
        return False
    total = decimal.Decimal(0)
    for num, unit in _TERM_RE.findall(value):
        total += decimal.Decimal(num) * _UNIT_NS[unit]
    # Truncation, not rounding, mirrors Go: 0.9ns is 0ns to time.ParseDuration.
    return total >= 1
