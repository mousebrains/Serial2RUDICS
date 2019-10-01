#! /usr/bin/env python3
#
# Read from a serial port which is direct connected to a glider simulator,
# shoebox or pocket.
#
# When the glider is on the surface, connect to a RUDICS port on a dockserver
# and simulate a RUDICS connection.
#
# When the glider dives, disconnect from the dockserver.
#
# September-2019, Pat Welch, pat@mousebrains.com

import time
import math
import argparse
import socket
import select
import serial
import re
import logging
import logging.handlers

def mkLogger(args):
    logger = logging.getLogger()

    if args.logfile:
        ch = logging.handlers.RotatingFileHandler(args.logfile, maxBytes=1000000)
    else:
        ch = logging.StreamHandler()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    return logger

def mkSerial(args, logger):
    if args.input:
        logger.info('Opening file %s for input and %s for output', args.input, args.output)
        return (open(args.input, 'rb'), open(args.output, 'wb'))
    parity = serial.PARITY_NONE
    if args.parity == 'even': parity = serial.PARITY_EVEN
    elif args.parity == 'odd': parity = serial.PARITY_ODD
    elif args.parity == 'mark': parity = serial.PARITY_MARK
    elif args.parity == 'space': parity = serial.PARITY_SPACE

    logger.info('Opening serial port %s parity=%s baudrate=%s bytesize=%s stopbits=%s', \
             args.serial, args.parity, args.baudrate, args.bytesize, args.stopbits)
    fp = serial.Serial(port=args.serial,
             baudrate=args.baudrate,
             bytesize=args.bytesize,
             parity=parity,
             stopbits=args.stopbits
             )
    return (fp, fp)

class SimRUDICS:
    def __init__(self, ifn, ofn):
        self.ifp = None if ifn is None else open(ifn, 'rb')
        self.ofp = open(ofn, 'wb')

    def fileno(self):
        return self.ofp.fileno()
    def recv(self, n):
        return b'' if self.ifp is None else self.ifp.read(n)
    def send(self, msg):
        return self.ofp.write(msg)
    def shutdown(self, flags):
        return True
    def close(self):
        if self.ifp is not None:
            self.ifp.close()
        return self.ofp.close()

def openRUDICS(args, logger):
    if args.simDS:
        return SimRUDICS(args.dsInput, args.dsOutput)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(None) # Non-blocking
        s.connect((args.host, args.port)) # Connect to RUDICS listener on a Dockserver
        logger.info('Connected to %s:%s', args.host, args.port)
        return s
    except Exception as e:
        logger.exception('Unexpected error connecting to %s:%s', args.host, args.port)
    return None

def closeRUDICS(s, args, logger):
    if args.simDS:
        logger.info('Closing simDS %s %s', args.dsInput, args.dsOutput)
        s.close() # Close the file
        return None

    try:
        # Shutdown seems to hold the connection open????
        # s.shutdown(socket.SHUT_RDWR) # Shutdown the connection
        s.close() # Free up resources
        logger.info('Closed %s:%s', args.host, args.port)
    except Exception as e:
        logger.exception('Error closing %s:%s', args.host, args.port)
    return None

class Timers:
    def __init__(self, args, logger):
        self.logger = logger
        self.now = time.time()
        self.dsTimeSent = 0
        self.dsTimeClose = 0
        self.serTimeSent = 0
        self.dsSpacing = args.dsSpacing
        self.dsBaud = args.dsBaudrate
        self.serBaud = args.serBaudrate
        self.delayRUDICS = self.dsSpacing # Delay between closing/opening Dockserver connections
        # Delay between sending bytes to Dockserver 
        self.dtRUDICS = self.baud2dt(self.dsBaud, 0)
        # Delay between sending bytes to serial port
        self.dtSer = self.baud2dt(self.serBaud, args.stopbits) 

    def __str__(self):
        msg =   'Dockserver baudrate={} dt={}'.format(self.dsBaud, self.dtRUDICS)
        msg+= '\nDockserver delay between close/open {}'.format(self.dsSpacing)
        msg+= '\nSerial baudrate={} dt={}'.format(self.serBaud, self.dtSer)
        return msg

    def baud2dt(self, baud, stopbits):
        if (baud is None) or (baud <= 0):
            return 0
        return 1 / (baud / (8 + stopbits))

    def calcDelta(self, tPrev, dt):
        return max(0, (tPrev + dt) - self.now)

    def calcNumber(self, tPrev, dt):
        return 1e6 if dt == 0 else max(0, math.floor((self.now - tPrev) / dt))

    def nDockserver(self): # How many characters can I send to keep average baud rate
        return self.calcNumber(self.dsTimeSent, self.dtRUDICS)

    def dtDockserver(self): # delay until next byte can be sent to the dockserver
        return self.calcDelta(self.dsTimeSent, self.dtRUDICS)

    def nSerial(self): # How many characters can I send to keep average baud rate
        return self.calcNumber(self.serTimeSent, self.dtSer)

    def dtSerial(self): # delay until next byte can be sent to the serial port
        return self.calcDelta(self.serTimeSent, self.dtSer)

    def dtOpen(self): # delay until next Open can happen
        return self.calcDelta(self.dsTimeClose, self.dsSpacing)

    def dtSelect(self):
        dt = [self.dtDockserver(), self.dtSerial(), self.dtOpen()]
        if max(dt) == 0:
            return 0
        tMax = 1e10
        for a in dt:
            if (a > 0) and (tMax > a):
                tMax = a
        return a

