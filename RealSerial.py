#
# Interface to a serial port which is talking to a TWR Slocum glider simulator
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import argparse
import logging
import serial

baudrates = serial.Serial.BAUDRATES

class RealSerial:
    def __init__(self, args:argparse.ArgumentParser, logger:logging.Logger) -> None:
        self.args = args
        self.logger = logger
        self.buffer = bytearray()
        self.__open()

    @staticmethod
    def addArgs(parser:argparse.ArgumentParser) -> None:
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
        self.logger.info('Destroying Serial %s', self.port)
        self.close()

    def __bool__(self) -> bool:
        return (self.fp is not None) or bool(len(self.buffer))

    def inputFileno(self) -> serial.Serial: 
        return self.fp

    def outputFileno(self) -> serial.Serial: 
        return self.fp if len(self.buffer) else None

    def exceptionFileno(self) -> serial.Serial:
        return self.fp

    def send(self) -> None:
        if (self.fp is not None) and len(self.buffer):
            n = self.fp.write(self.buffer[0:1]) # 1 at a time to not overload
            self.buffer = self.buffer[n:]

    def put(self, c:bytes) -> None:
        self.buffer += c

    def get(self, n:int) -> bytes:
        if self.fp is None:
            return b''
        try:
            c = self.fp.read(n)
            if len(c) == 0: # EOF
                self.close()
            return c
        except serial.serialutil.SerialException as e:
            self.logger.error('Unexpected exception while reading serial port, %s', str(e))
        except:
            self.logger.exception('Unexpected exception while reading serial port')

        self.close()
        return b''

    def __open(self) -> None:
        args = self.args
        self.port = args.serial
        self.fp = None
        try:
            fp = serial.Serial(port=self.port, baudrate=args.baudrate, 
                    bytesize=args.bytesize, parity=args.parity, stopbits=args.stopbits)
            self.fp = fp
            self.logger.info('Opened serial port %s parity=%s baudrate=%s bytesize=%s stopbits=%s',
                args.serial, args.parity, args.baudrate, args.bytesize, args.stopbits)

        except serial.serialutil.SerialException:
            self.logger.exception('Error opening serial port %s', self.port)
        except ValueError:
            self.logger.exception('Value error opening serial port %s', self.port)
        except:
            self.logger.exception('Unexpected error opening serial port %s', self.port)

    def close(self) -> None:
        if self.fp is None: 
            return

        try:
            self.fp.close()
            self.logger.info('Closed %s', self.port)
        except serial.serialutil.SerialException:
            self.logger.exception('Error closing serial port %s', self.port)
        except:
            self.logger.exception('Unexpected error closing serial port %s', self.port)
        self.fp = None
