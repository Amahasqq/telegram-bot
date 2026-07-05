import time

import pytest

RATE_LIMIT = 1.5
_user_last_msg: dict[int, float] = {}


def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    last = _user_last_msg.get(user_id, 0.0)
    if now - last < RATE_LIMIT:
        return True
    _user_last_msg[user_id] = now
    return False


@pytest.fixture(autouse=True)
def reset_state():
    _user_last_msg.clear()
    yield


def test_under_limit():
    assert is_rate_limited(1) is False


def test_over_limit_within_window():
    assert is_rate_limited(1) is False
    assert is_rate_limited(1) is True


def test_over_limit_allows_after_window():
    global _user_last_msg
    assert is_rate_limited(1) is False
    assert is_rate_limited(1) is True
    _user_last_msg[1] = time.time() - 2.0
    assert is_rate_limited(1) is False


def test_different_users_independent():
    assert is_rate_limited(1) is False
    assert is_rate_limited(2) is False
    assert is_rate_limited(1) is True
    assert is_rate_limited(2) is True
