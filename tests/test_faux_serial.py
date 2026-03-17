import sys
import os
import select
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import FauxSerial
from tests.conftest import make_args


def test_pty_creation_yields_valid_device_path(tmp_path):
    """FauxSerial creates a PTY whose slave path exists on disk."""
    ifn = tmp_path / "input.bin"
    ifn.write_bytes(b"")  # empty file so EOF is immediate
    ofn = tmp_path / "output.bin"
    ofn.write_bytes(b"")

    args = make_args(input=str(ifn), output=str(ofn))
    fs = FauxSerial.FauxSerial(args)
    try:
        assert os.path.exists(fs.port), f"PTY slave path {fs.port} does not exist"
        assert fs.port.startswith("/dev/")
    finally:
        os.close(fs.master)


def test_data_flows_input_file_to_master(tmp_path):
    """Data written to the input file is readable from the slave (port) side."""
    payload = b"hello from input\n"
    ifn = tmp_path / "input.bin"
    ifn.write_bytes(payload)
    ofn = tmp_path / "output.bin"
    ofn.write_bytes(b"")

    args = make_args(input=str(ifn), output=str(ofn))
    fs = FauxSerial.FauxSerial(args)

    # Open the slave side before starting so the PTY link is active
    slave_fd = os.open(fs.port, os.O_RDWR | os.O_NOCTTY)
    tty.setraw(slave_fd)

    fs.start()

    collected = bytearray()
    deadline = time.monotonic() + 1.0
    try:
        while time.monotonic() < deadline and len(collected) < len(payload):
            ready, _, _ = select.select([slave_fd], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(slave_fd, 4096)
                if not chunk:
                    break
                collected += chunk
            except OSError:
                break
    finally:
        os.close(slave_fd)

    assert collected == payload


def test_data_flows_master_to_output_file(tmp_path):
    """Data written to the slave (port) side appears in the output file."""
    payload = b"hello from slave\n"
    ofn = tmp_path / "output.bin"
    ofn.write_bytes(b"")

    # Use a FIFO for input so the thread blocks waiting for data,
    # keeping the master fd alive while we write to it.
    fifo_path = str(tmp_path / "input.fifo")
    os.mkfifo(fifo_path)

    args = make_args(input=fifo_path, output=str(ofn))
    fs = FauxSerial.FauxSerial(args)

    # Open the slave side so the PTY link is active
    slave_fd = os.open(fs.port, os.O_RDWR | os.O_NOCTTY)
    tty.setraw(slave_fd)

    fs.start()

    # Open the FIFO for writing (this unblocks the thread's open() for reading)
    fifo_wr = os.open(fifo_path, os.O_WRONLY)
    try:
        # Give the thread a moment to enter its select loop
        time.sleep(0.2)

        # Write to the slave side; the thread reads it from the master
        # and writes it to the output file.
        os.write(slave_fd, payload)
        time.sleep(0.5)

        data = ofn.read_bytes()
        assert payload in data, (
            f"Expected {payload!r} in output file but got {data!r}"
        )
    finally:
        os.close(fifo_wr)
        os.close(slave_fd)


def test_setup_with_input_returns_pty_path(tmp_path):
    """setup() with --input creates a PTY and returns a /dev/ path."""
    ifn = tmp_path / "input.bin"
    ifn.write_bytes(b"")
    ofn = tmp_path / "output.bin"
    ofn.write_bytes(b"")

    args = make_args(input=str(ifn), output=str(ofn))
    # Reset global state so setup() creates a new FauxSerial
    FauxSerial.fauxSerial = None
    port = FauxSerial.setup(args)

    assert isinstance(port, str)
    assert port.startswith("/dev/"), f"Expected /dev/ path but got {port!r}"


def test_setup_with_serial_returns_device_directly():
    """setup() with --serial returns the serial device path unchanged."""
    device = "/dev/ttyUSB0"
    args = make_args(serial=device)
    FauxSerial.fauxSerial = None
    port = FauxSerial.setup(args)

    assert port == device


def test_graceful_shutdown_on_input_eof(tmp_path):
    """Thread stops after the input file is fully consumed (EOF)."""
    ifn = tmp_path / "input.bin"
    ifn.write_bytes(b"short\n")
    ofn = tmp_path / "output.bin"
    ofn.write_bytes(b"")

    args = make_args(input=str(ifn), output=str(ofn))
    fs = FauxSerial.FauxSerial(args)
    fs.start()

    # Drain what the thread writes to the master so it is not blocked
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.read(fs.master, 4096)
        except OSError:
            break
        time.sleep(0.05)

    # The thread should finish once input is exhausted and output is /dev/null
    fs.join(timeout=1.0)
    assert not fs.is_alive(), "FauxSerial thread did not stop after input EOF"
