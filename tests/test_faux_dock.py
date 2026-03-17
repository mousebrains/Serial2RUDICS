import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import socket
import time
import FauxDockServer
from tests.conftest import make_args


def _make_ds(tmp_path, *, ds_input=True):
    """Create a FauxDS with tmp_path-based input/output files.

    Returns (ds, input_path, output_path).  If ds_input is False the
    input file is not created and dsInput is left as None.
    """
    out_file = tmp_path / "ds_output.bin"
    out_file.touch()

    in_file = None
    if ds_input:
        in_file = tmp_path / "ds_input.fifo"
        os.mkfifo(str(in_file))

    args = make_args(
        simDS=True,
        dsInput=str(in_file) if in_file else None,
        dsOutput=str(out_file),
    )
    ds = FauxDockServer.FauxDS(args)
    ds.start()
    return ds, in_file, out_file


def _connect(ds, timeout=2.0):
    """Open a TCP connection to the running FauxDS."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((ds.host, ds.port))
    return s


# ---------- tests ----------


def test_ephemeral_port(tmp_path):
    """FauxDS should bind to an ephemeral port (> 0)."""
    ds, _, _ = _make_ds(tmp_path, ds_input=False)
    assert ds.port > 0


def test_accepts_connection(tmp_path):
    """A client should be able to connect without error."""
    ds, _, _ = _make_ds(tmp_path, ds_input=False)
    s = _connect(ds)
    s.close()


def test_client_to_output_file(tmp_path):
    """Data sent by the client should appear in the output file."""
    ds, _, out_file = _make_ds(tmp_path, ds_input=False)
    s = _connect(ds)
    payload = b"hello from client"
    s.sendall(payload)
    s.close()
    # Give the server time to flush
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        data = out_file.read_bytes()
        if data == payload:
            break
        time.sleep(0.05)
    assert out_file.read_bytes() == payload


def test_input_file_to_client(tmp_path):
    """Data written to the input file should arrive on the client socket."""
    ds, in_file, _ = _make_ds(tmp_path)
    s = _connect(ds)

    # Give the server a moment to accept and start its select loop
    time.sleep(0.15)

    payload = b"hello from file"
    # Write to the FIFO (this will block until the server reads it, but
    # the write is small so it completes quickly in a helper thread or
    # because the server is already selecting on the fifo).
    with open(str(in_file), "wb") as f:
        f.write(payload)

    # Read from the socket until we get the full payload or timeout
    s.settimeout(3.0)
    received = bytearray()
    deadline = time.monotonic() + 3.0
    while len(received) < len(payload) and time.monotonic() < deadline:
        try:
            chunk = s.recv(1024)
            if not chunk:
                break
            received += chunk
        except socket.timeout:
            break
    s.close()
    assert bytes(received) == payload


def test_multiple_sequential_connections(tmp_path):
    """FauxDS should accept a second connection after the first closes."""
    ds, _, out_file = _make_ds(tmp_path, ds_input=False)

    # First connection
    s1 = _connect(ds)
    s1.sendall(b"one")
    s1.close()

    # Wait for first connection data to flush
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if out_file.read_bytes() == b"one":
            break
        time.sleep(0.05)
    assert out_file.read_bytes() == b"one"

    # Give the server time to detect the close and loop back to accept()
    time.sleep(0.3)

    # Second connection -- server re-opens output in "wb" mode so it
    # overwrites the previous content.
    s2 = _connect(ds)
    s2.sendall(b"two")
    s2.close()

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        data = out_file.read_bytes()
        if data == b"two":
            break
        time.sleep(0.05)
    assert out_file.read_bytes() == b"two"


def test_setup_simDS(tmp_path):
    """setup() with --simDS should populate host and port in args."""
    out_file = tmp_path / "ds_out.bin"
    out_file.touch()
    args = make_args(
        host=None,
        simDS=True,
        dsInput=None,
        dsOutput=str(out_file),
    )
    result = FauxDockServer.setup(args)
    assert result.host == "127.0.0.1"
    assert isinstance(result.port, int)
    assert result.port > 0


def test_setup_host_passthrough():
    """setup() with --host should pass through unchanged; no FauxDS created."""
    args = make_args(host="example.com", simDS=False)
    original_port = args.port
    result = FauxDockServer.setup(args)
    assert result.host == "example.com"
    assert result.port == original_port
