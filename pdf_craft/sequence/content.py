from typing import Callable, Generator, Iterable

from ..markdown.paragraph import HTMLTag
from .chapter import BlockMember

Content = list[str | BlockMember | HTMLTag[BlockMember]]


def first(content: Content) -> None | str | BlockMember:
    if not content:
        return None
    element = content[0]
    if isinstance(element, HTMLTag):
        return first(element.children)
    else:
        return element


def last(content: Content) -> None | str | BlockMember:
    if not content:
        return None
    element = content[-1]
    if isinstance(element, HTMLTag):
        return last(element.children)
    else:
        return element


def join_texts_in_content(content: Content) -> None:
    for sub_content in _search_content(content):
        i: int = 0
        while i < len(sub_content) - 1:
            part1 = sub_content[i]
            part2 = sub_content[i + 1]
            if isinstance(part1, str) and isinstance(part2, str):
                sub_content[i] = part1 + part2
                del sub_content[i + 1]
            else:
                i += 1


def expand_text_in_content(
    content: Content,
    expand: Callable[[str], Iterable[str | BlockMember]],
) -> None:
    for sub_content in _search_content(content):
        i: int = 0
        while i < len(sub_content):
            part = sub_content[i]
            if isinstance(part, str):
                del sub_content[i]
                for expanded_part in expand(part):
                    sub_content.insert(i, expanded_part)
                    i += 1
            else:
                i += 1


def _search_content(content: Content) -> Generator[Content, None, None]:
    for child in content:
        if isinstance(child, HTMLTag):
            yield from _search_content(child.children)
    yield content
