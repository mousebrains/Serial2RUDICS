import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logging
import logging.handlers
import MyLogger
from tests.conftest import make_args


def _cleanup_logger():
    """Remove all handlers from the root logger."""
    logger = logging.getLogger()
    for h in logger.handlers[:]:
        logger.removeHandler(h)
        h.close()


def test_addArgs_registers_expected_arguments():
    """addArgs registers logfile, logBytes, logCount, and verbose arguments."""
    import argparse
    parser = argparse.ArgumentParser()
    MyLogger.addArgs(parser)
    args = parser.parse_args([
        "--logfile", "test.log",
        "--logBytes", "5000",
        "--logCount", "2",
        "--verbose",
    ])
    assert args.logfile == "test.log"
    assert args.logBytes == 5000
    assert args.logCount == 2
    assert args.verbose is True


def test_stream_handler_when_no_logfile():
    """mkLogger uses a StreamHandler when no logfile is set."""
    try:
        args = make_args(logfile=None)
        logger = MyLogger.mkLogger(args)
        handler_types = [type(h) for h in logger.handlers]
        assert logging.StreamHandler in handler_types
        assert logging.handlers.RotatingFileHandler not in handler_types
    finally:
        _cleanup_logger()


def test_rotating_file_handler_with_logfile(tmp_path):
    """mkLogger uses a RotatingFileHandler when logfile is set."""
    try:
        logfile = str(tmp_path / "app.log")
        args = make_args(logfile=logfile)
        logger = MyLogger.mkLogger(args)
        handler_types = [type(h) for h in logger.handlers]
        assert logging.handlers.RotatingFileHandler in handler_types
        assert logging.StreamHandler not in [
            type(h) for h in logger.handlers
            if not isinstance(h, logging.handlers.RotatingFileHandler)
        ]
    finally:
        _cleanup_logger()


def test_debug_level_when_verbose():
    """mkLogger sets DEBUG level when verbose=True."""
    try:
        args = make_args(verbose=True)
        logger = MyLogger.mkLogger(args)
        assert logger.level == logging.DEBUG
        # Check only the handler mkLogger added (last one), not pytest's LogCaptureHandler
        our_handler = logger.handlers[-1]
        assert our_handler.level == logging.DEBUG
    finally:
        _cleanup_logger()


def test_info_level_by_default():
    """mkLogger sets INFO level when verbose=False (default)."""
    try:
        args = make_args(verbose=False)
        logger = MyLogger.mkLogger(args)
        assert logger.level == logging.INFO
        # Check only the handler mkLogger added (last one), not pytest's LogCaptureHandler
        our_handler = logger.handlers[-1]
        assert our_handler.level == logging.INFO
    finally:
        _cleanup_logger()


def test_formatter_includes_asctime_and_levelname():
    """The formatter pattern contains %(asctime)s and %(levelname)s."""
    try:
        args = make_args()
        logger = MyLogger.mkLogger(args)
        assert len(logger.handlers) >= 1
        fmt = logger.handlers[-1].formatter._fmt
        assert "%(asctime)s" in fmt
        assert "%(levelname)s" in fmt
    finally:
        _cleanup_logger()
