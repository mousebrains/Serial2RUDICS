import fcntl
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import serial
import serial.serialutil

from RealSerial import WRITE_CHUNK_BYTES, RealSerial
from tests.conftest import make_args


def _make_serial_args(**overrides):
    """Return args with serial=/dev/ttyUSB0 plus any overrides."""
    defaults = {"serial": "/dev/ttyUSB0"}
    defaults.update(overrides)
    return make_args(**defaults)


# ── Init ─────────────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_init_opens_port_with_correct_args(mock_serial_cls, mock_fcntl):
    args = _make_serial_args(baudrate=9600, parity="E", bytesize=7, stopbits=2)
    rs = RealSerial(args)

    mock_serial_cls.assert_called_once_with(
        port="/dev/ttyUSB0",
        baudrate=9600,
        bytesize=7,
        parity="E",
        stopbits=2,
    )
    assert rs.fp is mock_serial_cls.return_value


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_init_sets_nonblocking(mock_serial_cls, mock_fcntl):
    """__open should set O_NONBLOCK on the serial fd so writes never block."""
    mock_fp = mock_serial_cls.return_value
    mock_fp.fileno.return_value = 5
    mock_fcntl.return_value = 0  # initial flags

    RealSerial(_make_serial_args())

    # F_GETFL then F_SETFL with O_NONBLOCK ORed in
    assert mock_fcntl.call_count == 2
    get_call, set_call = mock_fcntl.call_args_list
    assert get_call.args == (5, fcntl.F_GETFL)
    assert set_call.args == (5, fcntl.F_SETFL, 0 | os.O_NONBLOCK)


@patch("serial.Serial", side_effect=serial.serialutil.SerialException("boom"))
def test_init_handles_serial_exception(mock_serial_cls):
    """SerialException during open should not propagate; fp stays None."""
    args = _make_serial_args()
    rs = RealSerial(args)

    assert rs.fp is None


