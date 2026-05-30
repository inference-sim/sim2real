"""Tests for the shadow GPU reservation ledger."""
from unittest.mock import patch

from pipeline.lib.shadow import ShadowLedger


def test_empty_ledger_reserved_is_zero():
    ledger = ShadowLedger(ttl=120)
    assert ledger.reserved() == 0


def test_record_adds_to_reserved():
    ledger = ShadowLedger(ttl=120)
    ledger.record(4)
    assert ledger.reserved() == 4
    ledger.record(2)
    assert ledger.reserved() == 6


def test_effective_free_subtracts_reserved():
    ledger = ShadowLedger(ttl=120)
    ledger.record(4)
    ledger.record(4)
    assert ledger.effective_free(probed_free=65) == 57


def test_entries_expire_after_ttl():
    ledger = ShadowLedger(ttl=10)
    with patch("time.time", return_value=100.0):
        ledger.record(4)
    with patch("time.time", return_value=105.0):
        ledger.record(2)
    with patch("time.time", return_value=109.0):
        assert ledger.reserved() == 6
    with patch("time.time", return_value=111.0):
        assert ledger.reserved() == 2
    with patch("time.time", return_value=116.0):
        assert ledger.reserved() == 0


def test_ttl_zero_disables_tracking():
    ledger = ShadowLedger(ttl=0)
    ledger.record(4)
    assert ledger.reserved() == 0


def test_effective_free_never_negative():
    ledger = ShadowLedger(ttl=120)
    ledger.record(100)
    assert ledger.effective_free(probed_free=10) == 0
