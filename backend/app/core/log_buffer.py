"""In-memory ring buffer for recent log lines — used by diagnostics API."""
import logging
from collections import deque
from threading import Lock

_MAX_LINES = 2000
_buffer: deque[str] = deque(maxlen=_MAX_LINES)
_lock = Lock()


class BufferHandler(logging.Handler):
    """Logging handler that appends formatted records to a deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            with _lock:
                _buffer.append(line)
        except Exception:
            pass


def get_lines(n: int = 100, level_filter: str = "") -> list[str]:
    """Return last *n* log lines, optionally filtered by level."""
    with _lock:
        lines = list(_buffer)
    # Take tail
    lines = lines[-n:]
    if level_filter:
        fl = level_filter.upper()
        lines = [l for l in lines if fl in l]
    return lines


def install(fmt: str = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s") -> None:
    """Attach BufferHandler to the root logger."""
    handler = BufferHandler()
    handler.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(handler)
