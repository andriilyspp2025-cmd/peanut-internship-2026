import pytest
from src.core.serializer import (
    CanonicalSerializer,
    FloatRejectedError,
    LargeIntegerWarning,
)


def test_canonical_serializer_determinism():
    obj = {"c": 3, "a": 1, "b": {"z": 26, "x": 24}}
    first_result = CanonicalSerializer.serialize(obj)

    for _ in range(1000):
        assert CanonicalSerializer.serialize(obj) == first_result


def test_canonical_serializer_nested_object_sorting():
    obj1 = {"c": 3, "a": 1, "b": {"z": 26, "x": 24}}
    obj2 = {"a": 1, "b": {"x": 24, "z": 26}, "c": 3}

    assert CanonicalSerializer.serialize(obj1) == CanonicalSerializer.serialize(obj2)
    assert b'{"a":1,"b":{"x":24,"z":26},"c":3}' == CanonicalSerializer.serialize(obj1)


def test_canonical_serializer_unicode_emoji_handling():
    obj = {"message": "Hello 🌍!", "key": "Привіт"}
    serialized = CanonicalSerializer.serialize(obj)

    # ensure_ascii=False means it should literally contain the utf-8 bytes for the emoji
    assert "🌍".encode("utf-8") in serialized
    assert "Привіт".encode("utf-8") in serialized


def test_canonical_serializer_rejects_floats():
    with pytest.raises(FloatRejectedError, match="Float value detected"):
        CanonicalSerializer.serialize({"value": 1.5})


def test_canonical_serializer_warns_large_integers():
    large_int = 2**53 + 1
    with pytest.warns(
        LargeIntegerWarning, match="exceeds JavaScript's MAX_SAFE_INTEGER"
    ):
        CanonicalSerializer.serialize({"value": large_int})


def test_canonical_serializer_hash_consistency():
    obj = {"a": 1, "b": 2}
    # Just asserting it returns bytes
    assert isinstance(CanonicalSerializer.hash(obj), bytes)
