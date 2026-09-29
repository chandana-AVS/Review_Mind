"""Injectable clock. Always use clock.now() instead of datetime.now()/utcnow()."""

from collections.abc import Callable
from datetime import UTC, datetime


def _system_now() -> datetime:
    return datetime.now(UTC)


_now: Callable[[], datetime] = _system_now


def now() -> datetime:
    return _now()


def set_clock(fn: Callable[[], datetime]) -> None:
    """Tests use this (via the `frozen_clock` fixture) to freeze time."""
    global _now
    _now = fn


def reset_clock() -> None:
    set_clock(_system_now)
