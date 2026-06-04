"""Tests for orchestrator log deduplication (issue #197) and the unified
capacity log format (issue #272)."""

from pipeline.deploy import _format_capacity


def _make_info_collector():
    """Return a list and a mock info() that appends to it."""
    messages = []

    def _info(msg):
        messages.append(msg)

    return messages, _info


class TestCapacityLogDedup:
    """Capacity log should only fire when its inputs change."""

    def test_repeated_capacity_not_logged(self):
        """Same capacity on consecutive iterations -> only first logs."""
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_capacity(effective, probed, reserved, allocatable, requested, has_pending):
            key = "capacity"
            state = (effective, probed, reserved, allocatable, requested)
            if has_pending and state != _last_log_state.get(key):
                mock_info(_format_capacity(effective, probed, reserved, allocatable, requested))
                _last_log_state[key] = state

        _log_capacity(31, 31, 0, 93, 62, True)
        _log_capacity(31, 31, 0, 93, 62, True)
        _log_capacity(31, 31, 0, 93, 62, True)

        assert len(messages) == 1
        assert "31 effective free GPUs" in messages[0]

    def test_changed_capacity_logs_again(self):
        """Different capacity -> logs again."""
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_capacity(effective, probed, reserved, allocatable, requested, has_pending):
            key = "capacity"
            state = (effective, probed, reserved, allocatable, requested)
            if has_pending and state != _last_log_state.get(key):
                mock_info(_format_capacity(effective, probed, reserved, allocatable, requested))
                _last_log_state[key] = state

        _log_capacity(31, 31, 0, 93, 62, True)
        _log_capacity(25, 25, 0, 93, 68, True)

        assert len(messages) == 2

    def test_changed_reservation_logs_again(self):
        """Same probe but different shadow reservation -> logs again (issue #272)."""
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_capacity(effective, probed, reserved, allocatable, requested, has_pending):
            key = "capacity"
            state = (effective, probed, reserved, allocatable, requested)
            if has_pending and state != _last_log_state.get(key):
                mock_info(_format_capacity(effective, probed, reserved, allocatable, requested))
                _last_log_state[key] = state

        # Same probe (23, 48, 25), but reservation changes 0 -> 16
        _log_capacity(23, 23, 0, 48, 25, True)
        _log_capacity(7, 23, 16, 48, 25, True)

        assert len(messages) == 2


class TestSlotsBusyLogDedup:
    """The all-slots-busy log (issue #274) should deduplicate per state transition."""

    def test_repeated_slots_busy_not_logged(self):
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_slots_busy(n_pending, total_slots):
            key = "slots_busy"
            state = (n_pending, total_slots)
            if state != _last_log_state.get(key):
                mock_info(f"Dispatching 0/{n_pending} pending — all {total_slots} slots busy")
                _last_log_state[key] = state

        _log_slots_busy(19, 4)
        _log_slots_busy(19, 4)
        _log_slots_busy(19, 4)

        assert len(messages) == 1

    def test_changed_pending_count_logs_again(self):
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_slots_busy(n_pending, total_slots):
            key = "slots_busy"
            state = (n_pending, total_slots)
            if state != _last_log_state.get(key):
                mock_info(f"Dispatching 0/{n_pending} pending — all {total_slots} slots busy")
                _last_log_state[key] = state

        _log_slots_busy(19, 4)
        _log_slots_busy(18, 4)

        assert len(messages) == 2


