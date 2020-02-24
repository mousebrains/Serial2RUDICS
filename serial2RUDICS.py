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

        if ifpSerial is not None: ifps.append(ifpSerial)
        if ofpSerial is not None: ofps.append(ofpSerial)
        if ifpRUDICS is not None: ifps.append(ifpRUDICS)
        if ofpRUDICS is not None: ofps.append(ofpRUDICS)

        timeout = rudics.timeout()
        # logger.info('timeout=%s ifps=%s ofps=%s s %s r %s', 
                # timeout, len(ifps), len(ofps), len(serial.buffer), len(rudics.buffer))
        [readable, writeable, exceptable] = select.select(ifps, ofps, ifps, timeout)

        if not readable and not writeable and not exceptable: # Timeout
            rudics.timedOut()
            continue

        for fp in exceptable: # Handle exceptions first
            # logger.info('exceptable fp=%s', fp)
            if fp == ifpSerial:
                logger.warning('Select exception for serial connection')
                serial.close() # Exception on the serial side
            else: # exception on the RUDICS side
                logger.warning('Select exception for RUDICS connection')
                rudics.close()

        if exceptable: continue # Skip reading/writing this time if there are exceptions

        for fp in writeable:
            if fp == ofpSerial:
                serial.send()
            else: # RUDICS
                rudics.send()


        for fp in readable:
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
