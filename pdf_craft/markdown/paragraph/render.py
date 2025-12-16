from typing import Generator, Callable, Iterable

from .types import P, HTMLTag


def render_markdown_paragraph(
        children: list[str | P | HTMLTag[P]],
        render_payload: Callable[[P], Iterable[str]],
    ) -> Generator[str, None, None]:
    for child in children:
        if isinstance(child, str):
            yield child
        elif isinstance(child, HTMLTag):
            yield from _render_html_tag(child, render_payload)
        else:
            yield from render_payload(child)


def _render_html_tag(tag: HTMLTag[P], render_payload: Callable[[P], Iterable[str]]) -> Generator[str, None, None]:
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