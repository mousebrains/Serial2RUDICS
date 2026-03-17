import math
import re
import socket
import sys
import os
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from RUDICS import RUDICS, MAX_BUFFER_SIZE, MAX_LINE_SIZE
from tests.conftest import make_args


# ---------------------------------------------------------------------------
# Construction / state
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_default_trigger_on_is_compiled_pattern(self):
        r = RUDICS(make_args())
        assert isinstance(r.triggerOn, re.Pattern)

    def test_default_trigger_off_is_compiled_pattern(self):
        r = RUDICS(make_args())
        assert isinstance(r.triggerOff, re.Pattern)

    def test_custom_trigger_on_overrides_default(self):
        r = RUDICS(make_args(triggerOn=["my_custom_pattern"]))
        assert r.triggerOn.search(b"my_custom_pattern") is not None
        # The default pattern should NOT match
        assert r.triggerOn.search(b"surface_0: Picking iridium or freewave") is None

    def test_custom_trigger_off_overrides_default(self):
        r = RUDICS(make_args(triggerOff=["custom_off_trigger"]))
        assert r.triggerOff.search(b"custom_off_trigger") is not None
        assert r.triggerOff.search(b"surface_0: Waiting for final gps fix") is None

    def test_seconds_per_byte_none_without_baudrate(self):
        r = RUDICS(make_args(rudicsBaudrate=None))
        assert r.secondsPerByte is None

    def test_seconds_per_byte_none_for_zero_baudrate(self):
        r = RUDICS(make_args(rudicsBaudrate=0))
        assert r.secondsPerByte is None

    def test_seconds_per_byte_calculated_with_baudrate(self):
        r = RUDICS(make_args(rudicsBaudrate=9600))
        assert r.secondsPerByte == pytest.approx(9 / 9600)

    def test_initial_qWantOpen_true_by_default(self):
        r = RUDICS(make_args())
        assert r.qWantOpen is True

    def test_initial_qWantOpen_false_when_disconnected(self):
        r = RUDICS(make_args(disconnected=True))
        assert r.qWantOpen is False


# ---------------------------------------------------------------------------
# Trigger detection via put()
# ---------------------------------------------------------------------------

class TestPut:
    def test_trigger_on_picking_iridium(self):
        r = RUDICS(make_args(disconnected=True))
        assert r.qWantOpen is False
        r.put(b"surface_0:2024/01/01 00:00:00 Picking iridium or freewave\n")
        assert r.qWantOpen is True

    def test_trigger_on_abort_the_mission(self):
        r = RUDICS(make_args(disconnected=True))
        r.put(b"mission_0: abort_the_mission\n")
        assert r.qWantOpen is True

    def test_trigger_off_waiting_for_final_gps(self):
        r = RUDICS(make_args())
        assert r.qWantOpen is True
        r.put(b"surface_0:2024/01/01 00:00:00 Waiting for final gps fix\n")
        assert r.qWantOpen is False

    def test_trigger_off_state_active(self):
        r = RUDICS(make_args())
        assert r.qWantOpen is True
        r.put(b"surface_1:2024/01/01 01:00:00 STATE Active ->\n")
        assert r.qWantOpen is False

    def test_no_trigger_on_filler_sensor_data(self):
        r = RUDICS(make_args())
        original = r.qWantOpen
        r.put(b"sensor: depth=100 temp=12.3 sal=34.5\n")
        assert r.qWantOpen == original

    def test_partial_line_buffers_without_triggering(self):
        r = RUDICS(make_args(disconnected=True))
        # Partial line: no newline, so trigger should NOT fire
        r.put(b"surface_0:2024/01/01 00:00:00 Picking iridium or freewave")
        assert r.qWantOpen is False
        assert len(r.line) > 0

    def test_multi_line_chunk_processes_all_complete_lines(self):
        r = RUDICS(make_args(disconnected=True))
        chunk = (
            b"sensor: depth=10\n"
            b"mission_0: abort_the_mission\n"
            b"sensor: depth=20\n"
        )
        r.put(chunk)
        # abort_the_mission should have fired triggerOn
        assert r.qWantOpen is True

    def test_line_buffer_resets_when_exceeding_max_line_size(self):
        r = RUDICS(make_args())
        # Feed data just over MAX_LINE_SIZE without a newline
        big_chunk = b"x" * (MAX_LINE_SIZE + 1)
        r.put(big_chunk)
        # The line buffer should have been reset to just big_chunk (the current c)
        assert len(r.line) == len(big_chunk)

        # Now send a small piece that pushes over the limit again
        r.line = bytearray(b"y" * MAX_LINE_SIZE)
        r.put(b"zz")
        # After exceeding, line should be reset to just the new bytes
        assert r.line == bytearray(b"zz")

    def test_buffer_accumulates_when_qWantOpen_true(self):
        r = RUDICS(make_args())  # qWantOpen=True by default
        r.put(b"sensor: depth=100\n")
        assert len(r.buffer) > 0

    def test_buffer_unchanged_when_qWantOpen_false(self):
        r = RUDICS(make_args(disconnected=True))
        assert r.qWantOpen is False
        r.put(b"sensor: depth=100\n")
        # No trigger fires and qWantOpen stays False, so no buffering
        assert len(r.buffer) == 0

    def test_buffer_capped_at_max_buffer_size(self):
        r = RUDICS(make_args())
        # Fill buffer right up to the limit
        r.buffer = bytearray(b"x" * MAX_BUFFER_SIZE)
        r.put(b"sensor: depth=100\n")
        # Buffer should not have grown beyond MAX_BUFFER_SIZE
        assert len(r.buffer) == MAX_BUFFER_SIZE

    def test_triggering_chunk_captured_in_buffer(self):
        r = RUDICS(make_args(disconnected=True))
        trigger_line = b"surface_0:2024/01/01 00:00:00 Picking iridium or freewave\n"
        r.put(trigger_line)
        assert r.qWantOpen is True
        # The trigger chunk itself should be in the buffer
        assert trigger_line in r.buffer