# ── __bool__ ─────────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_bool_true_with_open_port(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    assert rs.fp is not None
    assert bool(rs) is True


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_bool_true_with_buffer(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    rs.buffer = bytearray(b"data")
    assert bool(rs) is True


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_bool_false_when_no_port_and_no_buffer(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    rs.buffer = bytearray()
    assert bool(rs) is False


# ── put() ────────────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_put_accumulates_buffer(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    rs.put(b"abc")
    rs.put(b"def")
    assert bytes(rs.buffer) == b"abcdef"


# ── send() ───────────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("os.write")
@patch("serial.Serial")
def test_send_writes_chunk_and_advances(mock_serial_cls, mock_os_write, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    mock_fp.fileno.return_value = 7
    mock_os_write.return_value = 3

    rs = RealSerial(_make_serial_args())
    rs.put(b"XYZ")
    rs.send()

    mock_os_write.assert_called_once_with(7, b"XYZ")
    assert bytes(rs.buffer) == b""


@patch("fcntl.fcntl")
@patch("os.write")
@patch("serial.Serial")
def test_send_caps_at_chunk_size(mock_serial_cls, mock_os_write, mock_fcntl):
    """send() should write at most WRITE_CHUNK_BYTES per call."""
    mock_fp = mock_serial_cls.return_value
    mock_fp.fileno.return_value = 7
    mock_os_write.return_value = WRITE_CHUNK_BYTES

    rs = RealSerial(_make_serial_args())
    rs.put(b"A" * (WRITE_CHUNK_BYTES + 100))
    rs.send()

    (fd, payload), _ = mock_os_write.call_args
    assert fd == 7
    assert len(payload) == WRITE_CHUNK_BYTES
    assert len(rs.buffer) == 100


@patch("fcntl.fcntl")
@patch("os.write")
@patch("serial.Serial")
def test_send_handles_blocking_io_error(mock_serial_cls, mock_os_write, mock_fcntl):
    """BlockingIOError (EAGAIN) should leave the buffer untouched."""
    mock_fp = mock_serial_cls.return_value
    mock_fp.fileno.return_value = 7
    mock_os_write.side_effect = BlockingIOError()

    rs = RealSerial(_make_serial_args())
    rs.put(b"ABC")
    rs.send()
    assert bytes(rs.buffer) == b"ABC"
    assert rs.fp is mock_fp  # Port stays open on EAGAIN


@patch("fcntl.fcntl")
@patch("os.write")
@patch("serial.Serial")
def test_send_closes_port_on_oserror(mock_serial_cls, mock_os_write, mock_fcntl):
    """Generic OSError on write should close the port."""
    mock_fp = mock_serial_cls.return_value
    mock_fp.fileno.return_value = 7
    mock_os_write.side_effect = OSError("write failed")

    rs = RealSerial(_make_serial_args())
    rs.put(b"ABC")
    rs.send()
    assert rs.fp is None


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_send_noop_when_no_port(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    rs.put(b"data")
    rs.send()  # should not raise


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_send_noop_when_buffer_empty(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    rs.send()  # buffer is empty


@patch("fcntl.fcntl")
@patch("os.write")
@patch("serial.Serial")
def test_send_zero_write_does_not_consume_buffer(mock_serial_cls, mock_os_write, mock_fcntl):
    """If os.write returns 0, buffer should not advance."""
    mock_fp = mock_serial_cls.return_value
    mock_fp.fileno.return_value = 7
    mock_os_write.return_value = 0

    rs = RealSerial(_make_serial_args())
    rs.put(b"ABC")
    rs.send()
    assert bytes(rs.buffer) == b"ABC"  # Unchanged


# ── get() ────────────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_get_returns_data(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    mock_fp.read.return_value = b"hello"

    rs = RealSerial(_make_serial_args())
    result = rs.get(5)

    mock_fp.read.assert_called_once_with(5)
    assert result == b"hello"


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_get_eof_closes_port(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    mock_fp.read.return_value = b""

    rs = RealSerial(_make_serial_args())
    result = rs.get(10)

    assert result == b""
    mock_fp.close.assert_called_once()
    assert rs.fp is None


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_get_handles_serial_exception(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    mock_fp.read.side_effect = serial.serialutil.SerialException("read error")

    rs = RealSerial(_make_serial_args())
    result = rs.get(5)

    assert result == b""
    mock_fp.close.assert_called_once()
    assert rs.fp is None


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_get_with_n_le_zero_returns_empty(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    assert rs.get(0) == b""
    assert rs.get(-1) == b""
    mock_serial_cls.return_value.read.assert_not_called()


# ── nAvailable() ─────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_nAvailable_returns_in_waiting(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    mock_fp.in_waiting = 42

    rs = RealSerial(_make_serial_args())
    assert rs.nAvailable() == 42


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_nAvailable_returns_zero_when_no_port(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    assert rs.nAvailable() == 0


# ── close() ──────────────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_close_closes_port_and_sets_fp_none(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    rs = RealSerial(_make_serial_args())

    assert rs.fp is not None
    rs.close()

    mock_fp.close.assert_called_once()
    assert rs.fp is None


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_close_idempotent(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    rs = RealSerial(_make_serial_args())

    rs.close()
    rs.close()  # second call should not raise

    mock_fp.close.assert_called_once()
    assert rs.fp is None


# ── outputFileno() ───────────────────────────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_outputFileno_returns_port_when_buffer_exists(mock_serial_cls, mock_fcntl):
    mock_fp = mock_serial_cls.return_value
    rs = RealSerial(_make_serial_args())
    rs.put(b"data")

    assert rs.outputFileno() is mock_fp


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_outputFileno_returns_none_when_buffer_empty(mock_serial_cls, mock_fcntl):
    rs = RealSerial(_make_serial_args())
    assert rs.outputFileno() is None


# ── close() drops undeliverable output ───────────────────────────────

@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_close_clears_pending_buffer(mock_serial_cls, mock_fcntl):
    """Bytes queued for a closed port can never be written, so drop them.

    Keeping them leaves __bool__ true with no way to drain, which wedges
    doit()'s `while serial or rudics` loop forever.
    """
    rs = RealSerial(_make_serial_args())
    rs.put(b"unsent glider command")
    assert bool(rs) is True

    rs.close()

    assert rs.buffer == bytearray()
    assert bool(rs) is False, "a closed, drained port must let doit() exit"


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_put_is_bounded_by_max_buffer(mock_serial_cls, mock_fcntl):
    """The dockserver->glider direction is bounded like the reverse path."""
    rs = RealSerial(_make_serial_args(maxBuffer=1024))
    rs.put(b"a" * 1024)
    rs.put(b"b" * 512)  # Over the limit, discarded

    assert len(rs.buffer) == 1024
    assert b"b" not in rs.buffer


@patch("fcntl.fcntl")
@patch("serial.Serial")
def test_put_after_close_does_not_rewedge(mock_serial_cls, mock_fcntl):
    """doit() calls put() for every dockserver byte without checking the port.

    Buffering those bytes would make __bool__ true again with no drain path,
    and close() cannot clear it a second time (it returns early on fp None),
    so the loop would wedge exactly as it did before close() learned to clear.
    """
    rs = RealSerial(_make_serial_args())
    rs.close()

    rs.put(b"data arriving from the dockserver after the port died")

    assert rs.buffer == bytearray()
    assert bool(rs) is False, "a closed port must stay falsy so doit() can exit"
