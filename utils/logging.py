"""Structured logging utilities."""

import logging
import sys
from typing import Optional
from datetime import datetime


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured, readable logs."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",
    }

    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname

        if self.use_colors:
            color = self.COLORS.get(level, self.COLORS["RESET"])
            reset = self.COLORS["RESET"]
            level_str = f"{color}{level:8}{reset}"
        else:
            level_str = f"{level:8}"

        # Extract source if present
        source = getattr(record, "source", "")
        source_str = f"[{source}] " if source else ""

        # Extract progress if present
        progress = getattr(record, "progress", "")
        progress_str = f"({progress}) " if progress else ""

        return f"{timestamp} {level_str} {source_str}{progress_str}{record.getMessage()}"


class SourceAdapter(logging.LoggerAdapter):
    """Adapter to add source context to log messages."""

    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {})
        kwargs["extra"]["source"] = self.extra.get("source", "")
        return msg, kwargs


_loggers: dict = {}


def setup_logger(
    name: str = "vibe_coder",
    level: int = logging.INFO,
    use_colors: bool = True,
) -> logging.Logger:
    """Set up and return a configured logger."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(use_colors=use_colors))
    logger.addHandler(handler)

    _loggers[name] = logger
    return logger


def get_logger(name: str = "vibe_coder", source: Optional[str] = None) -> logging.LoggerAdapter:
    """Get a logger, optionally with source context."""
    if name not in _loggers:
        setup_logger(name)

    logger = _loggers[name]

    if source:
        return SourceAdapter(logger, {"source": source})
    return SourceAdapter(logger, {})


def log_progress(
    logger: logging.LoggerAdapter,
    current: int,
    total: int,
    message: str,
) -> None:
    """Log a progress message."""
    progress = f"{current}/{total}"
    logger.info(message, extra={"progress": progress})
