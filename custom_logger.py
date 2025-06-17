import logging
from logging.handlers import RotatingFileHandler


class LoggerSetup:
    # Track loggers that have already been configured
    _configured_loggers = set()

    @staticmethod
    def setup_logger(name: str, log_file: str, level=logging.DEBUG):
        """Set up a logger with file and console handlers"""
        # Get or create the logger
        logger = logging.getLogger(name)

        # Skip setup if this logger has already been configured
        if name in LoggerSetup._configured_loggers:
            return logger

        # Add to our tracking set
        LoggerSetup._configured_loggers.add(name)

        # Reset any existing handlers to avoid duplication
        if logger.hasHandlers():
            logger.handlers.clear()

        # Configure the logger
        logger.setLevel(level)
        logger.propagate = False

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d | %(message)s'
        )

        # File handler with rotation
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        return logger
