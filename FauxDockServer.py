# Set up a port to listen on and accept incoming a connection
#
# Read from a file and send to the port
# Write to a file from the port
#
# Jan-2020, Pat Welch, pat@mousebrains.com

import socket
import threading
import argparse
import logging
import select
from io import FileIO, BufferedWriter
from typing import Any

fauxDS = None

def addArgs(parser: argparse.ArgumentParser) -> None:
    ''' Add my command line arguments '''
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument('--host', help='Dockserver hostname with RUDICS listener')
    grp.add_argument('--simDS', action='store_true', help='Simulate a dockserver')

    optgrp = parser.add_argument_group('Simulated Dockserver Options')
    optgrp.add_argument('--dsInput', type=str,
            help='When --simDS is specified, where should simulated dockserver input come from')
    optgrp.add_argument('--dsOutput', type=str, default='/dev/null',
            help='When --simDS is specified, where should simulated dockserver output go')

def setup(args: argparse.Namespace) -> argparse.Namespace:
    ''' Return the args namespace, starting a faux dockserver thread if needed '''
    global fauxDS

    if args.host is not None:
        return args
    fauxDS = FauxDS(args)
    fauxDS.start()
    args.host = fauxDS.host
    args.port = fauxDS.port
    return args

class FauxDS(threading.Thread):
    ''' Create a port and listen on it for an incoming connection '''
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(daemon=True)
        self.args = args
        self.host = '127.0.0.1' # localhost ip address
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, 0))
        self.port = self.sock.getsockname()[1]

    def run(self) -> None: # Called on start
        try:
            with self.sock as s:
                s.listen(1)
                logging.info('FauxDS listening at %s:%s', self.host, self.port)
                while True:
                    (conn, addr) = s.accept()
                    logging.info('FauxDS connection from %s', addr)
                    with conn:
                        self.doit(conn)
                    logging.info('FauxDS connection ended, waiting for next')
        except Exception:
            logging.exception('FauxDS')

    def doit(self, connection: socket.socket) -> None:
        args = self.args
        conn: socket.socket | None = connection

        ifn = args.dsInput
        ofn = args.dsOutput

        ifp: FileIO | None = None if ifn is None else open(ifn, 'rb', buffering=0)
        ofp: BufferedWriter | None = open(ofn, 'wb')

        try:
            logging.info('FauxDS opened %s for input', args.dsInput)
            logging.info('FauxDS opened %s for output', args.dsOutput)

            toSocket = bytearray()
            toFile = bytearray()

            while (conn is not None) or toFile:
                inputs: list[Any] = []
                outputs: list[Any] = []

                if conn is not None:
                    inputs.append(conn)
                    if toSocket:
                        outputs.append(conn)

                if ifp is not None:
                    inputs.append(ifp)

                if (ofp is not None) and toFile:
                    outputs.append(ofp)

                (ifps, ofps, _) = select.select(inputs, outputs, [])

                for fp in ifps:
                    if fp == ifp: # File input
                        c = fp.read(1)
                        if c == b'': # EOF
                            ifp.close()
                            ifp = None
                            logging.info('FauxDS closed %s', ifn)
                        else:
                            toSocket += c
                    else:
                        c = fp.recv(1)
                        if c == b'': # EOF
                            conn = None
                            logging.info('FauxDS closed connection')
                        else:
                            toFile += c

                for fp in ofps:
                    if fp == ofp:
                        n = ofp.write(toFile)
                        ofp.flush()
                        toFile = toFile[n:]
                    elif conn is not None:
                        n = conn.send(toSocket)
                        toSocket = toSocket[n:]
        finally:
            if ifp is not None:
                ifp.close()
            if ofp is not None:
                ofp.close()
                logging.info('FauxDS closed %s', ofn)
