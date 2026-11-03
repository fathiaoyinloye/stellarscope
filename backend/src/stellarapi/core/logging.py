import logging
import sys

from stellarapi.core.__init__ import get_loggers  # circular-safe; we'll use simple logging


def setup_logging(level: str = "INFO") -> None:
    """Configure application-wide logging.

    - One handler on stderr with a concise, structured format.
    - Log level driven by the ``LOG_LEVEL`` environment variable
      (``"DEBUG"``, ``"INFO"``, ``"WARNING"``, ``"ERROR"``).
    - Prevents double-handling if called multiple times.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    # Configure the root logger once
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(numeric_level)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)