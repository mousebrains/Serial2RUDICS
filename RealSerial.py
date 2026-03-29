#
# Interface to a serial port which is talking to a TWR Slocum glider simulator
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import argparse
import logging
import serial

baudrates = serial.Serial.BAUDRATES

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

    def __del__(self) -> None: # Destructor
        try:
            logging.info('Destroying Serial %s', getattr(self, 'port', '?'))
            self.close()
        except Exception:
            pass

    def __bool__(self) -> bool:
        return (self.fp is not None) or bool(self.buffer)

    def inputFileno(self) -> serial.Serial | None:
        return self.fp

    def outputFileno(self) -> serial.Serial | None:
        return self.fp if self.buffer else None

    def send(self) -> None:
        if (self.fp is not None) and self.buffer:
            n = self.fp.write(self.buffer[:1]) # 1 at a time to not overload
            if n is not None and n > 0:
                self.buffer = self.buffer[n:]

    def put(self, c: bytes) -> None:
        self.buffer += c

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
            logging.exception('Exception while reading serial port')
            self.close()
            return b''
        except Exception:
            logging.exception('Unexpected exception while reading serial port')
            self.close()
            return b''
        else:
            return c

    def __open(self) -> None:
        args = self.args
        try:
            fp = serial.Serial(port=self.port, baudrate=args.baudrate,
                    bytesize=args.bytesize, parity=args.parity, stopbits=args.stopbits)
            self.fp = fp
            logging.info('Opened serial port %s parity=%s baudrate=%s bytesize=%s stopbits=%s',
                args.serial, args.parity, args.baudrate, args.bytesize, args.stopbits)

        except serial.serialutil.SerialException:
            logging.exception('Error opening serial port %s', self.port)
        except ValueError:
            logging.exception('Value error opening serial port %s', self.port)
        except Exception:
            logging.exception('Unexpected error opening serial port %s', self.port)

    def close(self) -> None:
        if self.fp is None:
            return

        try:
            self.fp.close()
            logging.info('Closed %s', self.port)
        except serial.serialutil.SerialException:
            logging.exception('Error closing serial port %s', self.port)
        except Exception:
            logging.exception('Unexpected error closing serial port %s', self.port)
        self.fp = None
