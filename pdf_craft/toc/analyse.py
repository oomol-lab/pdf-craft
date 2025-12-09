from typing import Iterable, Callable
from .types import P, TocItem


def analyse_toc(
    payloads: Iterable[P],
    get_det: Callable[[P], Iterable[tuple[int, int, int, int]]],
    get_title: Callable[[P], str],
) -> list[TocItem[P]]:
    raise NotImplementedError()
