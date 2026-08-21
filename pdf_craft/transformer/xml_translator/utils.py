import re
from collections.abc import Iterable
from typing import TypeVar

K = TypeVar("K")
T = TypeVar("T")

_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", text)


def is_the_same(elements: Iterable[T]) -> bool:
    iterator = iter(elements)
    try:
        first_element = next(iterator)
    except StopIteration:
        return True

    for element in iterator:
        if element != first_element:
            return False
    return True


def nest(items: Iterable[tuple[K, T]]) -> dict[K, list[T]]:
    nested_dict: dict[K, list[T]] = {}
    for key, value in items:
        ensure_list(nested_dict, key).append(value)
    return nested_dict


def ensure_list(target: dict[K, list[T]], key: K) -> list[T]:
    value = target.get(key, None)
    if value is None:
        value = []
        target[key] = value
    return value
