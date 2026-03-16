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

MAX_BUFFER_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_LINE_SIZE = 1024 * 1024  # 1 MB

class RUDICS:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.triggerOn = self.__mkTrigger(args.triggerOn,
                [
                    r'behavior surface_\d+:\s+SUBSTATE \d+ ->\d+ : Picking iridium or freewave',
                    r':\s+abort_the_mission',
                    ]
                )
                    # , r'init_gps_input[(][)]'
                    # , r'end_gps_input[(][)]'
        self.triggerOff = self.__mkTrigger(args.triggerOff,
                [
                    r'surface_\d+:\s+.*Waiting\s+for\s+final\s+GPS\s+fix',
                    ]
                )
                    # r'behavior dive_to_\d+:\s+SUBSTATE \d+ ->\d+ : diving',
        self.secondsPerByte: float | None = \
                None if (args.rudicsBaudrate is None) or (args.rudicsBaudrate < 1) \
                else (9 / args.rudicsBaudrate) # Time to send 9 bits
        self.buffer = bytearray()
        self.line = bytearray()
        self.tLastOpen: float = 0
        self.tLastClose: float = 0
        self.tLastSend: float = 0
        self.tNextSend: float = 0
        self.tNextOpen: float = 0
        self.tLastAction: float | None = None
        self.qWantOpen = not args.disconnected # Initially connection state
        self.s: socket.socket | None = None

    @staticmethod
    def addArgs(parser: argparse.ArgumentParser) -> None:
        grp = parser.add_argument_group('RUDICS Trigger on/off Options')
        grp.add_argument('--triggerOff', action='append',
                help='Shutdown Dockserver connection after this line seen')
        grp.add_argument('--triggerOn', action='append',
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
                help="Delay between retries at connecting to the RUDICS port")
        grp.add_argument('--rudicsMaxOpenTime', type=int, default=86400,
                help="Maximum length of time a single RUDICS connection can be open")
        grp.add_argument('--rudicsMaxOpenTimeDelay', type=int, default=1800,
                help="Time after a forced RUDICS disconnect until reopening")
        grp.add_argument('--connectTimeout', type=float, default=10,
                help="Timeout in seconds for connecting to the RUDICS port")

        grp.add_argument('--disconnected', action='store_true',
                help='Should the initial state be disconnected?')

    def __del__(self) -> None: # Destructor
        try:
            logging.info('Destroying RUDICS')
            self.close()
        except Exception:
            pass

    def __bool__(self) -> bool:
        return (self.s is not None) or self.qWantOpen

    def __mkTrigger(self, items: list[str] | None, defaults: list[str]) -> re.Pattern[bytes]:
        # If items has only one item, then that is the pattern
        if not items:
            items = defaults
        if len(items) == 1:
            a = items[0]
        else:
            a = '(' + '|'.join(items) + ')'
        return re.compile(bytes(a, 'utf-8'), re.IGNORECASE)

    def timeout(self) -> float:
        now = time.time()

        if self.tLastOpen > 0:
            # Time until idle timeout (measured from last activity or connection open)
            tRef = max(self.tLastOpen, self.tLastAction or 0)
            dt = max(1, self.args.idleTimeout - (now - tRef))
            # Time until max open time
            dt = min(dt, max(1, self.args.rudicsMaxOpenTime - (now - self.tLastOpen)))
        else:
            dt = max(1, self.args.idleTimeout)

        # Wake up at tNextOpen to retry connection even if buffer is empty
        if self.qWantOpen and self.s is None and self.tNextOpen > now:
            dt = min(dt, max(1, self.tNextOpen - now))

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
        if self.tLastOpen <= 0:
            return
        now = time.time()

        # Enforce max open time
        if (now - self.tLastOpen) >= self.args.rudicsMaxOpenTime:
            logging.info('Max open time exceeded')
            self.close()
            self.tNextOpen = max(self.tNextOpen, now + self.args.rudicsMaxOpenTimeDelay)
            self.qWantOpen = True
            return

        # Idle timeout: time since last activity or connection open
        tRef = max(self.tLastOpen, self.tLastAction or 0)
        if (now - tRef) >= self.args.idleTimeout:
            logging.info('Idle timeout')
            self.close()
            self.tLastAction = now

    def send(self) -> None:
        logging.debug('RUDICS:send %s', len(self.buffer))
        now = time.time()

        if (self.s is None) or (not len(self.buffer)) or (self.tNextSend >= now):
            return

        if self.secondsPerByte is None: # Not baudrate limited
            n = len(self.buffer) # Send whole buffer
        else: # baudrate limited
            self.tNextSend = now + self.secondsPerByte
            dt = now - self.tLastSend # Time since the last send
            n = math.floor(dt / self.secondsPerByte) # How many bytes can be sent
            if n <= 0:
                return

        if n >= len(self.buffer):
            m = self.write(self.buffer)
        else:
            m = self.write(self.buffer[0:n])

        logging.debug('RUDICS:sent full buffer m=%s n=%s len=%s buffer=%s',
                m, n, len(self.buffer), self.buffer)

        self.buffer = self.buffer[m:]

        if m > 0:
            self.tLastSend = now

    def put(self, c: bytes) -> None:
        self.tLastAction = time.time()
        wasOpen = self.qWantOpen

        self.line += c

        if len(self.line) > MAX_LINE_SIZE:
            logging.warning('Line buffer exceeded %s bytes, discarding', MAX_LINE_SIZE)
            self.line = bytearray(c)

        if b'\n' in self.line:
            lines = self.line.split(b"\n")
            self.line = lines[-1]  # Keep incomplete tail

            for line in lines[:-1]:  # Process all complete lines
                line = line.rstrip(b'\r')
                if not line:
                    continue
                try:
                    msg = str(line, "utf-8")
                except Exception:
                    msg = repr(bytes(line))

                logging.info('qWantOpen %s line=%s', self.qWantOpen, msg.strip())
                if self.qWantOpen: # Check if we should turn off?
                    if self.triggerOff.search(line) is not None:
                        self.qWantOpen = False
                        self.close()
                        self.buffer = bytearray()
                        wasOpen = False
                else:
                    if self.triggerOn.search(line) is not None:
                        self.qWantOpen = True
                        self.open()

        # Buffer data after trigger detection so trigger-on chunks are captured
        if (wasOpen or self.qWantOpen) and len(self.buffer) < MAX_BUFFER_SIZE:
            self.buffer += c

    def get(self, n: int) -> bytes:
        self.tLastAction = time.time()
        c = self.read(n)
        if not len(c) and self.s is not None: # Connection dropped, not already handled by read()
            self.close()
            self.qWantOpen = True # Want to reconnect
        logging.info('get n=%s c=%s', n, c)
        return c

    def inputFileno(self) -> socket.socket | None:
        if self.qWantOpen and (self.s is None):
            self.open()
        return self.s

    def outputFileno(self) -> socket.socket | None:
        if self.qWantOpen and (self.s is None):
            self.open()
        return self.s if len(self.buffer) and (time.time() >= self.tNextSend) else None

    def write(self, buffer: bytes) -> int:
        try:
            if self.s is not None:
                return self.s.send(buffer)
        except Exception:
            logging.exception('Exception while writing %s', buffer)
            self.close()
            self.qWantOpen = True
        return 0

    def read(self, n: int) -> bytes:
        try:
            if self.s is not None:
                return self.s.recv(n)
        except Exception:
            logging.exception('Exception while receiving %s', n)
            self.close()
            self.qWantOpen = True
        return b''

    def close(self) -> None:
        self.qWantOpen = False # I don't want to be open
        if self.s is None:
            return

        try: # Shutdown seems to hold the connection open????
            # s.shutdown(socket.SHUT_RDWR) # Shutdown the connection
            self.s.close() # Free up resources
            logging.info('Closed %s:%s', self.args.host, self.args.port)
        except Exception:
            logging.exception('Error closing %s:%s', self.args.host, self.args.port)

        self.s = None
        self.tLastOpen = 0
        now = time.time()
        self.tLastClose = now
        self.tNextOpen = max(self.tNextOpen, now + self.args.rudicsSpacing)

    def open(self) -> None:
        if self.s is not None: # Already open
            return

        if time.time() < self.tNextOpen: # Don't open yet
            self.qWantOpen = True # We want to be open
            return

        args = self.args
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(args.connectTimeout) # Bounded connect timeout
            s.connect((args.host, args.port)) # Connect to RUDICS listener on a Dockserver
            s.settimeout(None) # Blocking for normal I/O
            logging.info('Connected to %s:%s', args.host, args.port)
            self.s = s
            self.tLastOpen = time.time()
            self.qWantOpen = True # I'm now open
        except Exception:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
            self.tNextOpen = time.time() + args.rudicsDelay
            self.qWantOpen = True # We want to be open
            logging.exception('Unexpected error connecting to %s:%s, wait %s seconds to retry',
                    args.host, args.port, args.rudicsDelay)
