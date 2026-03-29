import argparse
import os
import sys
import pytest

# Ensure the project root is on sys.path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import FauxDockServer


def build_synthetic_mlg(*, include_abort: bool = False, cycles: int = 2) -> bytes:
    """Build synthetic MLG data with dive/surface cycles."""
    lines: list[str] = []
    lines.append("the8x3_filename")
    lines.append("full_filename")
    lines.append("LOG FILE OPENED")
    lines.append("")

    for i in range(cycles):
        # Surface → triggerOff (glider going down)
        lines.append(f"surface_{i}:2024/01/01 00:00:{i:02d} Waiting for final gps fix")
        lines.append(f"sensor: depth={i * 100 + 10} temp=12.3 sal=34.5")
        lines.append(f"sensor: depth={i * 100 + 50} temp=11.8 sal=34.6")

        if include_abort and i == 0:
            lines.append(f"mission_{i}: abort_the_mission")

        # Surface → triggerOn (glider coming up)
        lines.append(f"surface_{i}:2024/01/01 01:00:{i:02d} Picking iridium or freewave")
        lines.append("iridium: connected to dockserver")
        lines.append("iridium: data transfer complete")

    lines.append("")
    lines.append("LOG FILE CLOSED")
    return ("\r\n".join(lines) + "\r\n").encode()


@pytest.fixture
def synthetic_mlg_bytes() -> bytes:
    return build_synthetic_mlg()


@pytest.fixture
def synthetic_abort_bytes() -> bytes:
    return build_synthetic_mlg(include_abort=True, cycles=1)


@pytest.fixture
def tmp_mlg_file(tmp_path, synthetic_mlg_bytes):
    p = tmp_path / "test.mlg"
    p.write_bytes(synthetic_mlg_bytes)
    return str(p)


@pytest.fixture
def tmp_abort_file(tmp_path, synthetic_abort_bytes):
    p = tmp_path / "abort.mlg"
    p.write_bytes(synthetic_abort_bytes)
    return str(p)


def make_args(**overrides) -> argparse.Namespace:
    """Create an argparse.Namespace with test defaults."""
    defaults = {
        # Serial
        "serial": None,
        "input": None,
        "output": "/dev/null",
        "baudrate": 115200,
        "parity": "N",
        "bytesize": 8,
        "stopbits": 1,
        # RUDICS
        "host": "127.0.0.1",
        "port": 6565,
        "triggerOn": None,
        "triggerOff": None,
        "idleTimeout": 5,
        "rudicsSpacing": 0.1,
        "rudicsBaudrate": None,
        "rudicsDelay": 1,
        "rudicsMaxOpenTime": 60,
        "rudicsMaxOpenTimeDelay": 1,
        "connectTimeout": 2,
        "disconnected": False,
        # Dockserver
        "simDS": False,
        "dsInput": None,
        "dsOutput": "/dev/null",
        # Logger
        "logfile": None,
        "logBytes": 10000000,
        "logCount": 3,
        "verbose": False,
        # Binary
        "binary": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def default_args():
    return make_args()


@pytest.fixture
def faux_dockserver():
    """Start a FauxDS on an ephemeral port and yield it."""
    args = make_args(simDS=True)
    ds = FauxDockServer.FauxDS(args)
    ds.start()
    yield ds
    # Daemon thread — will be cleaned up on process exit


@pytest.fixture
def rudics_args(faux_dockserver):
    """Args configured to connect to the faux dockserver."""
    return make_args(
        host=faux_dockserver.host,
        port=faux_dockserver.port,
    )
