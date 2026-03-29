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
BINARY_SUPPRESS_SECS = 5.0  # Suppress trigger matching after binary data (file transfer)

# Bytes considered normal text: TAB, LF, CR, and printable ASCII (0x20-0x7E)
_TEXT_BYTES = frozenset({0x09, 0x0A, 0x0D} | set(range(0x20, 0x7F)))

class RUDICS:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.triggerOn = self.__mkTrigger(args.triggerOn,
                [
                    r'surface_\d+:.*Picking iridium or freewave',
                    r':\s+abort_the_mission',
                    ]
                )
        self.triggerOff = self.__mkTrigger(args.triggerOff,
                [
                    r'surface_\d+:.*Waiting\s+for\s+final\s+gps\s+fix',
                    ]
                )
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
        self.tLastBinary: float = 0  # Time of last binary data seen (file transfer)

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
            a = '|'.join(items)
        return re.compile(a.encode(), re.IGNORECASE)

    def timeout(self) -> float:
        now = time.time()
        idle_timeout: float = self.args.idleTimeout
        max_open_time: float = self.args.rudicsMaxOpenTime

        if self.tLastOpen > 0:
            # Time until idle timeout (measured from last activity or connection open)
            tRef = max(self.tLastOpen, self.tLastAction or 0)
            dt: float = max(1.0, idle_timeout - (now - tRef))
            # Time until max open time
            dt = min(dt, max(1.0, max_open_time - (now - self.tLastOpen)))
        else:
            dt = max(1.0, idle_timeout)

        # Wake up at tNextOpen to retry connection even if buffer is empty
        if self.qWantOpen and self.s is None and self.tNextOpen > now:
            dt = min(dt, max(1.0, self.tNextOpen - now))

        if not self.buffer:
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

    def send(self) -> None:
        logging.debug('RUDICS:send %s', len(self.buffer))
        now = time.time()

        if (self.s is None) or (not self.buffer) or (self.tNextSend >= now):
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
            m = self.write(self.buffer[:n])

        logging.debug('RUDICS:sent m=%s n=%s remaining=%s', m, n, len(self.buffer))

        self.buffer = self.buffer[m:]

        if m > 0:
            self.tLastSend = now

    @staticmethod
    def _hasBinaryData(data: bytes | bytearray) -> bool:
        """Check if data contains non-text bytes indicating a file transfer."""
        return any(b not in _TEXT_BYTES for b in data)

    def _inFileTransfer(self) -> bool:
        """True if binary data was recently seen, indicating a file transfer."""
        return (time.time() - self.tLastBinary) < BINARY_SUPPRESS_SECS

    def put(self, c: bytes) -> None:
        self.tLastAction = time.time()
        wasOpen = self.qWantOpen

        # Track binary data for file transfer detection
        if self._hasBinaryData(c):
            self.tLastBinary = time.time()

        self.line += c

        if len(self.line) > MAX_LINE_SIZE:
            logging.warning('Line buffer exceeded %s bytes, discarding %s bytes',
                    MAX_LINE_SIZE, len(self.line) - len(c))
            self.line = bytearray(c)

        if b'\n' in self.line:
            lines = self.line.split(b"\n")
            self.line = lines[-1]  # Keep incomplete tail

            inTransfer = self._inFileTransfer()

            for line in lines[:-1]:  # Process all complete lines
                line = line.rstrip(b'\r')
                if not line:
                    continue
                try:
                    msg = line.decode("utf-8")
                except Exception:
                    msg = repr(bytes(line))

                logging.info('qWantOpen %s line=%s', self.qWantOpen, msg.strip())
                if inTransfer:
                    continue  # Suppress trigger matching during file transfers
                if self.qWantOpen: # Check if we should turn off?
                    if self.triggerOff.search(line) is not None:
                        logging.info('triggerOff matched: %s', msg.strip())
                        self.qWantOpen = False
                        self.close()
                        self.buffer = bytearray()
                        wasOpen = False
                elif self.triggerOn.search(line) is not None:
                    logging.info('triggerOn matched: %s', msg.strip())
                    self.qWantOpen = True
                    self.open()

        # Buffer data after trigger detection so trigger-on chunks are captured
        if wasOpen or self.qWantOpen:
            if len(self.buffer) < MAX_BUFFER_SIZE:
                self.buffer += c
            else:
                logging.warning('Buffer full (%s bytes), discarding %s bytes',
                        len(self.buffer), len(c))

    def get(self, n: int) -> bytes:
        self.tLastAction = time.time()
        c = self.read(n)
        if not c and self.s is not None: # Connection dropped, not already handled by read()
            self.close()
            self.qWantOpen = True # Want to reconnect
        logging.info('get n=%s len=%s', n, len(c))
        return c

    def inputFileno(self) -> socket.socket | None:
        if self.qWantOpen and (self.s is None):
            self.open()
        return self.s

    def outputFileno(self) -> socket.socket | None:
        if self.qWantOpen and (self.s is None):
            self.open()
        return self.s if self.buffer and (time.time() >= self.tNextSend) else None

    def write(self, buffer: bytes | bytearray) -> int:
        try:
            if self.s is not None:
                return self.s.send(buffer)
        except Exception:
            logging.exception('Exception while writing %d bytes', len(buffer))
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

        try:
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
