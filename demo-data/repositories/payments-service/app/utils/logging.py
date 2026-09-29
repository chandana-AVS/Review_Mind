"""structlog setup. Use snake_case event names and keyword fields; no f-string messages."""

import re

import structlog

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_PAN = re.compile(r"\b(\d{6})\d{3,9}(\d{4})\b")


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def redact(value: str) -> str:
    """Mask emails and card numbers before logging."""
    value = _EMAIL.sub("***@***", value)
    return _PAN.sub(r"\1******\2", value)
