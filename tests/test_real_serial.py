import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

import serial
import serial.serialutil

from RealSerial import RealSerial
from tests.conftest import make_args


def _make_serial_args(**overrides):
    """Return args with serial=/dev/ttyUSB0 plus any overrides."""
    defaults = dict(serial="/dev/ttyUSB0")
    defaults.update(overrides)
    return make_args(**defaults)


# ── Init ─────────────────────────────────────────────────────────────

@patch("serial.Serial")
def test_init_opens_port_with_correct_args(mock_serial_cls):
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


@patch("serial.Serial", side_effect=serial.serialutil.SerialException("boom"))
def test_init_handles_serial_exception(mock_serial_cls):
    """SerialException during open should not propagate; fp stays None."""
    args = _make_serial_args()
    rs = RealSerial(args)

    assert rs.fp is None


# ── __bool__ ─────────────────────────────────────────────────────────

@patch("serial.Serial")
def test_bool_true_with_open_port(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    assert rs.fp is not None
    assert bool(rs) is True


@patch("serial.Serial")
def test_bool_true_with_buffer(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    rs.buffer = bytearray(b"data")
    assert bool(rs) is True


@patch("serial.Serial")
def test_bool_false_when_no_port_and_no_buffer(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    rs.buffer = bytearray()
    assert bool(rs) is False


# ── put() ────────────────────────────────────────────────────────────

@patch("serial.Serial")
def test_put_accumulates_buffer(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    rs.put(b"abc")
    rs.put(b"def")
    assert bytes(rs.buffer) == b"abcdef"


# ── send() ───────────────────────────────────────────────────────────

@patch("serial.Serial")
def test_send_writes_one_byte_and_advances(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    mock_fp.write.return_value = 1

    rs = RealSerial(_make_serial_args())
    rs.put(b"XYZ")
    rs.send()

    mock_fp.write.assert_called_once_with(bytearray(b"X"))
    assert bytes(rs.buffer) == b"YZ"


@patch("serial.Serial")
def test_send_noop_when_no_port(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    rs.put(b"data")
    rs.send()  # should not raise
    mock_serial_cls.return_value.write.assert_not_called()


@patch("serial.Serial")
def test_send_noop_when_buffer_empty(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    rs.send()  # buffer is empty
    mock_serial_cls.return_value.write.assert_not_called()


@patch("serial.Serial")
def test_send_zero_write_does_not_consume_buffer(mock_serial_cls):
    """If write() returns 0, buffer should not advance."""
    mock_fp = mock_serial_cls.return_value
    mock_fp.write.return_value = 0

    rs = RealSerial(_make_serial_args())
    rs.put(b"ABC")
    rs.send()
    assert bytes(rs.buffer) == b"ABC"  # Unchanged


# ── get() ────────────────────────────────────────────────────────────

@patch("serial.Serial")
def test_get_returns_data(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    mock_fp.read.return_value = b"hello"

    rs = RealSerial(_make_serial_args())
    result = rs.get(5)

    mock_fp.read.assert_called_once_with(5)
    assert result == b"hello"


@patch("serial.Serial")
def test_get_eof_closes_port(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    mock_fp.read.return_value = b""

    rs = RealSerial(_make_serial_args())
    result = rs.get(10)

    assert result == b""
    mock_fp.close.assert_called_once()
    assert rs.fp is None


@patch("serial.Serial")
def test_get_handles_serial_exception(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    mock_fp.read.side_effect = serial.serialutil.SerialException("read error")

    rs = RealSerial(_make_serial_args())
    result = rs.get(5)

    assert result == b""
    mock_fp.close.assert_called_once()
    assert rs.fp is None


@patch("serial.Serial")
def test_get_with_n_le_zero_returns_empty(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    assert rs.get(0) == b""
    assert rs.get(-1) == b""
    mock_serial_cls.return_value.read.assert_not_called()


# ── nAvailable() ─────────────────────────────────────────────────────

@patch("serial.Serial")
def test_nAvailable_returns_in_waiting(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    mock_fp.in_waiting = 42

    rs = RealSerial(_make_serial_args())
    assert rs.nAvailable() == 42


@patch("serial.Serial")
def test_nAvailable_returns_zero_when_no_port(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    rs.fp = None
    assert rs.nAvailable() == 0


# ── close() ──────────────────────────────────────────────────────────

@patch("serial.Serial")
def test_close_closes_port_and_sets_fp_none(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    rs = RealSerial(_make_serial_args())

    assert rs.fp is not None
    rs.close()

    mock_fp.close.assert_called_once()
    assert rs.fp is None


@patch("serial.Serial")
def test_close_idempotent(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    rs = RealSerial(_make_serial_args())

    rs.close()
    rs.close()  # second call should not raise

    mock_fp.close.assert_called_once()
    assert rs.fp is None


# ── outputFileno() ───────────────────────────────────────────────────

@patch("serial.Serial")
def test_outputFileno_returns_port_when_buffer_exists(mock_serial_cls):
    mock_fp = mock_serial_cls.return_value
    rs = RealSerial(_make_serial_args())
    rs.put(b"data")

    assert rs.outputFileno() is mock_fp


@patch("serial.Serial")
def test_outputFileno_returns_none_when_buffer_empty(mock_serial_cls):
    rs = RealSerial(_make_serial_args())
    assert rs.outputFileno() is None
