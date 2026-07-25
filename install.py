#! /usr/bin/env python3
#
# Install a service for acting as a RUDICS connection to an SFMC server
#
# Jan-2023, Pat Welch, pat@mousebrains.com

import getpass
import os
import subprocess
import time
from argparse import ArgumentParser, Namespace
from tempfile import NamedTemporaryFile


def barebones(content: str) -> list[str]:
    lines = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or line[0] == "#":
            continue
        lines.append(line)
    return lines

def expand_units(services: list[str], devices: list[str]) -> list[str]:
    """Expand systemd template units into the instance names to enable.

    A template unit (foo@.service) gets one instance per device; a plain unit
    such as the ssh tunnel is enabled once, not once per device.
    """
    units: list[str] = []
    for service in services:
        if "@.service" in service:
            units.extend(service.replace("@", "@" + device, 1) for device in devices)
        else:
            units.append(service)
    return units


def mkTunnelDeps(args: Namespace) -> str:
    """[Unit] ordering that ties a port service to the ssh tunnel service."""
    return ("# Wants=, not Requires=: a tunnel restart must not tear down every\n"
            "# port instance; they get ECONNREFUSED on the forward and retry.\n"
            f"Wants={args.tunnelService}\n"
            f"After={args.tunnelService}")


def substitute_template(content: str, args: Namespace, root: str) -> str:
    """Replace @MARKER@ placeholders in a service template with args values."""
    # When tunneling, the port services talk to the local end of the ssh
    # forward and the tunnel unit carries @REMOTEHOST@:@REMOTEPORT@ onward,
    # so --hostname/--port keep meaning "the dockserver" either way.
    qTunnel = args.tunnelProxy is not None
    content = content.replace("@DATE@", "Generated on " + time.asctime())
    content = content.replace("@GENERATED@", str(args))
    content = content.replace("@REMOTEHOST@", args.hostname)
    content = content.replace("@REMOTEPORT@", str(args.port))
    content = content.replace("@LOCALPORT@", str(args.tunnelLocalPort))
    content = content.replace("@PROXY@", args.tunnelProxy or "")
    content = content.replace("@SSHKEY@", args.tunnelKey or "")
    content = content.replace("@SSH@", args.ssh)
    content = content.replace("@TUNNELRESTARTSECONDS@", str(args.tunnelRestartSeconds))
    content = content.replace("@TUNNELDEPS@", mkTunnelDeps(args) if qTunnel else "")
    content = content.replace("@USERNAME@", args.username)
    content = content.replace("@GROUPNAME@", args.group)
    content = content.replace("@DIRECTORY@", args.directory)
    content = content.replace("@EXECUTABLE@", os.path.join(root, args.executable))
    content = content.replace("@HOSTNAME@", "127.0.0.1" if qTunnel else args.hostname)
    content = content.replace("@PORT@", str(args.tunnelLocalPort if qTunnel else args.port))
    content = content.replace("@BAUDRATE@", str(args.baudrate))
    content = content.replace("@TIMEOUT@", str(args.timeout))
    content = content.replace("@RESTARTSECONDS@", str(args.restartSeconds))
    content = content.replace("@MEMORYMAX@", args.memoryMax)
    return content

def validate_args(args: Namespace) -> None:
    """Validate arguments, raising SystemExit on invalid values."""
    if not 1 <= args.port <= 65535:
        raise SystemExit(f"--port must be 1-65535, got {args.port}")
    if args.timeout < 1:
        raise SystemExit(f"--timeout must be positive, got {args.timeout}")
    if args.restartSeconds < 1:
        raise SystemExit(f"--restartSeconds must be positive, got {args.restartSeconds}")
    if not args.memoryMax:
        raise SystemExit("--memoryMax must be a non-empty systemd memory value (e.g. 128M)")
    if args.tunnelProxy is None:
        return
    if not args.tunnelProxy.strip():
        raise SystemExit("--tunnelProxy must be [user@]hostname of the ssh proxy host")
    if not 1 <= args.tunnelLocalPort <= 65535:
        raise SystemExit(f"--tunnelLocalPort must be 1-65535, got {args.tunnelLocalPort}")
    if args.tunnelRestartSeconds < 1:
        raise SystemExit(
                f"--tunnelRestartSeconds must be positive, got {args.tunnelRestartSeconds}")