# ---------------------------------------------------------------------------
# timeout()
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_returns_idle_timeout_when_no_connection(self):
        r = RUDICS(make_args(idleTimeout=42))
        assert r.tLastOpen == 0
        dt = r.timeout()
        assert dt == pytest.approx(42.0, abs=1.0)

    def test_respects_max_open_time_when_connected(self):
        r = RUDICS(make_args(rudicsMaxOpenTime=10, idleTimeout=3600))
        now = time.time()
        r.tLastOpen = now - 5  # Opened 5 seconds ago
        r.tLastAction = now
        dt = r.timeout()
        # Should wake up in ~5 seconds (10 - 5) for max open time
        assert dt <= 6.0

    def test_includes_tNextOpen_delay_for_reconnect(self):
        r = RUDICS(make_args(idleTimeout=3600))
        r.qWantOpen = True
        r.s = None
        now = time.time()
        r.tNextOpen = now + 3.0
        dt = r.timeout()
        assert dt <= 4.0

    def test_baudrate_send_delay_in_buffer(self):
        r = RUDICS(make_args(rudicsBaudrate=9600, idleTimeout=3600))
        r.buffer = bytearray(b"hello")
        now = time.time()
        r.tNextSend = now + 2.0
        dt = r.timeout()
        assert dt <= 3.0


# ---------------------------------------------------------------------------
# timedOut()
# ---------------------------------------------------------------------------

class TestTimedOut:
    def test_noop_when_tLastOpen_is_zero(self):
        r = RUDICS(make_args())
        r.tLastOpen = 0
        r.timedOut()
        # Nothing should change
        assert r.qWantOpen is True

    def test_closes_on_idle_timeout(self):
        r = RUDICS(make_args(idleTimeout=5))
        # Simulate: socket is "open" via a socketpair, but idle for longer than timeout
        a, b = socket.socketpair()
        try:
            r.s = a
            now = time.time()
            r.tLastOpen = now - 10
            r.tLastAction = now - 10
            r.qWantOpen = True
            r.timedOut()
            assert r.s is None
            assert r.qWantOpen is False
        finally:
            b.close()

    def test_closes_on_max_open_time(self):
        r = RUDICS(make_args(rudicsMaxOpenTime=10, rudicsMaxOpenTimeDelay=5))
        a, b = socket.socketpair()
        try:
            r.s = a
            now = time.time()
            r.tLastOpen = now - 15  # Exceeds rudicsMaxOpenTime of 10
            r.tLastAction = now  # Recent activity, so idle timeout won't fire first
            r.qWantOpen = True
            r.timedOut()
            assert r.s is None
            # After max open time, qWantOpen should be True (wants to reconnect)
            assert r.qWantOpen is True
            # tNextOpen should be set into the future
            assert r.tNextOpen >= now + 4
        finally:
            b.close()


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------

