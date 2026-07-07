"""Tests for pipeline/lib/pairkey.py — grammar parser and iteration-spec parser."""

import pytest

from pipeline.lib.pairkey import (
    PairKeyParts,
    parse_iteration_spec,
    parse_pair_key,
)


# ── parse_pair_key ─────────────────────────────────────────────────────────


class TestParsePairKeyLegacy:
    """Legacy pair keys (no |iN suffix) parse as iteration=1."""

    def test_simple_legacy(self):
        assert parse_pair_key("wl-chat-mid|sim2real-ac") == PairKeyParts(
            workload="chat-mid", package="sim2real-ac", iteration=1
        )

    def test_single_char_identifiers(self):
        assert parse_pair_key("wl-a|b") == PairKeyParts(
            workload="a", package="b", iteration=1
        )

    def test_digits_in_identifiers(self):
        assert parse_pair_key("wl-chat-70b|pkg-v2") == PairKeyParts(
            workload="chat-70b", package="pkg-v2", iteration=1
        )


class TestParsePairKeyWithSuffix:
    """Pair keys with |iN suffix parse to that iteration."""

    def test_i1(self):
        assert parse_pair_key("wl-chat-mid|sim2real-ac|i1") == PairKeyParts(
            workload="chat-mid", package="sim2real-ac", iteration=1
        )

    def test_i3(self):
        assert parse_pair_key("wl-chat-mid|sim2real-ac|i3") == PairKeyParts(
            workload="chat-mid", package="sim2real-ac", iteration=3
        )

    def test_i10(self):
        parts = parse_pair_key("wl-foo|bar|i10")
        assert parts.iteration == 10

    def test_i100(self):
        parts = parse_pair_key("wl-foo|bar|i100")
        assert parts.iteration == 100


class TestParsePairKeyGrammarViolations:
    """Malformed keys raise ValueError naming the offending key."""

    @pytest.mark.parametrize(
        "bad_key",
        [
            "wl-Chat-mid|pkg",        # uppercase in workload
            "wl-chat-mid|Pkg",        # uppercase in package
            "wl-chat|pkg|I1",         # uppercase iteration marker
            "wl-chat|pkg|i01",        # leading zero in iteration
            "wl-chat|pkg|i0",         # zero iteration (must be >= 1)
            "wl-chat|pkg|i",          # missing iteration number
            "wl-chat|pkg|1",          # missing "i" prefix on iteration
            "wl-|pkg",                # empty workload
            "wl-chat|",               # empty package
            "chat-mid|pkg",           # missing "wl-" prefix
            "wl-chat-mid",            # missing package
            "wl-chat|pkg|i1|extra",   # trailing segment
            "wl-chat|pkg|i1|",        # trailing pipe
            "wl-chat||pkg",           # empty middle segment
            "wl--chat|pkg",           # leading hyphen after wl-
            "wl-chat-|pkg",           # trailing hyphen in workload
            "wl-chat|-pkg",           # leading hyphen in package
            "wl-chat|pkg-",           # trailing hyphen in package
            "",                       # empty string
            "wl-",                    # prefix only
            "wl-chat|pkg|i1 ",        # trailing whitespace
            " wl-chat|pkg",           # leading whitespace
            "wl-chat.mid|pkg",        # dot in workload
            "wl-chat_mid|pkg",        # underscore in workload
        ],
    )
    def test_malformed_raises(self, bad_key):
        with pytest.raises(ValueError) as exc_info:
            parse_pair_key(bad_key)
        # Message must name the offending key (verbatim, quoted).
        assert repr(bad_key) in str(exc_info.value)

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            parse_pair_key(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            parse_pair_key(123)  # type: ignore[arg-type]


class TestPairKeyPartsToKey:
    """PairKeyParts.to_key() always emits the canonical |iN suffix."""

    def test_iteration_1_gets_suffix(self):
        parts = PairKeyParts(workload="chat-mid", package="pkg", iteration=1)
        assert parts.to_key() == "wl-chat-mid|pkg|i1"

    def test_iteration_5(self):
        parts = PairKeyParts(workload="foo", package="bar", iteration=5)
        assert parts.to_key() == "wl-foo|bar|i5"

    def test_round_trip_legacy_normalizes(self):
        """Legacy input normalizes to canonical form via to_key()."""
        parts = parse_pair_key("wl-chat|pkg")
        assert parts.to_key() == "wl-chat|pkg|i1"

    def test_round_trip_suffixed(self):
        parts = parse_pair_key("wl-chat|pkg|i7")
        assert parts.to_key() == "wl-chat|pkg|i7"


# ── parse_iteration_spec ────────────────────────────────────────────────────


class TestParseIterationSpecValid:
    """Valid list + range syntax parses correctly."""

    def test_single_value(self):
        assert parse_iteration_spec("2") == {2}

    def test_list(self):
        assert parse_iteration_spec("1,3") == {1, 3}

    def test_range(self):
        assert parse_iteration_spec("1-3") == {1, 2, 3}

    def test_mixed(self):
        assert parse_iteration_spec("1,3-5") == {1, 3, 4, 5}

    def test_overlapping_deduplicates(self):
        assert parse_iteration_spec("1-3,2-4") == {1, 2, 3, 4}

    def test_whitespace_tolerated(self):
        assert parse_iteration_spec("1, 3-5") == {1, 3, 4, 5}
        assert parse_iteration_spec(" 2 ") == {2}
        assert parse_iteration_spec("1 - 3") == {1, 2, 3}

    def test_range_single_point(self):
        assert parse_iteration_spec("4-4") == {4}


class TestParseIterationSpecInvalid:
    """Malformed specs raise ValueError."""

    @pytest.mark.parametrize(
        "bad_spec",
        [
            "0",           # zero not allowed
            "-1",          # negative
            "5-1",         # reversed range
            "abc",         # non-integer
            "1,abc",       # non-integer in list
            "01",          # leading zero
            "1-01",        # leading zero in range endpoint
            "1-",          # missing hi
            "-3",          # missing lo (parses as negative single, not range)
            "1--3",        # double hyphen
            "1,,3",        # empty middle token
            ",",           # only comma
            "",            # empty string
            "   ",         # whitespace only
            "1-2-3",       # three-endpoint range
            "1.5",         # float
        ],
    )
    def test_malformed_raises(self, bad_spec):
        with pytest.raises(ValueError) as exc_info:
            parse_iteration_spec(bad_spec)
        assert repr(bad_spec) in str(exc_info.value)

    def test_non_string_raises(self):
        with pytest.raises(ValueError):
            parse_iteration_spec(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            parse_iteration_spec(1)  # type: ignore[arg-type]