if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("service", type=str, nargs="*", help="Service file(s) to copy")
    parser.add_argument("--serviceDirectory", type=str, default="/etc/systemd/system",
            help="Where to copy service file to")
    parser.add_argument("--device", type=str, action="append", help="Explicit devices to enable, ttyUSB0...")
    grp = parser.add_argument_group(description="Service file translation related options")
    grp.add_argument("--hostname", type=str, default="gliderfmc1.ceoas.oregonstate.edu",
            help="Remote hostname")
    grp.add_argument("--port", type=int, default=6565, help="Port number on remote host")
    grp.add_argument("--username", type=str, help="User to execute service as")
    grp.add_argument("--group", type=str, default="dialout", help="Group to execute service as")
    grp.add_argument("--baudrate", type=int, default=115200, help="Baud rate for serial connection")
    grp.add_argument("--timeout", type=int, default=3600,
            help="Seconds for connection to timeout with no activity")
    grp.add_argument("--directory", type=str, help="Directory to change to for running the service")
    grp.add_argument("--restartSeconds", type=int, default=60,
            help="Time before restarting the service after the previous instance exits")
    grp.add_argument("--memoryMax", type=str, default="128M",
            help="systemd MemoryMax= value per service (default 128M; lower if running many ports on tight RAM)")
    grp.add_argument("--executable", type=str, default="serial2RUDICS.py",
            help="Executable name to be executed by service")
    grp = parser.add_argument_group(description="SSH tunnel related options")
    grp.add_argument("--tunnelProxy", type=str,
            help="Reach the dockserver through this ssh proxy host, [user@]hostname. "
                 "Installs the tunnel service and points the port services at the "
                 "local end of the forward instead of --hostname directly")
    grp.add_argument("--tunnelLocalPort", type=int, default=16565,
            help="Port on 127.0.0.1 the ssh tunnel forwards from")
    grp.add_argument("--tunnelKey", type=str,
            help="SSH identity file for the tunnel (default ~USER/.ssh/id_rudics)")
    grp.add_argument("--tunnelService", type=str, default="rudics-tunnel.service",
            help="Service file for the ssh tunnel")
    grp.add_argument("--tunnelRestartSeconds", type=int, default=5,
            help="Time before restarting the tunnel after ssh exits")
    grp.add_argument("--ssh", type=str, default="/usr/bin/ssh", help="ssh executable")
    parser.add_argument("--force", action="store_true", help="Force writing a new file")
    parser.add_argument("--systemctl", type=str, default="/bin/systemctl",
            help="systemctl executable")
    parser.add_argument("--mkdir", type=str, default="/bin/mkdir", help="mkdir executable")
    parser.add_argument("--cp", type=str, default="/bin/cp", help="cp executable")
    parser.add_argument("--chmod", type=str, default="/bin/chmod", help="chmod executable")
    parser.add_argument("--sudo", type=str, default="/usr/bin/sudo", help="sudo executable")
    args = parser.parse_args()

    if not args.service:
        args.service.append("USBToRUDICS@.service")

    if (args.tunnelProxy is not None) and (args.tunnelService not in args.service):
        args.service.append(args.tunnelService)

    if not args.device:
        args.device = [f"ttyUSB{x}" for x in range(10)]

    if args.username is None:
        args.username = getpass.getuser()

    if args.directory is None:
        args.directory = os.path.expanduser(f"~{args.username}/logs")

    # The key is read by the service user, not by whoever runs this script.
    if args.tunnelKey is None:
        args.tunnelKey = os.path.expanduser(f"~{args.username}/.ssh/id_rudics")

    validate_args(args)

    args.tunnelKey = os.path.abspath(os.path.expanduser(args.tunnelKey))
    args.directory = os.path.abspath(os.path.expanduser(args.directory))
    args.serviceDirectory = os.path.abspath(os.path.expanduser(args.serviceDirectory))

    root = os.path.dirname(os.path.abspath(__file__)) # Where the script is at

    if not os.path.isdir(args.directory):
        print("Creating working directory", args.directory)
        os.makedirs(args.directory, exist_ok=True)

    qDidSomething = False

    for service in args.service: # Walk through services to copy over
        target = os.path.join(args.serviceDirectory, service)
        if not os.path.isabs(service):
            service = os.path.join(root, service)
        service = os.path.abspath(os.path.expanduser(service))
        if not os.path.isfile(service):
            print(f"ERROR {service} does not exist")
            continue

        with open(service) as fp:
            content = fp.read() # Load the new service
        content = substitute_template(content, args, root)

        if not args.force and os.path.exists(target):
            try:
                with open(target) as fp:
                    current = barebones(fp.read()) # Current contents
                    proposed = barebones(content) # What we want to write
                    if current == proposed:
                        print("No need to update, identical")
                        continue
            except OSError as e:
                # Unreadable (e.g. root-owned, or gone since the exists check).
                # Fall through and rewrite it rather than skipping the install.
                print(f"Could not read {target} to compare ({e}), rewriting")

        if not os.path.isdir(os.path.dirname(target)):
            wd = os.path.dirname(target)
            print("Making", wd)
            subprocess.run((args.sudo, args.mkdir, "-p", wd), shell=False, check=True)

        # Write to a temporary file, then copy as root via sudo
        with NamedTemporaryFile(mode="w") as tfp:
            tfp.write(content)
            tfp.flush()
            print("Writing to", target)
            subprocess.run((args.sudo, args.cp, tfp.name, target), shell=False, check=True)
            subprocess.run((args.sudo, args.chmod, "0644", target), shell=False, check=True)

        qDidSomething = True

    if qDidSomething:
        print("Forcing reload of daemon")
        subprocess.run((args.sudo, args.systemctl, "daemon-reload"), shell=False, check=True)

        units = expand_units(args.service, args.device)
        if units:
            cmd = [args.sudo, args.systemctl, "enable"]
            cmd.extend(units)
            print("Enabling", " ".join(units))
            subprocess.run(cmd, shell=False, check=True)

        if args.tunnelProxy is not None:
            # udev starts the port instances, but nothing triggers the tunnel,
            # so it stays down until boot unless started by hand. Not started
            # here: the ssh key and known_hosts may not be in place yet, and
            # Restart=always would loop on the failure.
            print(f"\nSSH tunnel installed: 127.0.0.1:{args.tunnelLocalPort}"
                  f" -> {args.hostname}:{args.port} via {args.tunnelProxy}")
            print(f"Before starting it, on this host as {args.username}:")
            print(f"  ssh-keygen -t ed25519 -N '' -f {args.tunnelKey}   # if not already made")
            print(f"  ssh-keyscan -H {args.tunnelProxy.split('@')[-1]}"
                  f" >> ~{args.username}/.ssh/known_hosts")
            print(f"and install {args.tunnelKey}.pub on the proxy host, then:")
            print(f"  sudo systemctl start {args.tunnelService}")
