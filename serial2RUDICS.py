#! /usr/bin/env python3
#
# Read from a serial port which is direct connect to a glider simulator,
# shoebox or pocket.
#
# When the glider is on the surface, connect to a RUDICS port on a dockserver
#
# When the glider dives, disconnect from the dockserver.
#
# September-2019, Pat Welch, pat@mousebrains.com

import argparse
import logging
import select
import signal
import sys
from typing import Any

import FauxDockServer
import FauxSerial
import MyLogger
from RealSerial import RealSerial
from RUDICS import RUDICS

logger = logging.getLogger(__name__)

def doit(serial: RealSerial, rudics: RUDICS, binary: str | None = None) -> None:
    # SIM115: the trace file stays open across the whole select loop; the
    # try/finally below is the context manager.
    ofp = open(binary, "wb") if binary else None  # noqa: SIM115

    try:
        while serial or rudics: # While an open serial port or stuff to send to RUDICS
            rudics.timedOut() # Check timeouts every iteration, not just on select timeout

            ifpSerial = serial.inputFileno()
            ofpSerial = serial.outputFileno()
            ifpRUDICS = rudics.inputFileno()
            ofpRUDICS = rudics.outputFileno()

            ifps: list[Any] = [] # input file numbers to select on
            ofps: list[Any] = [] # output file numbers to select on

            if ifpSerial is not None:
                ifps.append(ifpSerial)
            if ofpSerial is not None:
                ofps.append(ofpSerial)
            if ifpRUDICS is not None:
                ifps.append(ifpRUDICS)
            if ofpRUDICS is not None:
                ofps.append(ofpRUDICS)

            timeout = rudics.timeout()
            [readable, writeable, exceptable] = select.select(ifps, ofps, ifps, timeout)

            if not readable and not writeable and not exceptable: # Timeout
                continue

            for fp in exceptable: # Handle exceptions first
                if fp == ifpSerial:
                    logger.warning('Select exception for serial connection')
                    serial.close() # Exception on the serial side
                else: # exception on the RUDICS side
                    logger.warning('Select exception for RUDICS connection')
                    rudics.close() # qWantOpen stays as-is; main loop will redial

            if exceptable:
                continue # Skip reading/writing this time if there are exceptions

            for fp in writeable:
                if fp == ofpSerial:
                    serial.send()
                else: # RUDICS
                    rudics.send()

            for fp in readable:
                if fp == ifpSerial:
                    n = serial.nAvailable() # How many characters are available
                    if n <= 0: # Transient zero from in_waiting despite select, skip
                        continue
                    c = serial.get(n) # Read a character
                    if c:
                        rudics.put(c)
                        if ofp:
                            ofp.write(f"SERIAL {len(c)} : ".encode() + c + b'\n')
                    else: # EOF
                        serial.close()
                else: # RUDICS
                    c = rudics.get(1024 * 1024) # Read what is available up to 1MB
                    if c:
                        serial.put(c)
                        if ofp:
                            ofp.write(f"RUDICS {len(c)} : ".encode() + c + b'\n')
                    # get() already handled close and reconnect intent
    finally:
        if ofp:
            ofp.close()

def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a RUDICS connection for a Slocum simulator")
    MyLogger.addArgs(parser)
    FauxSerial.addArgs(parser)
    RealSerial.addArgs(parser)
    FauxDockServer.addArgs(parser)
    RUDICS.addArgs(parser)
    parser.add_argument("--binary", type=str, help="Binary output filename")
    args = parser.parse_args()

    MyLogger.mkLogger(args)
    logger.info('args=%s', args)

    tty = None
    rudics = None

    signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(0))

    try:
        args.serial, faux = FauxSerial.setup(args)
        args = FauxDockServer.setup(args)
        tty = RealSerial(args) # Serial input/output
        if faux is not None:
            # Only now does something hold the PTY slave open, so the writer
            # thread's select() cannot see a hangup and destroy the device.
            faux.start()
        if tty.fp is None:
            # RealSerial logs and swallows open failures. Without this guard we
            # would go on to dial the dockserver and hold a RUDICS session for a
            # glider we cannot hear -- a phantom session. Exit non-zero instead
            # and let systemd's Restart=always retry the port.
            logger.error('Serial port %s did not open, exiting for a restart', args.serial)
            sys.exit(1)
        rudics = RUDICS(args)
        doit(tty, rudics, args.binary)
    except Exception:
        logger.exception('Unexpected exception')
    finally:
        logger.info('Fell into finally')
        if tty is not None:
            tty.close()
        if rudics is not None:
            rudics.close()


if __name__ == "__main__":
    main()
