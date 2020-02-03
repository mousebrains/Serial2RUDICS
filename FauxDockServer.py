
# Set up a port to listen on and accept incoming an connection
# 
# Read from a file and send to the port
# Write to a file from the the port
# 
# Jan-2020, Pat Welch, pat@mousebrains.com

import socket
import threading
import argparse
import logging
import select
import random

fauxDS = None

def addArgs(parser:argparse.ArgumentParser) -> None:
    ''' Add my command line arguments '''
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument('--host', help='Dockserver hostname with RUDICS listener')
    grp.add_argument('--simDS', action='store_true', help='Simulate a dockserver')
    
    grp = parser.add_argument_group('Simulated Dockserver Options')
    grp.add_argument('--dsInput', type=str,
            help='When --simDS is specified, where should simulated dockserver input come from')
    grp.add_argument('--dsOutput', type=str, default='/dev/null',
            help='When --simDS is specified, where should simulated dockserver output go')

def setup(args:argparse.ArgumentParser, logger:logging.Logger) -> argparse.ArgumentParser:
    ''' Return the serial device name to use, and start a ptty/thread if needed '''

    if args.host is not None:
        return args
    fauxDS = FauxDS(args, logger)
    fauxDS.start()
    args.host = fauxDS.host
    args.port = fauxDS.port
    return args

class FauxDS(threading.Thread):
    ''' Create a port and listen on it for an incoming connection '''
    def __init__(self, args:argparse.ArgumentParser, logger:logging.Logger) -> None:
        threading.Thread.__init__(self, daemon=True)
        self.args = args
        self.logger = logger
        self.host = '127.0.0.1' # localhost ip address
        self.port = random.randrange(60000, 65536) # Port to listen on

    def run(self) -> None: # Called on start
        args = self.args
        logger = self.logger

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((self.host, self.port))
                s.listen(1) # Reject after first connection
                logger.info('FauxDS listening at %s:%s', self.host, self.port)
                (conn, addr) = s.accept()

                logger.info('FauxDS connection from %s', addr)
                
                with conn:
                    self.doit(conn)
        except:
            logger.exception('FauxDS')

    def doit(self, conn) -> None:
        args = self.args
        logger = self.logger

        ifn = args.dsInput
        ofn = args.dsOutput

        ifp = None if ifn is None else open(ifn, 'rb')
        ofp = open(ofn, 'wb')

        logger.info('FauxDS opened %s for input', args.dsInput)
        logger.info('FauxDS opened %s for output', args.dsOutput)

        toSocket = bytearray()
        toFile = bytearray()

        while (conn is not None) or len(toFile):
            inputs = []
            outputs = []

            if conn is not None:
                inputs.append(conn)
                if len(toSocket):
                    outputs.append(conn)

            if ifp is not None:
                inputs.append(ifp)

            if (ofp is not None) and len(toFile):
                outputs.append(ofp)

            (ifps, ofps, efps) = select.select(inputs, outputs, [])

            for fp in ifps:
                if fp == ifp: # File input
                    c = fp.read(1)
                    if c == b'': # EOF
                        ifp.close()
                        ifp = None
                        logger.info('FauxDS closed %s', ifn)
                    else:
                        toSocket += c
                else:
                    c = fp.recv(1)
                    if c == b'': # EOF
                        conn.close()
                        conn = None
                        logger.info('FauxDS closed connection')
                    else:
                        toFile += c

            for fp in ofps:
                if fp == ofp:
                    n = ofp.write(toFile)
                    ofp.flush()
                    toFile = toFile[n:]
                else:
                    n = conn.send(toSocket)
                    toSocket = toSocket[n:]

        if ofp is not None:
            ofp.close()
            logger.info('FauxDS closed %s', ofn)
