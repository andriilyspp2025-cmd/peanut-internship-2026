import pytest

x = 5


def test_invariant():
    result = x + 0
    assert result == x


def test_determinism():
    result_a = 100 * 0.05
    result_b = 100 * 0.05
    assert result_a == result_b


def test_negative():
    with pytest.raises(ZeroDivisionError):
        10 / 0
