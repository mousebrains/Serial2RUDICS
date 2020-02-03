#! /usr/bin/env python3
#
# Add logger related options and create a logger object
#
# September-2019, Pat Welch, pat@mousebrains.com

import argparse
import logging
import logging.handlers

def addArgs(parser:argparse.ArgumentParser) -> None:
    grp = parser.add_argument_group('Logger Related Options')
    grp.add_argument('--logfile', help='Name of logfile')
    grp.add_argument('--logBytes', type=int, default=10000000, help='Maximum logfile size in bytes')
    grp.add_argument('--logCount', type=int, default=3, help='Number of backup files to keep')
    grp.add_argument('--verbose', action='store_true', help='Enable verbose logging')

def mkLogger(args:argparse.ArgumentParser) -> logging.Logger:
    logger = logging.getLogger()
    if args.logfile:
        ch = logging.handlers.RotatingFileHandler(args.logfile,
                maxBytes=args.logBytes,
                backupCount=args.logCount)
    else:
        ch = logging.StreamHandler()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s')
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    
    return logger 
