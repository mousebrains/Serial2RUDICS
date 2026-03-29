import sys
import os
from argparse import Namespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from install import barebones, substitute_template, validate_args


def _install_args(**overrides):  # type: ignore[no-untyped-def]
    defaults = dict(
        username="testuser", group="dialout",
        directory="/home/testuser/logs",
        executable="serial2RUDICS.py",
        hostname="example.host.edu", port=6565,
        baudrate=115200, timeout=3600, restartSeconds=60,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def test_barebones_strips_comments():
    """Lines starting with # are removed."""
    content = "# this is a comment\nkeep this\n# another comment"
    result = barebones(content)
    assert result == ["keep this"]


def test_barebones_strips_empty_lines():
    """Empty and blank lines are removed."""
    content = "hello\n\n   \nworld\n\n"
    result = barebones(content)
    assert result == ["hello", "world"]


def test_barebones_strips_whitespace():
    """Leading and trailing whitespace is stripped from each line."""
    content = "  alpha  \n\tbeta\t\n  gamma  "
    result = barebones(content)
    assert result == ["alpha", "beta", "gamma"]


def test_barebones_preserves_content():
    """Non-comment, non-empty lines are preserved in order."""
    content = "first\nsecond\nthird"
    result = barebones(content)
    assert result == ["first", "second", "third"]


def test_substitute_template():
    """substitute_template replaces all @MARKER@ tokens."""
    template = (
        "User=@USERNAME@\n"
        "Group=@GROUPNAME@\n"
        "WorkingDirectory=@DIRECTORY@\n"
        "ExecStart=@EXECUTABLE@ --host @HOSTNAME@ --port @PORT@"
        " --baudrate @BAUDRATE@ --timeout @TIMEOUT@\n"
        "RestartSec=@RESTARTSECONDS@\n"
    )
    args = _install_args()
    content = substitute_template(template, args, "/opt/bin")

    assert "User=testuser" in content
    assert "Group=dialout" in content
    assert "WorkingDirectory=/home/testuser/logs" in content
    assert "--host example.host.edu --port 6565" in content
    assert "--baudrate 115200 --timeout 3600" in content
    assert "RestartSec=60" in content
    for marker in ("@USERNAME@", "@GROUPNAME@", "@DIRECTORY@",
                    "@HOSTNAME@", "@PORT@", "@BAUDRATE@",
                    "@TIMEOUT@", "@RESTARTSECONDS@"):
        assert marker not in content


def test_substitute_template_executable_uses_root():
    """@EXECUTABLE@ is joined with the root directory."""
    content = substitute_template("@EXECUTABLE@", _install_args(), "/srv/app")
    assert content == "/srv/app/serial2RUDICS.py"


def test_validate_args_accepts_valid():
    validate_args(_install_args())


def test_validate_args_rejects_port_zero():
    with pytest.raises(SystemExit, match="--port"):
        validate_args(_install_args(port=0))


def test_validate_args_rejects_port_too_high():
    with pytest.raises(SystemExit, match="--port"):
        validate_args(_install_args(port=70000))


def test_validate_args_rejects_negative_timeout():
    with pytest.raises(SystemExit, match="--timeout"):
        validate_args(_install_args(timeout=0))


def test_validate_args_rejects_negative_restart():
    with pytest.raises(SystemExit, match="--restartSeconds"):
        validate_args(_install_args(restartSeconds=-1))