baudrates ={110, 300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 
        28800, 38400, 56000, 57600, 115200, 
        128000, 153600, 230400, 25600,
        460800, 921600
        }

parser = argparse.ArgumentParser(
        description='Simulate a Dockserver connection for a Slocum simulator')

grp = parser.add_mutually_exclusive_group(required=True)
grp.add_argument('--host', help='Dockserver hostname with RUDICS listener')
grp.add_argument('--simDS', action='store_true', help='Simulate a Dockserver connection')

grp = parser.add_argument_group('Real Dockserver Options')
grp.add_argument('--port', type=int, default=6565, help="Dockserver's RUDICS port")
grp.add_argument('--dsSpacing', type=float, default=10, 
        help='Delay between closing a Dockserver connection and opening a new one in seconds')
grp.add_argument('--dsBaudrate', type=int, choices=baudrates,
        help='Baudrate to feed characters to the dockserver at')

grp = parser.add_argument_group('Simulated Dockserver Options')
grp.add_argument('--dsInput', type=str,  
        help='When --simDS is specified, where should simulated Dockserver input come from')
grp.add_argument('--dsOutput', type=str, default='/dev/null',
        help='When --simDS is specified, where should simulated Dockserver output goto')

grp = parser.add_mutually_exclusive_group(required=True)
grp.add_argument('--serial', help='Serial port to listen on')
grp.add_argument('--input', help='Input file to send to Dockserver')

grp = parser.add_argument_group('Real Serial Port Options')
grp.add_argument('--baudrate', type=int, choices=baudrates, default=115200,
        help='Serial port baudrate')
grp.add_argument('--parity', type=str, choices=['none', 'even', 'odd', 'mark', 'space'], 
        default='none', help='Serial port parity')
grp.add_argument('--bytesize', type=int, choices=[5, 6, 7, 8], default=8, help='Bits/byte')
grp.add_argument('--stopbits', type=float, choices=[1, 1.5, 2], 
        default=1, help='Number of stop bits')
grp.add_argument('--serBaudrate', type=int, choices=baudrates,
        help='Baudrate to feed characters to the serial port at')

grp = parser.add_argument_group('Serial Port options when reading from a file')
grp.add_argument('--output', type=str, default='/dev/null',
        help='Where to send serial output to when --input is specified')

grp = parser.add_argument_group('Logger Related Options')
grp.add_argument('--logfile', help='Name of logfile')
grp.add_argument('--verbose', action='store_true', help='Enable verbose logging')

grp = parser.add_argument_group('Trigger on/off Options')
grp.add_argument('--triggerOff', default='surface_[0-9]+: Waiting for final GPS fix.',
        help='shutdown Dockserver connection after this line seen')
grp.add_argument('--triggerOn', 
        default='behavior surface_[0-9]+: SUBSTATE [0-9]+ ->[0-9]+ : Picking iridium or freewave',
        help='Start Dockserver connection after this line seen')

args = parser.parse_args()

args.triggerOn  = bytes(args.triggerOn,  'utf-8')
args.triggerOff = bytes(args.triggerOff, 'utf-8')

logger = mkLogger(args)
logger.info('args=%s', args)

