from typing import Callable, Generator, Iterable

from ...language import is_chinese_char
from .types import HTMLTag, P


def render_markdown_paragraph(
    children: list[str | P | HTMLTag[P]],
    render_payload: Callable[[P | str], Iterable[str]],
) -> Generator[str, None, None]:
    yield from _normalize_paragraph(
        parts=_render_markdown_children(
            children=children,
            render_payload=render_payload,
        ),
    )


def _render_markdown_children(
    children: list[str | P | HTMLTag[P]],
    render_payload: Callable[[P | str], Iterable[str]],
) -> Generator[str, None, None]:
    for child in children:
        if isinstance(child, HTMLTag):
            yield from _render_html_tag(child, render_payload)
        else:
            yield from render_payload(child)


def _render_html_tag(
    tag: HTMLTag[P], render_payload: Callable[[P | str], Iterable[str]]
) -> Generator[str, None, None]:
    tag_name = tag.definition.name

    if not tag.children:
        yield "<"
        yield tag_name
        yield from _render_attributes(tag.attributes)
        yield " />"
    else:
        yield "<"
        yield tag_name
        yield from _render_attributes(tag.attributes)
        yield ">"

        yield from render_markdown_paragraph(tag.children, render_payload)

        yield "</"
        yield tag_name
        yield ">"


def _render_attributes(attributes: list[tuple[str, str]]) -> Generator[str, None, None]:
    for name, value in attributes:
        yield " "
        yield name
        if value:
            yield '="'
            yield from _escape_attribute(value)
            yield '"'


def _escape_attribute(value: str) -> Generator[str, None, None]:
    for char in value:
        if char == "&":
            yield "&amp;"
        elif char == '"':
            yield "&quot;"
        elif char == "<":
            yield "&lt;"
        elif char == ">":
            yield "&gt;"
        else:
            yield char


def _normalize_paragraph(parts: Iterable[str]) -> Generator[str, None, None]:
    last_char: str | None = None
    is_line_head = True
    for part in _split_enters(parts):
        if part != "\n":
            if is_line_head:
                is_line_head = False
                part = part.lstrip()
                if (
                    part
                    and last_char is not None
                    and (not is_chinese_char(last_char) or not is_chinese_char(part[0]))
                ):
                    yield " "
            if part:
                yield part
                part = part.rstrip()
                if part:
                    last_char = part[-1]
        else:
            is_line_head = True


def _split_enters(parts: Iterable[str]) -> Generator[str, None, None]:
    for part in parts:
        if not part:
            continue
        split_parts = part.splitlines()
        yield split_parts[0]
        for i in range(1, len(split_parts)):
            yield "\n"
            yield split_parts[i]
