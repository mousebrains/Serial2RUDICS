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

def doit(serial:RealSerial, rudics:RUDICS, logger:logging.Logger) -> None:

    while bool(serial) or bool(rudics): # While an open serial port or stuff to send to RUDICS
        ifpSerial = serial.inputFileno()
        ofpSerial = serial.outputFileno()
        ifpRUDICS = rudics.inputFileno()
        ofpRUDICS = rudics.outputFileno()

        ifps = [] # input file numbers to select on
        ofps = [] # output file numbers to select on
        efps = [] # exception file numbers to select on

        if ifpSerial is not None: 
            ifps.append(ifpSerial)
            efps.append(ifpSerial)
        if ofpSerial is not None: ofps.append(ofpSerial)
        if ifpRUDICS is not None: 
            ifps.append(ifpRUDICS)
            efps.append(ifpRUDICS)
        if ofpRUDICS is not None: ofps.append(ofpRUDICS)

        timeout = rudics.timeout()
        # logger.info('timeout=%s ifps=%s ofps=%s efps=%s s %s r %s', 
                # timeout, len(ifps), len(ofps), len(efps), len(serial.buffer), len(rudics.buffer))
        [readable, writeable, exceptable] = select.select(ifps, ofps, efps, timeout)

        if not readable and not writeable and not exceptable: # Timeout
            rudics.timedOut()
            continue

        for fp in exceptable: # Handle exceptions first
            # logger.info('exceptable fp=%s', fp)
            if fp == ifpSerial:
                serial.close() # Exception on the serial side
            else: # exception on the RUDICS side
                rudics.close()

        if exceptable: # Some exceptions so skip trying to read/write this time
            continue

        for fp in writeable:
            # logger.info('writeable ofpSerial %s', fp == ofpSerial)
            if fp == ofpSerial:
                serial.send()
            else: # RUDICS
                rudics.send()


        for fp in readable:
            # logger.info('readable ifpSerial %s', fp == ifpSerial)
            if fp == ifpSerial:
                c = serial.get(1) # Read a character
                if len(c): 
                    rudics.put(c)
                else: # EOF
                    serial.close()
            else: # RUDICS
                c = rudics.get(8192) # Read what is available up to 8192 bytes
                if len(c):
                    serial.put(c)
                else: # EOF
                    rudics.close()

parser = argparse.ArgumentParser(description="Simulate a RUIDCS connection for a Slocum simulator")
MyLogger.addArgs(parser)
FauxSerial.addArgs(parser)
RealSerial.addArgs(parser)
FauxDockServer.addArgs(parser)
RUDICS.addArgs(parser)

args = parser.parse_args()

logger = MyLogger.mkLogger(args)
logger.info('args=%s', args)

tty = None
rudics = None

try:
    args.serial = FauxSerial.setup(args, logger)
    args = FauxDockServer.setup(args, logger)
    tty = RealSerial(args, logger) # Serial input/output
    rudics = RUDICS(args, logger)
    doit(tty, rudics, logger)
except:
    logger.exception('Unexpected exception')
finally:
    logger.info('Fell into finally')
    if tty is not None: 
        tty.close()
    if rudics is not None:
        rudics.close()