class TestSend:
    def test_noop_on_empty_buffer(self):
        a, b = socket.socketpair()
        try:
            r = RUDICS(make_args())
            r.s = a
            r.buffer = bytearray()
            r.send()
            # Nothing sent
            b.setblocking(False)
            with pytest.raises(BlockingIOError):
                b.recv(1)
        finally:
            a.close()
            b.close()

    def test_noop_without_socket(self):
        r = RUDICS(make_args())
        r.buffer = bytearray(b"hello")
        r.s = None
        r.send()
        # Buffer unchanged
        assert r.buffer == bytearray(b"hello")

    def test_sends_full_buffer_without_baudrate(self):
        a, b = socket.socketpair()
        try:
            r = RUDICS(make_args(rudicsBaudrate=None))
            r.s = a
            payload = b"hello world 12345"
            r.buffer = bytearray(payload)
            r.tNextSend = 0  # Not rate-limited
            r.send()
            received = b""
            b.setblocking(False)
            try:
                while True:
                    received += b.recv(4096)
            except BlockingIOError:
                pass
            assert received == payload
            assert len(r.buffer) == 0
        finally:
            a.close()
            b.close()

    def test_sends_proportional_to_elapsed_time_with_baudrate(self):
        a, b = socket.socketpair()
        try:
            baud = 9600
            r = RUDICS(make_args(rudicsBaudrate=baud))
            r.s = a
            r.buffer = bytearray(b"x" * 1000)
            now = time.time()
            # Simulate: last send was 0.01 seconds ago
            dt = 0.01
            r.tLastSend = now - dt
            r.tNextSend = 0
            r.send()
            expected_bytes = math.floor(dt / (9 / baud))
            # Some bytes should have been sent, but not all 1000
            remaining = len(r.buffer)
            sent = 1000 - remaining
            assert sent > 0
            assert sent <= expected_bytes + 1  # Allow small timing variance
            assert remaining > 0
        finally:
            a.close()
            b.close()


# ---------------------------------------------------------------------------
# open() / close()
# ---------------------------------------------------------------------------

class TestOpenClose:
    def test_open_connects_to_faux_dockserver(self, rudics_args):
        r = RUDICS(rudics_args)
        r.open()
        assert r.s is not None
        assert r.tLastOpen > 0
        r.close()

    def test_close_sets_spacing_delay(self, rudics_args):
        r = RUDICS(rudics_args)
        r.open()
        assert r.s is not None
        now_before = time.time()
        r.close()
        assert r.s is None
        # tNextOpen should be in the future by at least rudicsSpacing
        assert r.tNextOpen >= now_before + rudics_args.rudicsSpacing * 0.9

    def test_close_is_idempotent(self, rudics_args):
        r = RUDICS(rudics_args)
        r.open()
        r.close()
        first_tNextOpen = r.tNextOpen
        # Second close should be a no-op (socket is already None)
        r.close()
        assert r.s is None
        # tNextOpen should not change on second close since s was already None
        assert r.tNextOpen == first_tNextOpen


# ---------------------------------------------------------------------------
# __bool__ / fileno helpers
# ---------------------------------------------------------------------------

class TestBoolFileno:
    def test_true_when_socket_open(self):
        a, b = socket.socketpair()
        try:
            r = RUDICS(make_args())
            r.s = a
            r.qWantOpen = False
            assert bool(r) is True
        finally:
            a.close()
            b.close()

    def test_true_when_qWantOpen(self):
        r = RUDICS(make_args())
        r.s = None
        r.qWantOpen = True
        assert bool(r) is True

    def test_false_when_closed_and_not_wanting_open(self):
        r = RUDICS(make_args(disconnected=True))
        r.s = None
        r.qWantOpen = False
        assert bool(r) is False

    def test_inputFileno_triggers_open(self, faux_dockserver):
        args = make_args(host=faux_dockserver.host, port=faux_dockserver.port)
        r = RUDICS(args)
        r.qWantOpen = True
        r.s = None
        result = r.inputFileno()
        assert result is not None
        assert r.s is not None
        r.close()

    def test_outputFileno_returns_none_with_empty_buffer(self):
        r = RUDICS(make_args())
        r.buffer = bytearray()
        r.s = None
        r.qWantOpen = False
        assert r.outputFileno() is None
