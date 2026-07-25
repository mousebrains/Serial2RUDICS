import os
import re
import sys
from argparse import Namespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from install import barebones, expand_units, substitute_template, validate_args

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# An unsubstituted marker: @, then all-caps. Does not match a real ssh
# destination (tunnel@proxy.edu) or an email address in a comment.
MARKER = re.compile(r"@[A-Z][A-Z0-9_]*@")


def _install_args(**overrides):  # type: ignore[no-untyped-def]
    defaults = {
        "username": "testuser", "group": "dialout",
        "directory": "/home/testuser/logs",
        "executable": "serial2RUDICS.py",
        "hostname": "example.host.edu", "port": 6565,
        "baudrate": 115200, "timeout": 3600, "restartSeconds": 60,
        "memoryMax": "128M",
        "tunnelProxy": None, "tunnelLocalPort": 16565,
        "tunnelKey": "/home/testuser/.ssh/id_rudics",
        "tunnelService": "rudics-tunnel.service", "tunnelRestartSeconds": 5,
        "ssh": "/usr/bin/ssh",
    }
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


def test_validate_args_rejects_empty_memoryMax():
    with pytest.raises(SystemExit, match="--memoryMax"):
        validate_args(_install_args(memoryMax=""))


def test_substitute_template_includes_memoryMax():
    """@MEMORYMAX@ marker is substituted with the configured value."""
    template = "MemoryMax=@MEMORYMAX@"
    content = substitute_template(template, _install_args(memoryMax="96M"), "/opt/bin")
    assert content == "MemoryMax=96M"


def test_expand_units_expands_templates_per_device():
    """A foo@.service template yields one instance per device."""
    units = expand_units(["USBToRUDICS@.service"], ["ttyUSB0", "ttyUSB1"])
    assert units == ["USBToRUDICS@ttyUSB0.service", "USBToRUDICS@ttyUSB1.service"]


def test_expand_units_enables_plain_unit_once():
    """A non-template unit is enabled once, not once per device."""
    units = expand_units(["rudics-tunnel.service"], ["ttyUSB0", "ttyUSB1", "ttyUSB2"])
    assert units == ["rudics-tunnel.service"]


def test_expand_units_mixed():
    """Template and plain units can be installed together."""
    units = expand_units(["USBToRUDICS@.service", "rudics-tunnel.service"], ["ttyUSB0"])
    assert units == ["USBToRUDICS@ttyUSB0.service", "rudics-tunnel.service"]


def test_no_tunnel_connects_to_dockserver_directly():
    """Without --tunnelProxy the port service dials the dockserver itself."""
    template = "--host=@HOSTNAME@ --port=@PORT@\n@TUNNELDEPS@"
    content = substitute_template(template, _install_args(), "/opt/bin")
    assert "--host=example.host.edu --port=6565" in content
    assert "rudics-tunnel" not in content


def test_tunnel_points_port_service_at_local_forward():
    """With --tunnelProxy the port service dials the local end of the tunnel."""
    template = "--host=@HOSTNAME@ --port=@PORT@\n@TUNNELDEPS@"
    args = _install_args(tunnelProxy="tunnel@proxy.example.edu", tunnelLocalPort=16565)
    content = substitute_template(template, args, "/opt/bin")
    assert "--host=127.0.0.1 --port=16565" in content
    assert "Wants=rudics-tunnel.service" in content
    assert "After=rudics-tunnel.service" in content


def test_tunnel_markers_carry_the_dockserver():
    """@REMOTEHOST@/@REMOTEPORT@ stay the dockserver so ssh forwards there."""
    template = "-L 127.0.0.1:@LOCALPORT@:@REMOTEHOST@:@REMOTEPORT@ @PROXY@"
    args = _install_args(tunnelProxy="tunnel@proxy.example.edu")
    content = substitute_template(template, args, "/opt/bin")
    assert content == "-L 127.0.0.1:16565:example.host.edu:6565 tunnel@proxy.example.edu"


def test_real_templates_have_no_leftover_markers():
    """Every @MARKER@ in the shipped unit files is substituted, tunnel or not."""
    args = _install_args(tunnelProxy="tunnel@proxy.example.edu")
    for service in ("USBToRUDICS@.service", "rudics-tunnel.service"):
        with open(os.path.join(ROOT, service)) as fp:
            content = substitute_template(fp.read(), args, ROOT)
        assert not MARKER.search(content), f"{service}: {MARKER.search(content)}"

    args = _install_args()  # No tunnel: same templates must still fill in
    with open(os.path.join(ROOT, "USBToRUDICS@.service")) as fp:
        content = substitute_template(fp.read(), args, ROOT)
    assert not MARKER.search(content)


def test_tunnel_service_forwards_to_dockserver():
    """The shipped tunnel unit binds the forward to localhost only."""
    args = _install_args(tunnelProxy="tunnel@proxy.example.edu", tunnelLocalPort=17000)
    with open(os.path.join(ROOT, "rudics-tunnel.service")) as fp:
        content = substitute_template(fp.read(), args, ROOT)
    assert "-L 127.0.0.1:17000:example.host.edu:6565" in content
    assert "tunnel@proxy.example.edu" in content
    assert "RestartSec=5" in content


def test_validate_args_rejects_empty_tunnelProxy():
    with pytest.raises(SystemExit, match="--tunnelProxy"):
        validate_args(_install_args(tunnelProxy="   "))


def test_validate_args_rejects_bad_tunnelLocalPort():
    with pytest.raises(SystemExit, match="--tunnelLocalPort"):
        validate_args(_install_args(tunnelProxy="proxy.edu", tunnelLocalPort=0))


def test_validate_args_rejects_bad_tunnelRestartSeconds():
    with pytest.raises(SystemExit, match="--tunnelRestartSeconds"):
        validate_args(_install_args(tunnelProxy="proxy.edu", tunnelRestartSeconds=0))


def test_validate_args_ignores_tunnel_settings_without_proxy():
    """Tunnel values are only checked when a tunnel is actually requested."""
    validate_args(_install_args(tunnelLocalPort=0, tunnelRestartSeconds=0))
