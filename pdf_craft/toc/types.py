from dataclasses import dataclass
from typing import Generic, TypeVar


P = TypeVar("P")

@dataclass
class TocItem(Generic[P]):
    title: str
    payload: P
    children: list["TocItem[P]"]
