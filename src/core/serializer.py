import json
import warnings
from typing import Any
from eth_utils import keccak

# Константа для сумісності з JavaScript (Number.MAX_SAFE_INTEGER)
# Вище цього значення JS втрачає точність цілих чисел
_JS_MAX_SAFE_INT = 2**53 - 1


class FloatRejectedError(TypeError):
    """Викидається, коли знайдено float у даних для серіалізації."""

    pass


class LargeIntegerWarning(UserWarning):
    """Попередження про числа, що перевищують безпечний ліміт JavaScript."""

    pass


class CanonicalSerializer:
    """
    Професійна реалізація детерміністичної JSON-серіалізації.
    Забезпечує ідентичність байтів для криптографічних підписів.
    """

    @classmethod
    def _normalise(cls, obj: Any) -> Any:
        """
        Рекурсивно готує об'єкт: перевіряє типи (заборона float)
        та попереджає про великі індекси.
        """
        # Базові типи, які не потребують обробки
        if isinstance(obj, (bool, str)) or obj is None:
            return obj

        # Жорстка заборона float через ризик неточності округлення
        if isinstance(obj, float):
            raise FloatRejectedError(
                f"Float value detected: {obj}. "
                "Floats are forbidden in canonical serialization to prevent precision loss. "
                "Use int or string representation instead."
            )

        # Перевірка цілих чисел на сумісність із JS-середовищем
        if isinstance(obj, int):
            if abs(obj) > _JS_MAX_SAFE_INT:
                warnings.warn(
                    f"Integer {obj} exceeds JavaScript's MAX_SAFE_INTEGER (2^53 - 1). "
                    "This may lead to precision loss on the frontend/client side.",
                    LargeIntegerWarning,
                    stacklevel=3,
                )
            return obj

        # Рекурсивна обробка словників (ключі будуть відсортовані пізніше в json.dumps)
        if isinstance(obj, dict):
            return {str(k): cls._normalise(v) for k, v in obj.items()}

        # Підтримка списків та кортежів (tuple автоматично стає list у JSON)
        if isinstance(obj, (list, tuple)):
            return [cls._normalise(item) for item in obj]

        return obj

    @classmethod
    def serialize(cls, obj: Any) -> bytes:
        """
        Повертає канонічне байтове представлення об'єкта.
        - Ключі відсортовані за алфавітом
        - Жодних пробілів (компактний формат)
        - Unicode зберігається як є (ensure_ascii=False)
        """
        normalised_data = cls._normalise(obj)

        json_str = json.dumps(
            normalised_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        return json_str.encode("utf-8")

    @classmethod
    def hash(cls, obj: Any) -> bytes:
        """Повертає Keccak-256 хеш канонічної серіалізації."""
        return keccak(cls.serialize(obj))
