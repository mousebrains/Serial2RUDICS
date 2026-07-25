#
# Connect to a dockserver through a socket connection
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import argparse
import logging
import math
import re
import socket
import time

from RealSerial import baudrates

logger = logging.getLogger(__name__)

MAX_BUFFER_SIZE = 10 * 1024 * 1024  # 10 MB (default; override via --maxBuffer)
MAX_LINE_SIZE = 1024 * 1024  # 1 MB
BINARY_SESSION_SECS = 30.0  # How long after last binary data to consider zmodem session over
# Bytes handed to a single socket send. The socket stays non-blocking, so a
# stalled dockserver returns EWOULDBLOCK instead of parking the select loop;
# the cap bounds how long one writable-wakeup can spend copying to the kernel.
WRITE_CHUNK_BYTES = 64 * 1024

# Bytes considered normal text: TAB, LF, CR, and printable ASCII (0x20-0x7E)
_TEXT_BYTES = frozenset({0x09, 0x0A, 0x0D} | set(range(0x20, 0x7F)))
# Deletion set for bytes.translate-based binary detection (~50-100x faster
# than a Python-level loop on multi-KB chunks).
_TEXT_BYTES_AS_BYTES = bytes(sorted(_TEXT_BYTES))

# Patterns for detecting file display and GliderDos context
_TYPE_CAT_CMD = re.compile(rb'>\s*(?:type|cat)\s+\S+', re.IGNORECASE)
_GLIDERDOS_PROMPT = re.compile(rb'^GliderDos\s+', re.IGNORECASE)
_LOG_FILE_CLOSED = re.compile(rb'\.mlg\s+LOG\s+FILE\s+CLOSED', re.IGNORECASE)

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
        # None until put() sees the first byte; bare 0 sentinel would clash with
        # the small time.monotonic() values seen on freshly-booted CI runners.
        self.tLastSerialAction: float | None = None
        self.qWantOpen = not args.disconnected # Initially connection state
        # True when close() dropped intent purely because serial had gone
        # quiet. The next serial byte disproves that premise and re-arms
        # intent; without this the link stays down for the rest of the
        # surfacing, since triggerOn fires only once per surfacing.
        self.qIdleDemoted: bool = False
        # Set by read() when recv() reports EWOULDBLOCK so get() can tell a
        # spurious wakeup apart from a real EOF and not close a live socket.
        self.qWouldBlock: bool = False
        self.s: socket.socket | None = None
        # Init to -inf so _inBinarySession returns False before any binary
        # bytes are seen. (time.monotonic() at process start is small, so a
        # naive 0 sentinel would falsely flag the first 30s as in-session.)
        self.tLastBinary: float = float('-inf')
        self.qTypeCat: bool = False  # Suppression flag for type/cat file display
        self.qConnecting: bool = False  # Non-blocking connect in progress
        self.tConnectStarted: float = 0  # When non-blocking connect was initiated

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
        grp.add_argument('--reconnectMaxSerialIdle', type=float, default=600,
                help="Stop reconnecting if no serial data seen for this many seconds. "
                     "Bounds connection flapping when the glider is silent. "
                     "Set longer than the SFMC server-side idle reaper (~5 min).")
        grp.add_argument('--connectTimeout', type=float, default=10,
                help="Timeout in seconds for connecting to the RUDICS port")
        grp.add_argument('--maxBuffer', type=int, default=MAX_BUFFER_SIZE,
                help='Max bytes buffered while waiting to send to RUDICS '
                     '(lower on tight-memory hosts like Pi 3B running 10 ports)')

        grp.add_argument('--disconnected', action='store_true',
                help='Should the initial state be disconnected?')

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
        now = time.monotonic()
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

        # While connecting, wake up at the connect deadline so timedOut can abort.
        if self.qConnecting:
            remaining = self.args.connectTimeout - (now - self.tConnectStarted)
            dt = min(dt, max(0.1, remaining))

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
        # Abort a non-blocking connect that overran its budget so the main
        # select loop never blocks past connectTimeout.
        if self.qConnecting:
            now = time.monotonic()
            if (now - self.tConnectStarted) >= self.args.connectTimeout:
                logger.warning('Connect timeout to %s:%s, retry in %s seconds',
                        self.args.host, self.args.port, self.args.rudicsDelay)
                self._abandonConnect()
            return

        if self.tLastOpen <= 0:
            return
        now = time.monotonic()

        # Enforce max open time
        if (now - self.tLastOpen) >= self.args.rudicsMaxOpenTime:
            logger.info('Max open time exceeded')
            self.close()
            self.tNextOpen = max(self.tNextOpen, now + self.args.rudicsMaxOpenTimeDelay)
            self.qWantOpen = True
            return

        # Idle timeout: time since last activity or connection open
        tRef = max(self.tLastOpen, self.tLastAction or 0)
        if (now - tRef) >= self.args.idleTimeout:
            logger.info('Idle timeout')
            self.close()

    def send(self) -> None:
        # Writability on the socket during qConnecting is the kernel's
        # "connect complete" signal; finalize/abandon before any data flow.
        if self.qConnecting:
            self._checkConnect()
            return

        logger.debug('RUDICS:send %s', len(self.buffer))
        now = time.monotonic()

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

        logger.debug('RUDICS:sent m=%s n=%s remaining=%s', m, n, len(self.buffer))

        if m > 0:
            del self.buffer[:m]
            self.tLastSend = now

    @staticmethod
    def _hasBinaryData(data: bytes | bytearray) -> bool:
        """Check if data contains non-text bytes indicating a file transfer."""
        return bool(bytes(data).translate(None, _TEXT_BYTES_AS_BYTES))

    def _inBinarySession(self) -> bool:
        """True if binary data was recently seen, indicating an active zmodem session."""
        return (time.monotonic() - self.tLastBinary) < BINARY_SESSION_SECS

    def put(self, c: bytes) -> None:
        now = time.monotonic()
        self.tLastAction = now
        self.tLastSerialAction = now

        if self.qIdleDemoted:
            # close() dropped intent because the glider had gone quiet. This
            # byte proves it is talking again, so restore intent rather than
            # leaving the pilot with no link until the next surfacing.
            logger.info('Serial data after idle demotion, restoring RUDICS intent')
            self.qWantOpen = True
            self.qIdleDemoted = False

        wasOpen = self.qWantOpen

        # Track binary data for zmodem session detection
        if self._hasBinaryData(c):
            self.tLastBinary = time.monotonic()

        self.line += c

        if len(self.line) > MAX_LINE_SIZE:
            logger.warning('Line buffer exceeded %s bytes, discarding %s bytes',
                    MAX_LINE_SIZE, len(self.line) - len(c))
            self.line = bytearray(c)

        if b'\n' in self.line:
            lines = self.line.split(b"\n")
            self.line = lines[-1]  # Keep incomplete tail

            inBinarySession = self._inBinarySession()

            for line in lines[:-1]:  # Process all complete lines
                line = line.rstrip(b'\r')
                if not line:
                    continue
                try:
                    msg = line.decode("utf-8")
                except UnicodeDecodeError:
                    msg = repr(bytes(line))

                logger.info('qWantOpen %s line=%s', self.qWantOpen, msg.strip())

                # Detect type/cat command — set suppression flag
                if _TYPE_CAT_CMD.search(line):
                    self.qTypeCat = True

                # Clear type/cat flag on GliderDos prompt or LOG FILE CLOSED
                if self.qTypeCat and (
                        (_GLIDERDOS_PROMPT.search(line) and not _TYPE_CAT_CMD.search(line))
                        or _LOG_FILE_CLOSED.search(line)):
                    self.qTypeCat = False

                # Determine whether to suppress trigger matching:
                # 1. Line itself contains binary data (corrupted by zmodem framing)
                # 2. type/cat command is displaying a file
                # 3. Active zmodem session (binary data seen recently)
                suppress = (
                    self._hasBinaryData(line)
                    or self.qTypeCat
                    or inBinarySession
                )

                if suppress:
                    continue

                if self.qWantOpen: # Check if we should turn off?
                    if self.triggerOff.search(line) is not None:
                        logger.info('triggerOff matched: %s', msg.strip())
                        self.qWantOpen = False
                        # Deliberate shutdown, not an idle demotion: stay down
                        # until the next triggerOn, whatever the glider says.
                        self.qIdleDemoted = False
                        self.close()
                        self.buffer = bytearray()
                        wasOpen = False
                elif self.triggerOn.search(line) is not None:
                    logger.info('triggerOn matched: %s', msg.strip())
                    self.qWantOpen = True
                    self.open()

        # Buffer data after trigger detection so trigger-on chunks are captured
        if wasOpen or self.qWantOpen:
            if len(self.buffer) < self.args.maxBuffer:
                self.buffer += c
            else:
                logger.warning('Buffer full (%s bytes), discarding %s bytes',
                        len(self.buffer), len(c))

    def get(self, n: int) -> bytes:
        # Readability on the socket during qConnecting indicates either
        # error or a fast-path connect+payload; route through _checkConnect.
        if self.qConnecting:
            self._checkConnect()
            return b''
        c = self.read(n)
        if not c and self.s is not None and not self.qWouldBlock:
            self.close() # Connection dropped, not already handled by read()
        logger.debug('get n=%s len=%s', n, len(c))
        return c

    def inputFileno(self) -> socket.socket | None:
        if self.qWantOpen and (self.s is None) and not self.qConnecting:
            self.open()
        return self.s

    def outputFileno(self) -> socket.socket | None:
        if self.qWantOpen and (self.s is None) and not self.qConnecting:
            self.open()
        # During qConnecting we want select to wake on writability so
        # _checkConnect can finalize/abandon the connect.
        if self.qConnecting:
            return self.s
        return self.s if self.buffer and (time.monotonic() >= self.tNextSend) else None

    def write(self, buffer: bytes | bytearray) -> int:
        try:
            if self.s is not None:
                # Capped: send() applies short writes via del self.buffer[:m],
                # so a partial write costs one extra select wakeup, not data.
                return self.s.send(buffer[:WRITE_CHUNK_BYTES])
        except BlockingIOError:
            # Kernel socket buffer full. Not an error: the bytes stay queued
            # and outputFileno() keeps selecting for writability.
            return 0
        except Exception:
            logger.exception('Exception while writing %d bytes', len(buffer))
            self.close()
        return 0

    def read(self, n: int) -> bytes:
        self.qWouldBlock = False
        try:
            if self.s is not None:
                return self.s.recv(n)
        except BlockingIOError:
            # Readable per select but nothing to take. Flag it so get() does
            # not mistake the empty result for a peer disconnect.
            self.qWouldBlock = True
            return b''
        except Exception:
            logger.exception('Exception while receiving %s', n)
            self.close()
        return b''

    def close(self) -> None:
        # qWantOpen is intent. triggerOn/triggerOff are the primary controls,
        # but close() also demotes intent to False when serial has been silent
        # for longer than reconnectMaxSerialIdle (or never seen) — that caps
        # flapping when the glider stops talking, without losing reconnects
        # during normal SFMC ~5-min idle reaps.
        if self.tLastSerialAction is None or \
                (time.monotonic() - self.tLastSerialAction) > self.args.reconnectMaxSerialIdle:
            self.qWantOpen = False
            # Mark it re-armable: the premise ("glider has gone quiet") is
            # evaluated once, here, and only put() can disprove it later.
            self.qIdleDemoted = True
        self.qTypeCat = False  # Clear type/cat suppression on disconnect
        self.qConnecting = False  # Cancel any in-flight connect
        if self.s is None:
            return

        try:
            # Half-close the write side so the kernel flushes queued bytes
            # and sends FIN to the dockserver, instead of dropping them on
            # a bare close().
            try:
                self.s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass # Peer may already be gone; close still releases the fd
            self.s.close() # Free up resources
            logger.info('Closed %s:%s', self.args.host, self.args.port)
        except Exception:
            logger.exception('Error closing %s:%s', self.args.host, self.args.port)

        self.s = None
        self.tLastOpen = 0
        now = time.monotonic()
        self.tLastClose = now
        self.tNextOpen = max(self.tNextOpen, now + self.args.rudicsSpacing)

    def open(self) -> None:
        if self.s is not None: # Already open or already connecting
            return
        if self.qConnecting:
            return

        if time.monotonic() < self.tNextOpen: # Don't open yet
            self.qWantOpen = True # We want to be open
            return

        args = self.args
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False) # Non-blocking connect; never stalls the main loop
            try:
                s.connect((args.host, args.port))
            except BlockingIOError:
                # Linux: connect always reports EINPROGRESS on a non-blocking
                # socket; completion arrives later as socket writability.
                self.s = s
                self.qConnecting = True
                self.tConnectStarted = time.monotonic()
                self.qWantOpen = True
                logger.info('Connecting to %s:%s', args.host, args.port)
                return
            # Immediate completion (e.g. localhost): finalize now.
            self._finalizeConnect(s)
        except OSError:
            if s is not None:
                try:
                    s.close()
                except OSError as e:
                    # Already-dead fd; the retry below is what matters.
                    logger.debug('Ignoring close error on failed connect: %s', e)
            self.tNextOpen = time.monotonic() + args.rudicsDelay
            self.qWantOpen = True
            logger.exception('Unexpected error connecting to %s:%s, wait %s seconds to retry',
                    args.host, args.port, args.rudicsDelay)

    def _finalizeConnect(self, s: socket.socket) -> None:
        """Apply TCP options and transition from connecting to open."""
        # Stay non-blocking. A blocking socket parks the whole select loop
        # inside send() whenever the dockserver stops draining -- serial input
        # goes unread for as long as that lasts, and the glider's bytes are
        # lost. write()/read() handle EWOULDBLOCK instead.
        s.setblocking(False)
        # Disable Nagle: small serial-fragment writes should hit the
        # wire immediately to honor the 100ms serial->RUDICS budget.
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Keepalive so a half-open connection (NAT drop, dead server) is
        # detected within ~150s instead of waiting for idleTimeout.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, 'TCP_KEEPIDLE'):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
        if hasattr(socket, 'TCP_KEEPINTVL'):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 30)
        if hasattr(socket, 'TCP_KEEPCNT'):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        self.s = s
        self.qConnecting = False
        self.tLastOpen = time.monotonic()
        self.qWantOpen = True
        logger.info('Connected to %s:%s', self.args.host, self.args.port)

    def _abandonConnect(self) -> None:
        """Tear down an in-progress connect and schedule a retry."""
        if self.s is not None:
            try:
                self.s.close()
            except OSError as e:
                # Already-dead fd; we are abandoning it either way.
                logger.debug('Ignoring close error on abandoned connect: %s', e)
            self.s = None
        self.qConnecting = False
        self.tNextOpen = time.monotonic() + self.args.rudicsDelay
        self.qWantOpen = True

    def _checkConnect(self) -> None:
        """Resolve an in-flight non-blocking connect via SO_ERROR."""
        if not self.qConnecting or self.s is None:
            return
        try:
            err = self.s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        except OSError as e:
            err = e.errno or -1
        if err == 0:
            self._finalizeConnect(self.s)
        else:
            logger.warning('Connect failed to %s:%s: errno %s, retry in %s seconds',
                    self.args.host, self.args.port, err, self.args.rudicsDelay)
            self._abandonConnect()
