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

fauxSerial = None

def addArgs(parser:argparse.ArgumentParser) -> None:
    ''' Add my command line arguments '''
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument('--serial', type=str, help='Serial port to open to')
    grp.add_argument('--input', type=str,
            help='File to read as if coming from a serial device')

    grp = parser.add_argument_group(description='Serial simulation related options')
    grp.add_argument('--output', type=str,  default='/dev/null',
            help='File to write serial output to')

def setup(args:argparse.ArgumentParser, logger:logging.Logger) -> None:
    ''' Return the serial device name to use, and start a ptty/thread if needed '''
    if args.serial is not None:
        return args.serial
    fauxSerial = FauxSerial(args, logger)
    fauxSerial.start()
    return fauxSerial.port

class FauxSerial(threading.Thread):
    ''' Create a psuedo-tty and read from a file and send to the ptty and the inverse '''
    def __init__(self, args:argparse.ArgumentParser, logger:logging.Logger) -> None:
        threading.Thread.__init__(self, daemon=True)
        self.args = args
        self.logger = logger
        (self.master, self.slave) = pty.openpty() # Create a pseudo-tty pair
        self.port = os.ttyname(self.slave)

    def run(self) -> None: # Called on start
        try:
            self.runMain()
        except:
            self.logger.exception('Exception in FauxSerial')
    
    def runMain(self) -> None:
        args = self.args
        logger = self.logger
        master = self.master

        ifn = args.input
        ofn = args.output

        qMagic = ofn == '/dev/null'

        ifp = open(ifn, 'rb')
        ofp = open(ofn, 'wb')

        logger.info('FauxSerial opened %s for input', ifn)
        logger.info('FauxSerial opened %s for output', ofn)

        maxSize = 65536 # Maximum length of internal buffers
        toSerial = bytearray() # Buffer to send to psuedo-tty
        toFile = bytearray() # Buffer to send to the file

        dtExtra = None # Time to wait before closing the master device

        while True:
            inputs = []
            outputs = []
            exceptables = []

            if master is not None:
                exceptables.append(master)
                if len(toFile) < maxSize:
                    inputs.append(master)
                if len(toSerial) > 0:
                    outputs.append(master)
            elif not len(toFile): # Master is None and nothing left to write to file, so close ofp
                ofp.close()
                ofp = None
                logger.info('FauxSerial closing output, %s, since master is None', ofn)
                break

            if ifp is not None:
                if len(toSerial) < maxSize:
                    inputs.append(ifp)
            elif qMagic and not len(toSerial): # Nothing left to send to master
                dtExtra = 10 # Wait 10 seconds for additional input from master

            if (ofp is not None) and (len(toFile) > 0): outputs.append(ofp)

            (ifps, ofps, efps) = select.select(inputs, outputs, exceptables, dtExtra)

            if not ifps and not ofps and not efps: # Timeout
                logger.info('FauxSerial shutting down due to timeout')
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
                logger.info('FauxSerial Closing master PTY, %s', fp)

            for fp in ifps:
                if isinstance(fp, int): # read from master
                    toFile += os.read(fp, 1)
                else:
                    c = fp.read(1)
                    if c == b'': # EOF
                        ifp.close()
                        ifp = None
                        logger.info('FauxSerial Closed %s', ifn)
                    else:
                        toSerial += c

            for fp in ofps:
                if isinstance(fp, int): # write to master
                    n = os.write(fp, toSerial)
                    toSerial = toSerial[n:]
                else:
                    n = fp.write(toFile)
                    fp.flush()
                    toFile = toFile[n:]

        logger.info('FauxSerial Fell out of while loop')
