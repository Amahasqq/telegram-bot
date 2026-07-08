import pytest

from app.main import is_rate_limited, _user_last_msg


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
    _user_last_msg[1] = 0.0
    assert is_rate_limited(1) is False


def test_different_users_independent():
    assert is_rate_limited(1) is False
    assert is_rate_limited(2) is False
    assert is_rate_limited(1) is True
    assert is_rate_limited(2) is True
