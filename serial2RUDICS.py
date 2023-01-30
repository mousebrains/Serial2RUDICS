#! /usr/bin/env python3
#
# Read from a serial port which is direct connect to a gider simulator,
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
import MyLogger
import FauxSerial
import FauxDockServer
from RealSerial import RealSerial
from RUDICS import RUDICS
import time

def doit(serial:RealSerial, rudics:RUDICS, binary:str=None) -> None:
    ofp = open(binary, "wb") if binary else None

    while bool(serial) or bool(rudics): # While an open serial port or stuff to send to RUDICS
        ifpSerial = serial.inputFileno()
        ofpSerial = serial.outputFileno()
        ifpRUDICS = rudics.inputFileno()
        ofpRUDICS = rudics.outputFileno()

        ifps = [] # input file numbers to select on
        ofps = [] # output file numbers to select on

        if ifpSerial is not None: ifps.append(ifpSerial)
        if ofpSerial is not None: ofps.append(ofpSerial)
        if ifpRUDICS is not None: ifps.append(ifpRUDICS)
        if ofpRUDICS is not None: ofps.append(ofpRUDICS)

        timeout = rudics.timeout()
        # logging.info('timeout=%s ifps=%s ofps=%s s %s r %s', 
                # timeout, len(ifps), len(ofps), len(serial.buffer), len(rudics.buffer))
        [readable, writeable, exceptable] = select.select(ifps, ofps, ifps, timeout)

        if not readable and not writeable and not exceptable: # Timeout
            rudics.timedOut()
            continue

        for fp in exceptable: # Handle exceptions first
            # logging.info('exceptable fp=%s', fp)
            if fp == ifpSerial:
                logging.warning('Select exception for serial connection')
                serial.close() # Exception on the serial side
            else: # exception on the RUDICS side
                logging.warning('Select exception for RUDICS connection')
                rudics.close()

        if exceptable: continue # Skip reading/writing this time if there are exceptions

        for fp in writeable:
            if fp == ofpSerial:
                serial.send()
            else: # RUDICS
                rudics.send()


        for fp in readable:
            if fp == ifpSerial:
                n = serial.nAvailable() # How many characters are available
                c = serial.get(n) # Read a character
                if len(c): 
                    rudics.put(c)
                    if ofp: ofp.write(bytes(f"SERIAL {len(c)} : ", "UTF-8") + c + b'\n')
                else: # EOF
                    serial.close()
            else: # RUDICS
                c = rudics.get(1024 * 1024) # Read what is available up to 1MB
                if len(c):
                    serial.put(c)
                    if ofp: ofp.write(bytes(f"RUDICS {len(c)} : ", "UTF-8") + c + b'\n')
                else: # EOF
                    rudics.close()

    if ofp: ofp.close()

parser = argparse.ArgumentParser(description="Simulate a RUIDCS connection for a Slocum simulator")
MyLogger.addArgs(parser)
FauxSerial.addArgs(parser)
RealSerial.addArgs(parser)
FauxDockServer.addArgs(parser)
RUDICS.addArgs(parser)
parser.add_argument("--binary", type=str, help="Binary output filename")
args = parser.parse_args()

MyLogger.mkLogger(args)
logging.info('args=%s', args)

tty = None
rudics = None

try:
    args.serial = FauxSerial.setup(args)
    args = FauxDockServer.setup(args)
    tty = RealSerial(args) # Serial input/output
    rudics = RUDICS(args)
    doit(tty, rudics, args.binary)
except:
    logging.exception('Unexpected exception')
finally:
    logging.info('Fell into finally')
    if tty is not None: 
        tty.close()
    if rudics is not None:
        rudics.close()
