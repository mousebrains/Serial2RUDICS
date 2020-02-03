#
# Connect to a dockserver through a socket connection
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import argparse
import logging
import re
import time
import math
import socket
from RealSerial import baudrates

class RUDICS:
    def __init__(self, args:argparse.ArgumentParser, logger:logging.Logger) -> None:
        self.args = args
        self.logger = logger
        self.triggerOn  = re.compile(bytes(args.triggerOn,  'utf-8'), re.IGNORECASE)
        self.triggerOff = re.compile(bytes(args.triggerOff, 'utf-8'), re.IGNORECASE)
        self.bytesPerSecond = \
                None if (args.rudicsBaudrate is None) or (args.rudicsBaudrate < 1) \
                else (9 / args.rudicsBaudrate) # Time to send 9 bits
        self.buffer = bytearray()
        self.line = bytearray()
        self.tLastOpen = 0
        self.tLastClose = 0
        self.tLastSend = 0
        self.tNextSend = 0
        self.tNextOpen = 0
        self.tLastAction = None
        self.qWantOpen = True # Initially I want to be open
        self.s = None

    @staticmethod
    def addArgs(parser:argparse.ArgumentParser) -> None:
        grp = parser.add_argument_group('RUDICS Trigger on/off Options')
        grp.add_argument('--triggerOff',
                default='surface_[0-9]+:\s+.*Waiting\s+for\s+final\s+GPS\s+fix',
                help='Shutdown Dockserver connection after this line seen')
        grp.add_argument('--triggerOn',
                default=
                '(behavior\s+surface_[0-9]+:\s+SUBSTATE\s+[0-9]+\s+->[0-9]+\s+:\s+Picking\s+iridium\s+or\s+freewave|:\s+abort_the_mission)',
                help='Start Dockserver connection after this line seen')
        grp.add_argument('--idleTimeout', type=int, default=3600,
                help='If not input from either the serial or socket in this period of time, drop the connection')
        grp = parser.add_argument_group('Real RUDICS')
        grp.add_argument('--port', type=int, default=6565, help="Dockserver's RUDICS port")

        grp.add_argument('--rudicsSpacing', type=float, default=10,
                help='Delay between closing a RUDICS connection and opening a new one in seconds')
        grp.add_argument('--rudicsBaudrate', type=int, choices=baudrates,
                help='Baudrate to feed characters to the RUDICS connection at')
        grp.add_argument('--rudicsDelay', type=int, default=120,
                help="Delay between retrys at connecting to the RUDICS port")
        grp.add_argument('--rudicsMaxOpenTime', type=int, default=86400,
                help="Maximumm length of time a single RUDICS connection can be open")
        grp.add_argument('--rudicsMaxOpenTimeDelay', type=int, default=1800,
                help="Time after a forced RUDICS disconnect until reopening")

    def __del__(self) -> None: # Destructor
        self.logger.info('Destroying RUDICS')
        self.close()

    def __bool__(self) -> bool:
        return (self.s is not None) and (len(self.buffer) > 0)

    def timeout(self) -> float:
        now = time.time()
        dt = max(1, self.args.idleTimeout - \
                (0 if self.tLastAction is None else (now - self.tLastAction)))

        if not len(self.buffer):
            return dt # Nothing to send, so wait this long

        if self.tNextOpen > now:
            if self.tNextSend > now:
                return min(dt, min(self.tNextOpen, self.tNextSend) - now)
            return min(dt, self.tNextOpen - now)
        if self.tNextSend > now:
            return min(dt, self.tNextSend - now)
        return dt

    def timedOut(self) -> None:
        self.logger.info('Idle timeout')
        self.close()
        self.tLastAction = time.time()

    def send(self) -> None:
        self.logger.info('RUDICS:send')
        now = time.time()

        if (self.s is None) or (not len(self.buffer)) or (self.tNextSend >= now):
            return

        if self.bytesPerSecond is None: # Not baudrate limited
            n = len(self.buffer) # Send whole buffer
        else: # baudrate limited
            self.tNextSend = now + self.bytesPerSecond
            dt = now - self.tLastSend # Time since the last send
            n = math.floor(dt / self.bytesPerSecond) # How many bytes can be sent
            if n <= 0:
                return

        if n >= len(self.buffer):
            m = self.write(self.buffer)
        else:
            m = self.write(self.buffer[0:n])

        self.logger.info('RUDICS:sent full buffer m=%s n=%s len=%s buffer=%s', 
                m, n, len(self.buffer), self.buffer)

        self.buffer = self.buffer[m:]

        if m > 0:
            self.tLastSend = now

    def put(self, c:bytes) -> None:
        self.tLastAction = time.time()
        if self.qWantOpen:
            self.buffer += c

        self.line += c
        if c == b'\n':
            self.logger.info('qWantOpen %s line=%s', self.qWantOpen, self.line)
            if self.qWantOpen: # Check if we should turn off?
                self.qWantOpen = self.triggerOff.search(self.line) is None
            else:
                self.qWantOpen =  self.triggerOn.search(self.line) is not None
            self.line = bytearray()
            self.logger.info('qWantOpen post %s', self.qWantOpen)

    def get(self, n:int) -> bytes:
        self.tLastAction = time.time()
        c = self.read(n)
        if not len(c): # Connection dropped
            self.close()
        self.logger.info('get n=%s c=%s', n, c)
        return c

    def inputFileno(self) -> int:
        if self.qWantOpen and (self.s is None):
            self.open()
        return self.s

    def outputFileno(self) -> int:
        if self.qWantOpen and (self.s is None):
            self.open()
        return self.s if len(self.buffer) and (time.time() >= self.tNextSend) else None


    def qOpen(self) -> bool:
        return self.s is not None

    def write(self, buffer:bytes) -> int:
        return 0 if self.s is None else self.s.send(buffer)

    def read(self, n:int) -> bytes:
        return b'' if self.s is None else self.s.recv(n)

    def close(self) -> None:
        self.qWantOpen = False # I don't want to be open
        if self.s is None:
            return

        logger = self.logger

        try: # Shutdown seems to hold the connection open????
            # s.shutdown(socket.SHUT_RDWR) # Shutdown the connection
            self.s.close() # Free up resources
            logger.info('Closed %s:%s', self.args.host, self.args.port)
        except:
            logger.exception('Error closing %s:%s', self.args.host, self.args.port)

        self.s = None
        now = time.time()
        self.tLastClose = now
        self.tNextOpen = max(self.tNextOpen, now + self.args.rudicsDelay)

    def open(self) -> None:
        if self.s is not None: # Already open
            return

        if time.time() < self.tNextOpen: # Don't open yet
            self.qWantOpen = True # We want to be open
            return

        args = self.args
        logger = self.logger
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(None) # Non-blocking
            s.connect((args.host, args.port)) # Connect to RUDICS listener on a Dockserver
            logger.info('Connected to %s:%s', args.host, args.port)
            self.s = s
            self.tLastOpen = time.time()
            self.qWantOpen = True # I'm now open
        except:
            self.tNextOpen = time.time() + args.rudicsDelay
            self.qWantOpen = True # We want to be open
            logger.exception('Unexpected error connecting to %s:%s, wait %s seconds to retry',
                    args.host, args.port, args.rudicsDelay)