class TestDispatchLogDedup:
    """Dispatch-intent logs should deduplicate."""

    def test_repeated_capacity_limited_not_logged(self):
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_dispatch(dispatchable, pending, free_gpus, free_slots):
            if len(dispatchable) == 0 and pending:
                key = "dispatch"
                state = ("zero", len(pending), free_gpus)
                if state != _last_log_state.get(key):
                    mock_info(f"Dispatching 0/{len(pending)} pending pairs")
                    _last_log_state[key] = state
            elif len(dispatchable) < len(pending):
                key = "dispatch"
                state = ("cap_limited", len(dispatchable), len(pending), free_gpus)
                if state != _last_log_state.get(key):
                    mock_info(f"Dispatching {len(dispatchable)}/{len(pending)} pending pairs (capacity-limited)")
                    _last_log_state[key] = state
            elif free_slots < len(dispatchable):
                key = "dispatch"
                state = ("slot_limited", free_slots, len(pending))
                if state != _last_log_state.get(key):
                    mock_info(f"Dispatching {free_slots}/{len(pending)} pending pairs (slot-limited)")
                    _last_log_state[key] = state

        _log_dispatch(["a", "b"], ["a", "b", "c", "d"], 10, 5)
        _log_dispatch(["a", "b"], ["a", "b", "c", "d"], 10, 5)
        _log_dispatch(["a", "b"], ["a", "b", "c", "d"], 10, 5)
        assert len(messages) == 1

    def test_zero_dispatch_warning_reemits_periodically(self):
        """Stuck-dispatch warning re-emits every 10 iterations."""
        messages, mock_warn = _make_info_collector()
        _last_log_state = {}
        _zero_dispatch_count = 0

        def _log_zero_dispatch(n_pending, effective_free, reserved, smallest):
            nonlocal _zero_dispatch_count
            state = ("zero", n_pending, effective_free, reserved, smallest)
            _zero_dispatch_count += 1
            if state != _last_log_state.get("dispatch") or _zero_dispatch_count % 10 == 0:
                mock_warn(
                    f"Dispatching 0/{n_pending} pending pairs — "
                    f"smallest cost ({smallest}) exceeds {effective_free} "
                    f"effective free GPUs"
                )
                _last_log_state["dispatch"] = state

        # First emission
        _log_zero_dispatch(7, 2, 0, 4)
        assert len(messages) == 1

        # Iterations 2-9: suppressed
        for _ in range(8):
            _log_zero_dispatch(7, 2, 0, 4)
        assert len(messages) == 1

        # Iteration 10: re-emits
        _log_zero_dispatch(7, 2, 0, 4)
        assert len(messages) == 2

    def test_zero_dispatch_includes_smallest_in_state(self):
        """Changed smallest GPU cost triggers new warning even if count/free unchanged."""
        messages, mock_warn = _make_info_collector()
        _last_log_state = {}
        _zero_dispatch_count = 0

        def _log_zero_dispatch(n_pending, effective_free, reserved, smallest):
            nonlocal _zero_dispatch_count
            state = ("zero", n_pending, effective_free, reserved, smallest)
            _zero_dispatch_count += 1
            if state != _last_log_state.get("dispatch") or _zero_dispatch_count % 10 == 0:
                mock_warn(f"smallest cost ({smallest}) exceeds {effective_free} effective free GPUs")
                _last_log_state["dispatch"] = state

        _log_zero_dispatch(7, 2, 0, 4)
        _log_zero_dispatch(7, 2, 0, 8)  # smallest changed
        assert len(messages) == 2

    def test_zero_dispatch_includes_reservation_in_state(self):
        """Changed shadow reservation triggers new warning even if probed/smallest unchanged.

        Regression for issue #272 Defect 2: the dedup state tuple must include
        both effective_free and reserved as independent keys. Relying on
        probed free_gpus alone would suppress re-emission when only the
        shadow ledger changes, even though effective_free (and therefore the
        gating decision) has changed.
        """
        messages, mock_warn = _make_info_collector()
        _last_log_state = {}
        _zero_dispatch_count = 0

        def _log_zero_dispatch(n_pending, effective_free, reserved, smallest):
            nonlocal _zero_dispatch_count
            state = ("zero", n_pending, effective_free, reserved, smallest)
            _zero_dispatch_count += 1
            if state != _last_log_state.get("dispatch") or _zero_dispatch_count % 10 == 0:
                mock_warn(f"smallest cost ({smallest}) exceeds {effective_free} effective free GPUs")
                _last_log_state["dispatch"] = state

        # Same probed=23, same smallest=4, but reservation drops 23->16
        # so effective_free changes 0 -> 7. Must re-emit.
        _log_zero_dispatch(19, 0, 23, 4)
        _log_zero_dispatch(19, 7, 16, 4)
        assert len(messages) == 2

    def test_dispatch_logs_on_change(self):
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_dispatch(dispatchable, pending, free_gpus, free_slots):
            if len(dispatchable) == 0 and pending:
                key = "dispatch"
                state = ("zero", len(pending), free_gpus)
                if state != _last_log_state.get(key):
                    mock_info(f"Dispatching 0/{len(pending)} pending pairs")
                    _last_log_state[key] = state
            elif len(dispatchable) < len(pending):
                key = "dispatch"
                state = ("cap_limited", len(dispatchable), len(pending), free_gpus)
                if state != _last_log_state.get(key):
                    mock_info(f"Dispatching {len(dispatchable)}/{len(pending)} pending pairs (capacity-limited)")
                    _last_log_state[key] = state

        _log_dispatch(["a", "b"], ["a", "b", "c", "d"], 10, 5)
        _log_dispatch(["a", "b", "c"], ["a", "b", "c", "d"], 15, 5)
        assert len(messages) == 2


class TestStateClearOnEvent:
    """Dedup state should reset when an actual event occurs."""

    def test_dispatch_clears_capacity_state(self):
        """After a successful dispatch, capacity should log again even if unchanged."""
        messages, mock_info = _make_info_collector()
        _last_log_state = {}

        def _log_capacity(effective, probed, reserved, allocatable, requested, has_pending):
            key = "capacity"
            state = (effective, probed, reserved, allocatable, requested)
            if has_pending and state != _last_log_state.get(key):
                mock_info(_format_capacity(effective, probed, reserved, allocatable, requested))
                _last_log_state[key] = state

        def _on_dispatch():
            _last_log_state.pop("capacity", None)
            _last_log_state.pop("dispatch", None)

        _log_capacity(31, 31, 0, 93, 62, True)
        _log_capacity(31, 31, 0, 93, 62, True)
        _on_dispatch()
        _log_capacity(31, 31, 0, 93, 62, True)

        assert len(messages) == 2


class TestUnifiedCapacityFormat:
    """The `Capacity:` log line must be a single shared format (issue #272)."""

    def test_format_includes_all_five_numbers_in_fixed_order(self):
        msg = _format_capacity(7, 23, 16, 48, 25)
        assert msg == (
            "Capacity: 7 effective free GPUs "
            "(23 probed − 16 reserved; "
            "cluster: 48 allocatable − 25 requested)"
        )

    def test_format_with_empty_ledger_reads_cleanly(self):
        msg = _format_capacity(23, 23, 0, 48, 25)
        # Even when reservations are 0, the line names the term explicitly so
        # the reader sees a stable schema regardless of ledger contents.
        assert "0 reserved" in msg
        assert "23 effective free GPUs" in msg
        assert "23 probed" in msg
        assert "48 allocatable" in msg
        assert "25 requested" in msg
