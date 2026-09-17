"""Tests for the shared Go duration-string validator (issue #905).

The expected verdicts below were produced by running Go's own
``time.ParseDuration`` over these exact inputs, not by reading its
documentation. That distinction mattered: the premise this validator was
originally specified from — that ``15000000`` silently parses as 15ms — is
wrong. Go refuses an unitless number outright; the silent 1000x error is a wrong
UNIT (``15000000ns``). The cases below encode the measured behaviour.
"""
import pytest

from pipeline.lib import duration

#: Accepted: parseable by time.ParseDuration, non-negative, within int64 ns.
GOOD = [
    "0",                      # the one unitless value Go accepts; "no cap"
    "+0",
    "0s",
    "1s", "60s", "15m", "1h",
    "1ns", "1us",
    "1µs",               # MICRO SIGN spelling of us
    "1μs",               # GREEK SMALL LETTER MU — Go accepts this spelling too
    "1ms",
    "1.5s", ".5s", "5.s",     # Go's [0-9]*(\.[0-9]*)? admits all three
    "1h30m", "1m30s", "1h0m0s", "1h30m45s",
    "100h",
    "+60s",
    "1.000000001s",
    "1s1s",                   # Go sums repeated units rather than refusing them
    "30m1h",                  # ...and does not require descending order
    "9223372036854775807ns",  # exactly int64 max nanoseconds
]

#: Refused. Each entry pairs the value with why it is refused, so a reader can
#: tell "Go refuses this too" from "we are stricter than Go on purpose".
BAD_GO_ALSO_REFUSES = [
    "", " ", "60", "15000000", "abc", "s", "m", "1x", "1 s", "1s ", " 1s",
    "1sec", "1S", "1H", "1Ms", "++1s", "+-1s", "--1s", "1.2.3s", "1e3s",
    "1s1", ".s", ".", "-", "+", "0x1s", "1,5s", "1:30", "P1D", "1d", "1w", "1y",
    "00",                       # only a LONE "0" is special-cased by Go
    "9999999999s",              # ~317 years: over int64 nanoseconds
    "99999999999999999999s",
]

#: Go parses these; we refuse them anyway because blis refuses them downstream
#: (convert_otel.go:47, convert_weka.go:49, observe_cmd.go:355). Refusing here
#: moves the failure off the cluster.
BAD_WE_ARE_STRICTER = ["-5s", "-1h", "-0.5s", "-0"]

#: Non-strings never reach a duration grammar. ``True`` is listed explicitly
#: because bool is an int subclass and would otherwise slip through a naive
#: numeric guard.
BAD_WRONG_TYPE = [60, 0, 1.5, True, False, None, [], {}, ("60s",)]


@pytest.mark.parametrize("value", GOOD)
def test_accepts_what_go_accepts(value):
    assert duration.is_go_duration(value) is True


@pytest.mark.parametrize("value", BAD_GO_ALSO_REFUSES)
def test_refuses_what_go_refuses(value):
    assert duration.is_go_duration(value) is False


@pytest.mark.parametrize("value", BAD_WE_ARE_STRICTER)
def test_refuses_negatives_though_go_parses_them(value):
    """blis rejects a negative duration itself, so accepting one here would only
    defer the same failure until a namespace slot had been spent."""
    assert duration.is_go_duration(value) is False


@pytest.mark.parametrize("value", BAD_WRONG_TYPE)
def test_refuses_non_strings(value):
    assert duration.is_go_duration(value) is False


def test_the_silent_thousandfold_error_is_accepted_and_that_is_the_point():
    """``15000000ns`` is 15ms, not 15s. It is a VALID Go duration, so no
    predicate can refuse it without also refusing legitimate sub-second values.

    This test exists to record that the validator does not claim to catch it —
    what the schema does instead is force the unit to be written, so the mistake
    is visible in review rather than implied by a bare number. If someone later
    decides to restrict the accepted units, this test is where that decision
    should be argued, not silently inverted.
    """
    assert duration.is_go_duration("15000000ns") is True
    assert duration.is_go_duration("15000000us") is True
    # The bare form it would be transcribed from is refused, which is the half
    # that IS mechanically catchable.
    assert duration.is_go_duration("15000000") is False


def test_overflow_boundary_is_exact():
    """Decimal, not float: at the int64 ceiling a float comparison rounds and
    would admit the first value Go refuses."""
    assert duration.is_go_duration("9223372036854775807ns") is True
    assert duration.is_go_duration("9223372036854775808ns") is False


def test_multi_term_overflow_is_caught_across_terms():
    """Each term is small enough alone; only the sum overflows."""
    assert duration.is_go_duration("2562047h47m16s") is True
    assert duration.is_go_duration("2562047h47m16s854775808ns") is False


def test_describe_names_the_rule_and_an_example():
    """The describe string is what an operator sees on a rejection, so it must
    state the rule (explicit unit) and show a value that satisfies it."""
    assert "unit" in duration.DESCRIBE
    assert "60s" in duration.DESCRIBE


def test_every_unit_go_accepts_is_in_the_table():
    """A missing unit would silently refuse a value blis would have taken."""
    assert set(duration._UNIT_NS) == {
        "ns", "us", "µs", "μs", "ms", "s", "m", "h",
    }


def test_longer_units_are_matched_before_shorter_ones():
    """``ms`` must not be read as ``m`` followed by a stray ``s``. The unit
    alternation is ordered longest-first for this reason, and Python's ``|`` is
    first-match rather than longest-match, so the ordering is load-bearing."""
    assert duration.is_go_duration("500ms") is True
    assert duration.is_go_duration("500ns") is True
    assert duration.is_go_duration("500us") is True
