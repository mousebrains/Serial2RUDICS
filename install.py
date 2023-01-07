#! /usr/bin/env python3
#
# Install a service for acting as a RUDICS connection to an SFMC server
#
# Jan-2023, Pat Welch, pat@mousebrains.com

from argparse import ArgumentParser
import subprocess
from tempfile import NamedTemporaryFile
import yaml
import socket
import os
import re
import time
import sys

def barebones(content:str) -> list[str]:
    lines = []
    for line in content.split("\n"):
        line = line.strip()
        if (len(line) == 0) or (line[0] == "#"): continue
        lines.append(line)
    return lines

parser = ArgumentParser()
parser.add_argument("service", type=str, nargs="*", help="Service file(s) to copy")
parser.add_argument("--serviceDirectory", type=str, default="/etc/systemd/system",
        help="Where to copy service file to")
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

if not args.service: args.service.append("USBToRUDICS@.service")

if args.username is None: args.username = os.getlogin()

if args.directory is None: args.directory = "~/logs" # working directory to move to

args.directory = os.path.abspath(os.path.expanduser(args.directory))
args.serviceDirectory = os.path.abspath(os.path.expanduser(args.serviceDirectory))

root = os.path.dirname(os.path.abspath(__file__)) # Where the script is at

qDidSomething = False

for service in args.service: # Walk through services to copy over
    target = os.path.join(args.serviceDirectory, service)
    service = os.path.abspath(os.path.expanduser(service))
    if not os.path.isfile(service):
        print(f"ERROR {service} does not exist")
        continue

    with open(service, "r") as fp: input = fp.read() # Load the new service
    input = re.sub(r"@DATE@", "Generated on " + time.asctime(), input)
    input = re.sub(r"@GENERATED@", str(args), input)
    input = re.sub(r"@USERNAME@", args.username, input)
    input = re.sub(r"@GROUPNAME@", args.group, input)
    input = re.sub(r"@DIRECTORY@", args.directory, input)
    input = re.sub(r"@EXECUTABLE@", os.path.join(root, args.executable), input)
    input = re.sub(r"@HOSTNAME@", args.hostname, input)
    input = re.sub(r"@PORT@", str(args.port), input)
    input = re.sub(r"@BAUDRATE@", str(args.baudrate), input)
    input = re.sub(r"@TIMEOUT@", str(args.timeout), input)
    input = re.sub(r"@RESTARTSECONDS@", str(args.restartSeconds), input)

    if not args.force and os.path.exists(target):
        try:
            with open (target, "r") as fp:
                current = barebones(fp.read()) # Current contents
                proposed = barebones(input) # What we want to write
                if current == proposed:
                    print("No need to update, identical")
                    continue
        except:
            pass

    if not os.path.isdir(os.path.dirname(target)):
        wd = os.path.dirname(target)
        print("Making", wd)
        subprocess.run((args.sudo, args.mkdir, "-p", wd), shell=False, check=True)

    # Write to a temporary file, then copy as root via sudo
    with NamedTemporaryFile(mode="w") as fp:
        fp.write(input)
        fp.flush()
        print("Writing to", target)
        subprocess.run((args.sudo, args.cp, fp.name, target), shell=False, check=True)
        subprocess.run((args.sudo, args.chmod, "0644", target))

    qDidSomething = True

if qDidSomething:
    print("Forcing reload of daemon")
    subprocess.run((args.sudo, args.systemctl, "daemon-reload"), shell=False, check=True)
 
    services = " ".join(args.service)
    print(f"Enabling {services}")
    subprocess.run((args.sudo, args.systemctl, "enable", services), shell=False, check=True)

    # print(f"Starting {services}")
    # subprocess.run((args.sudo, args.systemctl, "restart", services), shell=False, check=True)

    # print(f"Status {services}")
    # s = subprocess.run((args.sudo, args.systemctl, "--no-pager", "status", services),
            # shell=False, check=True)
