#
# Use a pseudo tty and a thread to emulate
# a serial device
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import pty
import os
import threading
import argparse
import logging
import select
from io import FileIO, BufferedWriter
from typing import Any

fauxSerial = None

def addArgs(parser: argparse.ArgumentParser) -> None:
    ''' Add my command line arguments '''
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument('--serial', type=str, help='Serial port to open to')
    grp.add_argument('--input', type=str,
            help='File to read as if coming from a serial device')

    optgrp = parser.add_argument_group(description='Serial simulation related options')
    optgrp.add_argument('--output', type=str,  default='/dev/null',
            help='File to write serial output to')

def setup(args: argparse.Namespace) -> str:
    ''' Return the serial device name to use, and start a ptty/thread if needed '''
    global fauxSerial
    if args.serial is not None:
        serial_port: str = args.serial
        return serial_port
    fauxSerial = FauxSerial(args)
    fauxSerial.start()
    return fauxSerial.port

class FauxSerial(threading.Thread):
    ''' Create a pseudo-tty and read from a file and send to the ptty and the inverse '''
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(daemon=True)
        self.args = args
        (self.master, slave) = pty.openpty() # Create a pseudo-tty pair
        self.port = os.ttyname(slave)
        os.close(slave) # Close our copy; device remains accessible via master

    def run(self) -> None: # Called on start
        try:
            self.runMain()
        except Exception:
            logging.exception('Exception in FauxSerial')

    def runMain(self) -> None:
        args = self.args
        master: int | None = self.master

        ifn = args.input
        ofn = args.output

        qMagic = ofn == '/dev/null'

        ifp: FileIO | None = open(ifn, 'rb', buffering=0)
        ofp: BufferedWriter | None = open(ofn, 'wb')

        logging.info('FauxSerial opened %s for input', ifn)
        logging.info('FauxSerial opened %s for output', ofn)

        maxSize = 65536 # Maximum length of internal buffers
        toSerial = bytearray() # Buffer to send to pseudo-tty
        toFile = bytearray() # Buffer to send to the file

        try:
            while True:
                dtExtra = None # Time to wait before closing the master device

                inputs: list[Any] = []
                outputs: list[Any] = []
                exceptables: list[Any] = []

                if master is not None:
                    exceptables.append(master)
                    if len(toFile) < maxSize:
                        inputs.append(master)
                    if toSerial:
                        outputs.append(master)
                elif not toFile: # Master is None and nothing left to write to file, so close ofp
                    if ofp is not None:
                        ofp.close()
                    ofp = None
                    logging.info('FauxSerial closing output, %s, since master is None', ofn)
                    break

                if ifp is not None:
                    if len(toSerial) < maxSize:
                        inputs.append(ifp)
                elif qMagic and not toSerial: # Nothing left to send to master
                    dtExtra = 10 # Wait 10 seconds for additional input from master

                if (ofp is not None) and toFile:
                    outputs.append(ofp)

                (ifps, ofps, efps) = select.select(inputs, outputs, exceptables, dtExtra)

                if not ifps and not ofps and not efps: # Timeout
                    logging.info('FauxSerial shutting down due to timeout')
                    if master is not None:
                        os.close(master)
                        master = None
                    if ifp is not None:
                        ifp.close()
                        ifp = None
                    if ofp is not None:
                        ofp.close()
                        ofp = None
                    break

                for fp in efps:
                    os.close(fp) # Close the master on exception
                    master = None
                    logging.info('FauxSerial Closing master PTY, %s', fp)

                if efps:
                    continue

                for fp in ifps:
                    if isinstance(fp, int): # read from master
                        try:
                            toFile += os.read(fp, 1)
                        except OSError:
                            logging.info('FauxSerial master read error, closing PTY')
                            os.close(fp)
                            master = None
                    else:
                        c = fp.read(1)
                        if c == b'': # EOF
                            if ifp is not None:
                                ifp.close()
                            ifp = None
                            logging.info('FauxSerial Closed %s', ifn)
                        else:
                            toSerial += c

                for fp in ofps:
                    if isinstance(fp, int): # write to master
                        if master is None:
                            continue
                        try:
                            n = os.write(fp, toSerial)
                            toSerial = toSerial[n:]
                        except OSError:
                            logging.info('FauxSerial master write error, closing PTY')
                            os.close(fp)
                            master = None
                    else:
                        n = fp.write(toFile)
                        fp.flush()
                        toFile = toFile[n:]

            logging.info('FauxSerial Fell out of while loop')
        finally:
            if master is not None:
                os.close(master)
            if ifp is not None:
                ifp.close()
            if ofp is not None:
                ofp.close()
