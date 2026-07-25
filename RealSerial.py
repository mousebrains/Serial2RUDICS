#
# Interface to a serial port which is talking to a TWR Slocum glider simulator
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import argparse
import fcntl
import logging
import os

import serial

logger = logging.getLogger(__name__)

baudrates = serial.Serial.BAUDRATES

# Bytes per write attempt. With O_NONBLOCK and select-driven I/O the
# kernel writes what fits in its TTY buffer and returns the partial count;
# the rest waits for the next select wakeup. 4096 is the typical Linux
# TTY buffer size, so a single os.write usually drains everything pending.
WRITE_CHUNK_BYTES = 4096

class RealSerial:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.buffer = bytearray()
        self.port: str = args.serial
        self.fp: serial.Serial | None = None
        self.__open()

    @staticmethod
    def addArgs(parser: argparse.ArgumentParser) -> None:
        grp = parser.add_argument_group('Real Serial Port Options')
        grp.add_argument('--baudrate', type=int, choices=baudrates, default=115200,
                help='Serial port baudrate')
        grp.add_argument('--parity', type=str, choices=serial.Serial.PARITIES,
                default='N', help='Serial port parity')
        grp.add_argument('--bytesize', type=int, choices=serial.Serial.BYTESIZES,
                default=8, help='Bits/byte')
        grp.add_argument('--stopbits', type=float, choices=serial.Serial.STOPBITS,
                default=1, help='Number of stop bits')

    def __bool__(self) -> bool:
        return (self.fp is not None) or bool(self.buffer)

    def inputFileno(self) -> serial.Serial | None:
        return self.fp

    def outputFileno(self) -> serial.Serial | None:
        return self.fp if self.buffer else None

    def send(self) -> None:
        if (self.fp is None) or (not self.buffer):
            return
        try:
            n = os.write(self.fp.fileno(), bytes(self.buffer[:WRITE_CHUNK_BYTES]))
        except BlockingIOError:
            return # Kernel TTY buffer full; retry on next select wakeup
        except OSError:
            logger.exception('Error writing serial port %s', self.port)
            self.close()
            return
        if n > 0:
            del self.buffer[:n]

    def put(self, c: bytes) -> None:
        # Bounded like RUDICS.put(): a wedged or slow TTY must not let the
        # dockserver->glider direction grow until the MemoryMax kill.
        if len(self.buffer) < self.args.maxBuffer:
            self.buffer += c
        else:
            logger.warning('Serial buffer full (%s bytes), discarding %s bytes',
                    len(self.buffer), len(c))

    def nAvailable(self) -> int:
        return self.fp.in_waiting if self.fp else 0

    def get(self, n: int) -> bytes:
        if self.fp is None or n <= 0:
            return b''
        try:
            c: bytes = self.fp.read(n)
            if not c: # EOF
                self.close()
        except serial.serialutil.SerialException:
            logger.exception('Exception while reading serial port')
            self.close()
            return b''
        except Exception:
            logger.exception('Unexpected exception while reading serial port')
            self.close()
            return b''
        else:
            return c

    def __open(self) -> None:
        args = self.args
        try:
            fp = serial.Serial(port=self.port, baudrate=args.baudrate,
                    bytesize=args.bytesize, parity=args.parity, stopbits=args.stopbits)
            # Non-blocking writes: os.write returns partial counts immediately
            # instead of blocking on a slow baudrate. Required to honor the
            # 100ms serial->RUDICS latency budget shared with the reverse path.
            flags = fcntl.fcntl(fp.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(fp.fileno(), fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self.fp = fp
            logger.info('Opened serial port %s parity=%s baudrate=%s bytesize=%s stopbits=%s',
                args.serial, args.parity, args.baudrate, args.bytesize, args.stopbits)

        except serial.serialutil.SerialException:
            logger.exception('Error opening serial port %s', self.port)
        except ValueError:
            logger.exception('Value error opening serial port %s', self.port)
        except Exception:
            logger.exception('Unexpected error opening serial port %s', self.port)

    def close(self) -> None:
        if self.fp is None:
            return

        try:
            self.fp.close()
            logger.info('Closed %s', self.port)
        except serial.serialutil.SerialException:
            logger.exception('Error closing serial port %s', self.port)
        except Exception:
            logger.exception('Unexpected error closing serial port %s', self.port)
        self.fp = None
        # Nothing can ever drain a closed port, and __bool__ counts a non-empty
        # buffer as "still alive" -- leaving it would keep doit()'s
        # `while serial or rudics` loop running forever with no work to do and
        # no way for systemd to notice and restart us.
        if self.buffer:
            logger.warning('Discarding %s undeliverable bytes queued for %s',
                    len(self.buffer), self.port)
            self.buffer.clear()
