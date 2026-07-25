import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import socket
import threading
import time
import types

import pytest

import FauxDockServer
import FauxSerial
from RealSerial import RealSerial
from RUDICS import RUDICS
from serial2RUDICS import doit as _raw_doit
from tests.conftest import make_args


def doit(serial, rudics, binary=None):
    """Wrapper around the real doit() that suppresses OSError from fd cleanup."""
    try:
        _raw_doit(serial, rudics, binary)
    except OSError:
        pass  # Expected when test closes tty/rudics from outside


def _poll(predicate, timeout=5.0, interval=0.1):
    """Poll *predicate* until it returns True or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _build_surface_cycle(cycle: int) -> str:
    """Return MLG text for one dive-then-surface cycle."""
    lines = [
        f"surface_{cycle}:2024/01/01 00:00:{cycle:02d} Waiting for final gps fix",
        f"sensor: depth={cycle * 100 + 10} temp=12.3 sal=34.5",
        f"sensor: depth={cycle * 100 + 50} temp=11.8 sal=34.6",
        f"surface_{cycle}:2024/01/01 01:00:{cycle:02d} Picking iridium or freewave",
        "iridium: connected to dockserver",
        "iridium: data transfer complete",
    ]
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# Helpers for spinning up FauxSerial + RealSerial + FauxDS + RUDICS
# ---------------------------------------------------------------------------

def _make_faux_serial(tmp_path, mlg_bytes):
    """Create a FauxSerial feeding *mlg_bytes*; return (args, unstarted instance).

    The instance is returned unstarted on purpose: its thread may only run
    once RealSerial holds the PTY slave open. See FauxSerial.setup().
    """
    mlg_file = tmp_path / "input.mlg"
    mlg_file.write_bytes(mlg_bytes)
    args = make_args(input=str(mlg_file), output="/dev/null")
    args.serial, faux = FauxSerial.setup(args)
    return args, faux


def _make_infrastructure(tmp_path, mlg_bytes, *, disconnected=True, **extra_args):
    """Stand up FauxSerial, FauxDockServer, RealSerial, and RUDICS.

    Returns (tty, rudics, ds) so callers can inspect and clean up.
    """
    # FauxDockServer on an ephemeral port
    ds_args = make_args(simDS=True)
    ds = FauxDockServer.FauxDS(ds_args)
    ds.start()

    # FauxSerial feeding the MLG data (thread started below, after the
    # consumer opens the slave)
    fs_args, faux = _make_faux_serial(tmp_path, mlg_bytes)

    # Merge everything into one args namespace
    merged = make_args(
        serial=fs_args.serial,
        input=fs_args.input,
        output="/dev/null",
        host=ds.host,
        port=ds.port,
        disconnected=disconnected,
        rudicsSpacing=0.1,
        rudicsDelay=0.5,
        connectTimeout=2,
        **extra_args,
    )

    tty = RealSerial(merged)
    if faux is not None:
        faux.start() # Safe now: RealSerial holds the slave, so no hangup
    rudics = RUDICS(merged)
    return tty, rudics, ds


def _stop_doit_thread(t, tty, rudics, timeout=5.0):
    """Gracefully stop a doit() thread by closing its I/O objects.

    qWantOpen is sticky intent in production -- only triggerOff lowers it --
    so an external close() alone won't make the main loop exit. Tests
    pre-clear intent before closing to signal shutdown.
    """
    tty.close()
    rudics.qWantOpen = False  # Signal shutdown so the main loop terminates
    rudics.close()
    t.join(timeout=timeout)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_full_dive_surface_cycle(tmp_path):
    """TriggerOn fires on surface, RUDICS connects, then triggerOff closes."""
    # Data flows fast through PTY; both triggerOn and triggerOff may fire
    # within a single batch.  We verify the cycle completed by checking
    # tLastClose (evidence that a connection was made then closed).
    mlg = (
        b"surface_0:2024/01/01 00:00:00 Waiting for final gps fix\r\n"
        b"sensor: depth=100 temp=12.3\r\n"
        b"surface_0:2024/01/01 01:00:00 Picking iridium or freewave\r\n"
        b"iridium: data transfer complete\r\n"
        b"surface_0:2024/01/01 02:00:00 Waiting for final gps fix\r\n"
    )

    tty, rudics, _ds = _make_infrastructure(tmp_path, mlg, disconnected=True)
    t = threading.Thread(target=doit, args=(tty, rudics), daemon=True)
    t.start()
    try:
        # Wait for the full cycle: triggerOn -> connect -> triggerOff -> close.
        # Data flows very fast, so check tLastClose as evidence that a
        # RUDICS connection was opened and then closed.
        cycle_done = _poll(lambda: rudics.tLastClose > 0, timeout=5.0)
        assert cycle_done, "RUDICS connection was never opened then closed"

        # After the triggerOff, qWantOpen should be False
        assert rudics.qWantOpen is False
    finally:
        _stop_doit_thread(t, tty, rudics)


@pytest.mark.integration
def test_abort_triggers_connection(tmp_path):
    """An 'abort_the_mission' line triggers triggerOn."""
    # The abort line has no subsequent triggerOff, so qWantOpen stays True
    # and the RUDICS socket remains connected (or was connected then idled out).
    mlg = (
        b"sensor: depth=10 temp=12.0\r\n"
        b"mission_0: abort_the_mission\r\n"
        b"sensor: depth=20 temp=11.5\r\n"
    )

    tty, rudics, _ds = _make_infrastructure(tmp_path, mlg, disconnected=True)
    t = threading.Thread(target=doit, args=(tty, rudics), daemon=True)
    t.start()
    try:
        # The abort trigger should cause a connection attempt.  Check that
        # either the socket is open or evidence of a connection exists.
        triggered = _poll(
            lambda: rudics.s is not None or rudics.tLastClose > 0 or rudics.qWantOpen,
            timeout=5.0,
        )
        assert triggered, "abort_the_mission did not trigger connection"
    finally:
        _stop_doit_thread(t, tty, rudics)


@pytest.mark.integration
def test_reconnect_after_server_drop():
    """With recent serial activity, a server drop keeps qWantOpen True for redial."""
    ds_args = make_args(simDS=True)
    ds = FauxDockServer.FauxDS(ds_args)
    ds.start()

    args = make_args(
        host=ds.host,
        port=ds.port,
        disconnected=False,
        rudicsSpacing=0.1,
        reconnectMaxSerialIdle=600,
    )
    rudics = RUDICS(args)
    rudics.open()
    assert rudics.s is not None, "Failed to connect to FauxDS"

    # Simulate recent serial activity so the idle-threshold doesn't trip.
    rudics.tLastSerialAction = time.monotonic()
    assert rudics.qWantOpen is True
    rudics.close()
    assert rudics.s is None
    assert rudics.qWantOpen is True


@pytest.mark.integration
def test_idle_timeout_disconnects():
    """Idle timeout closes the socket when no activity."""
    a, b = socket.socketpair()
    try:
        args = make_args(idleTimeout=2, rudicsMaxOpenTime=86400)
        rudics = RUDICS(args)
        rudics.s = a
        now = time.monotonic()
        rudics.tLastOpen = now - 3  # Opened 3 seconds ago
        rudics.tLastAction = now - 3  # Last activity 3 seconds ago, timeout is 2

        rudics.timedOut()

        assert rudics.s is None, "Socket should have been closed on idle timeout"
        # Intent survives: only triggerOff ends a surfacing. A quiet glider is
        # redialed more slowly instead of being abandoned.
        assert rudics.qWantOpen is True
        assert rudics.reconnectBackoff > rudics.args.rudicsSpacing
    finally:
        b.close()


@pytest.mark.integration
def test_binary_log_output(tmp_path):
    """Binary log file captures SERIAL markers when data flows."""
    mlg = (
        b"surface_0:2024/01/01 01:00:00 Picking iridium or freewave\r\n"
        b"iridium: some data payload here\r\n"
    )

    bin_file = tmp_path / "trace.bin"

    # Use disconnected=False so qWantOpen starts True and data is buffered.
    tty, rudics, _ds = _make_infrastructure(
        tmp_path, mlg, disconnected=False, idleTimeout=2,
    )
    t = threading.Thread(
        target=doit, args=(tty, rudics), kwargs={"binary": str(bin_file)},
        daemon=True,
    )
    t.start()
    try:
        # Give data time to flow through the serial -> doit -> binary file.
        time.sleep(1.0)
    finally:
        # Close tty to force doit to exit, then join the thread so doit's
        # finally block flushes and closes the binary file.
        _stop_doit_thread(t, tty, rudics)

    data = bin_file.read_bytes() if bin_file.exists() else b""
    assert b"SERIAL" in data, (
        f"Expected 'SERIAL' marker in binary log, got {len(data)} bytes"
    )


@pytest.mark.integration
def test_max_open_time_forces_disconnect():
    """rudicsMaxOpenTime closes socket and sets tNextOpen in the future."""
    a, b = socket.socketpair()
    try:
        args = make_args(
            rudicsMaxOpenTime=2,
            rudicsMaxOpenTimeDelay=1,
            idleTimeout=3600,
        )
        rudics = RUDICS(args)
        rudics.s = a
        rudics.qWantOpen = True
        now = time.monotonic()
        rudics.tLastOpen = now - 3  # Open for 3s, max is 2s
        rudics.tLastAction = now    # Recent activity so idle timeout won't fire

        rudics.timedOut()

        assert rudics.s is None, "Socket should have been closed on max open time"
        assert rudics.qWantOpen is True, "Should want to reconnect after max open"
        assert rudics.tNextOpen >= now, "tNextOpen should be set in the future"
    finally:
        b.close()


@pytest.mark.integration
def test_multiple_surface_cycles(tmp_path):
    """Two dive/surface cycles produce at least two connect transitions.

    Data flows fast through the PTY, so both cycles may complete before
    polling can observe them.  We instrument RUDICS.open to count how
    many times a successful connection was established.
    """
    cycle0 = _build_surface_cycle(0)
    cycle1 = _build_surface_cycle(1)
    mlg = (cycle0 + cycle1).encode()

    tty, rudics, _ds = _make_infrastructure(
        tmp_path, mlg, disconnected=True,
        idleTimeout=10,
    )

    # Instrument RUDICS.open to count successful connections
    connect_count = [0]
    original_open = rudics.open.__func__

    def counting_open(self):
        original_open(self)
        if self.s is not None:
            connect_count[0] += 1

    rudics.open = types.MethodType(counting_open, rudics)

    t = threading.Thread(target=doit, args=(tty, rudics), daemon=True)
    t.start()
    try:
        # Wait for data to be processed.  Both cycles happen very fast.
        _poll(lambda: connect_count[0] >= 2, timeout=8.0)

        assert connect_count[0] >= 2, (
            f"Expected at least 2 connect transitions, saw {connect_count[0]}"
        )
    finally:
        _stop_doit_thread(t, tty, rudics)
