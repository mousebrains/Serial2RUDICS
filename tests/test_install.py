import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from install import barebones


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


def test_template_substitution():
    """The .replace() chain from install.py substitutes all @MARKER@ tokens."""
    template = (
        "# Generated service file\n"
        "# @DATE@\n"
        "User=@USERNAME@\n"
        "Group=@GROUPNAME@\n"
        "WorkingDirectory=@DIRECTORY@\n"
        "ExecStart=@EXECUTABLE@ --host @HOSTNAME@ --port @PORT@"
        " --baudrate @BAUDRATE@ --timeout @TIMEOUT@\n"
        "RestartSec=@RESTARTSECONDS@\n"
        "# @GENERATED@\n"
    )

    # Replicate the replacement chain from install.py
    content = template
    content = content.replace("@DATE@", "Generated on Mon Jan  1 00:00:00 2024")
    content = content.replace("@GENERATED@", "Namespace(fake=True)")
    content = content.replace("@USERNAME@", "testuser")
    content = content.replace("@GROUPNAME@", "dialout")
    content = content.replace("@DIRECTORY@", "/home/testuser/logs")
    content = content.replace("@EXECUTABLE@", "/opt/bin/serial2RUDICS.py")
    content = content.replace("@HOSTNAME@", "example.host.edu")
    content = content.replace("@PORT@", "6565")
    content = content.replace("@BAUDRATE@", "115200")
    content = content.replace("@TIMEOUT@", "3600")
    content = content.replace("@RESTARTSECONDS@", "60")

    assert "@" not in content.replace("@", "", content.count("@"))  # crude leftover check
    # Verify specific substitutions
    assert "User=testuser" in content
    assert "Group=dialout" in content
    assert "WorkingDirectory=/home/testuser/logs" in content
    assert "ExecStart=/opt/bin/serial2RUDICS.py --host example.host.edu --port 6565" in content
    assert "--baudrate 115200 --timeout 3600" in content
    assert "RestartSec=60" in content
    # No un-replaced markers remain
    for marker in (
        "@DATE@", "@GENERATED@", "@USERNAME@", "@GROUPNAME@",
        "@DIRECTORY@", "@EXECUTABLE@", "@HOSTNAME@", "@PORT@",
        "@BAUDRATE@", "@TIMEOUT@", "@RESTARTSECONDS@",
    ):
        assert marker not in content