try:
    (ifp, ofp) = mkSerial(args, logger) # Serial input/output file handles
    toRUDICS = bytearray() # Buffer to send to the RUDICS connection
    toSerial = bytearray() # Buffer to send to the serial port
    lineSerial = bytearray() # Used for pattern matching and diagnostics
    lineRUDICS = bytearray() # Diagnostic output

    rudics = openRUDICS(args, logger) # Initially open the dockserver connection
    timers = Timers(args, logger) # Delay timers
    qConnected = True # Connected to Dockserver initially
    qSend2RUDICS = True # Send to the Dockserver

    logger.info('Timers\n%s', timers)

    # Loop until the serial port has been closed,
    # and there is nothing left to send to the Dockserver
    while (ifp is not None) or len(toRUDICS):
        ifps = []
        ofps = []

        if ifp is not None:
            if args.input is None: # Using a real serial port
                if (timers.dtSerial() <= 0):
                    ifps.append(ifp)
            else: # Using a file input, so simulate a baud rate
                ifps.append(ifp)
        if len(toSerial) and (ofp is not None) and (timers.dtSerial() <= 0):
            ofps.append(ofp)

        timers.now = time.time() # Update current time

        qSend2RUDICS = len(toRUDICS) \
                and (timers.dtDockserver() <= 0) # Send to Dockserver

        if rudics is not None:
            if qConnected:
                if args.simDS and rudics.ifp is not None:
                    ifps.append(rudics.ifp)
                elif not args.simDS:
                    ifps.append(rudics)
            if qSend2RUDICS:
                ofps.append(rudics) # Send something to the Dockserver
            elif not qConnected and not len(toRUDICS):
                logger.info('Closing due to not qConnected')
                rudics = closeRUDICS(rudics, args, logger)
                timers.dsTimeClose = time.time()
        elif (qConnected or qSend2RUDICS) and (timers.dtOpen() <= 0): 
            rudics = openRUDICS(args, logger)
            logger.info('Opened Dockserver')
            if qSend2RUDICS:
                ofps.append(rudics)
            if qConnected:
                if args.simDS and rudics.ifp is not None:
                    ifps.append(rudics.ifp)
                elif not args.simDS:
                    ifps.append(rudics)

        # logger.info('Pre dt=%s n %s %s q %s %s ifp %s rudics %s', 
                # timers.dtSelect(), 
                # len(toSerial), len(toRUDICS),
                # qConnected, qSend2RUDICS, 
                # ifp == None, rudics == None)
        # Wait for something to be availble to read/write or timeout
        [readable, writeable, exceptable] = select.select( ifps, ofps, [], timers.dtSelect())


        # Do write first so we can simulate the baud rate better
        for fp in writeable:
            if fp == ofp:
                if timers.dtSer > 0:
                    m = max(1,timers.nSerial())
                    n = fp.write(toSerial[0:min(m, len(toSerial))])
                    timers.serTimeSent = time.time()
                else:
                    n = fp.write(toSerial)
                logger.info('Wrote %s to serial, %s', n, toSerial)
                toSerial = toSerial[n:]
            elif fp == rudics:
                if timers.dtRUDICS > 0:
                    m = max(1,timers.nDockserver()) # Allow catchup speed
                    n = fp.send(toRUDICS[0:min(m, len(toRUDICS))])
                    timers.dsTimeSent = time.time()
                else:
                    n = fp.send(toRUDICS)
                if n == 0:
                    logger.info('Closing due to n==0')
                    rudics = closeRUDICS(rudics, args, logger)
                    timers.dsTimeClose = time.time()
                else:
                    toRUDICS = toRUDICS[n:]

        for fp in readable:
            if fp == ifp: # From serial port
                c = fp.read(1) # Read a character
                if not len(c): # End of file?
                    ifp.close() # Close the port/file
                    ifp = None
                    ofp = None
                    logger.info('Serial port closed')
                    continue
                lineSerial += c
                if qConnected:
                    toRUDICS += c
                if c == b'\r': # End of a lineSerial, so check if a trigger activated
                    logger.info('Serial=%s', bytes(lineSerial))
                    if not qConnected and re.search(args.triggerOn, lineSerial):
                        logger.info('TriggerOn')
                        qConnected = True
                    elif qConnected and re.search(args.triggerOff, lineSerial):
                        logger.info('TriggerOff')
                        qConnected = False
                    lineSerial = bytearray()
            elif fp == rudics: # Read from RUDICS connection
                c = fp.recv(8192) # Read what is available up to 8192 bytes
                if len(c): # Something read
                    toSerial += c
                    lineRUDICS += c
                    if c.find(b'\r') >= 0:
                        parts = lineRUDICS.split(b'\r')
                        for index in range(len(parts)-1):
                            logger.info('RUDICS=%s', bytes(parts[index]))
                        if c[-1] == b'\r': #
                            logger.info('RUDICS=%s', bytes(parts[-1]))
                            lineRUDICS = bytearray()
                        else:
                            lineRUDICS = parts[-1]
                else: # Nothing read indicates socket closed
                    rudics = closeRUDICS(rudics, args, logger)
                    timers.dsTimeClose = time.time()


finally:
    logger.info('Fell out of loop into finally')
    # Check ifp/rudics are defined before testing them, just in case
    if ('ifp' in vars()) and (ifp is not None):
        ifp.close()
    if ('rudics' in vars()) and (rudics is not None):
        closeRUDICS(rudics, args, logger)
