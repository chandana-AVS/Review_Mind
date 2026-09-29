from datetime import UTC, datetime

import pytest

from app.utils import clock


@pytest.fixture
def frozen_clock():
    fixed = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    clock.set_clock(lambda: fixed)
    yield fixed
    clock.reset_clock()
