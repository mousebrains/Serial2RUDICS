#! /usr/bin/env python3
#
# Install a service for acting as a RUDICS connection to an SFMC server
#
# Jan-2023, Pat Welch, pat@mousebrains.com

from argparse import ArgumentParser
import getpass
import subprocess
from tempfile import NamedTemporaryFile
import os
import time

def barebones(content: str) -> list[str]:
    lines = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or line[0] == "#":
            continue
        lines.append(line)
    return lines

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
    grp.add_argument("--executable", type=str, default="serial2RUDICS.py",
            help="Executable name to be executed by service")
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

    if not args.device:
        args.device = [f"ttyUSB{x}" for x in range(10)]

    if args.username is None:
        args.username = getpass.getuser()

    if args.directory is None:
        args.directory = "~/logs" # working directory to move to

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
        content = content.replace("@DATE@", "Generated on " + time.asctime())
        content = content.replace("@GENERATED@", str(args))
        content = content.replace("@USERNAME@", args.username)
        content = content.replace("@GROUPNAME@", args.group)
        content = content.replace("@DIRECTORY@", args.directory)
        content = content.replace("@EXECUTABLE@", os.path.join(root, args.executable))
        content = content.replace("@HOSTNAME@", args.hostname)
        content = content.replace("@PORT@", str(args.port))
        content = content.replace("@BAUDRATE@", str(args.baudrate))
        content = content.replace("@TIMEOUT@", str(args.timeout))
        content = content.replace("@RESTARTSECONDS@", str(args.restartSeconds))

        if not args.force and os.path.exists(target):
            try:
                with open(target) as fp:
                    current = barebones(fp.read()) # Current contents
                    proposed = barebones(content) # What we want to write
                    if current == proposed:
                        print("No need to update, identical")
                        continue
            except Exception:
                pass

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

        if args.device:
            devices = [
                service.replace("@", "@" + device, 1)
                for service in args.service
                for device in args.device
            ]
            cmd = [args.sudo, args.systemctl, "enable"]
            cmd.extend(devices)
            print("Enabling", " ".join(devices))
            subprocess.run(cmd, shell=False, check=True)
