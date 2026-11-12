import re
from typing import Callable, Generator, Iterable

from ...expression import ExpressionKind, ParsedItem, parse_latex_expressions
from ...language import is_latin_letter
from ...markdown.paragraph import HTMLTag, parse_raw_markdown
from .chapter import BlockMember, InlineExpression, SourceTextFragment, TextFlowItem

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


LINK_FLAGS = (
    "‐",
    "‑",
    "‒",
    "–",
    "—",
    "―",
)

_LATEX_PLACEHOLDER_MARKER = "\uE000PDF_CRAFT_LATEX"


def _normalize_paragraph_content(paragraph: TextFlowItem, *, preserve_fragments: bool = False):
    # Initial joiner sources remain literal until review. The traditional
    # path can normalize on emission; refined assembly executes declared text
    # operations instead and must never call this heuristic after adoption.
    if not preserve_fragments and any(
        isinstance(child, SourceTextFragment) and child.source is not None for child in paragraph.children
    ):
        return
    if len(paragraph.children) < 2:
        return

    fragments = [child for child in paragraph.children if isinstance(child, SourceTextFragment)]
    for block1, block2 in zip(fragments, fragments[1:]):

        text1 = last(block1.content)
        text2 = first(block2.content)
        if not isinstance(text1, str) or not isinstance(text2, str):
            continue

        text1 = text1.rstrip()
        text2 = text2.lstrip()
        if not _is_splitted_word(text1, text2):
            continue

        tail_end = 0
        for j in range(len(text2)):
            if is_latin_letter(text2[j]):
                tail_end = j + 1
            else:
                break

        block1.content[-1] = text1[:-1] + text2[:tail_end]
        block2.content[0] = text2[tail_end:].lstrip()
        if not block2.content[0]:
            del block2.content[0]

    # 极端情况下 block2 会因为单词被移走而被清空。此时要将其整个删去。
    if not preserve_fragments:
        paragraph.children = [
            child for child in paragraph.children
            if not isinstance(child, SourceTextFragment) or child.content
        ]


def normalize_source_projection(paragraph: TextFlowItem) -> None:
    """Dehyphenate traditional (unrefined) text without erasing source identities.

    Content is already a detached projection here; TextSource keeps the exact
    OCR text. An empty projected fragment still owns its original PDF region.
    """
    if any(isinstance(child, SourceTextFragment) and child.source is not None
           for child in paragraph.children):
        _normalize_paragraph_content(paragraph, preserve_fragments=True)


def _parse_block_content(text: str | None) -> Content:
    if not text:
        return []

    protected_text, expressions, placeholder_pattern = _protect_latex_expressions(text)
    root_content: Content = parse_raw_markdown(protected_text)

    def expand_text(text: str):
        pos = 0
        for match in placeholder_pattern.finditer(text):
            if match.start() > pos:
                yield text[pos : match.start()]
            expression = expressions[int(match.group(1))]
            yield InlineExpression(
                kind=expression.kind,
                content=expression.content,
            )
            pos = match.end()
        if pos < len(text):
            yield text[pos:]

    expand_text_in_content(
        content=root_content,
        expand=expand_text,
    )
    return root_content


def _protect_latex_expressions(text: str) -> tuple[str, list[ParsedItem], re.Pattern]:
    expressions: list[ParsedItem] = []
    parts: list[str] = []
    placeholder_prefix = _create_latex_placeholder_prefix(text)

    for item in parse_latex_expressions(text):
        if item.kind == ExpressionKind.TEXT:
            parts.append(item.content)
            continue

        parts.append(f"{placeholder_prefix}{len(expressions)}\uE001")
        expressions.append(item)

    placeholder_pattern = re.compile(f"{re.escape(placeholder_prefix)}(\\d+)\uE001")
    return "".join(parts), expressions, placeholder_pattern


def _create_latex_placeholder_prefix(text: str) -> str:
    index = 0
    while True:
        prefix = f"{_LATEX_PLACEHOLDER_MARKER}_{index}_"
        if prefix not in text:
            return prefix
        index += 1


def _is_splitted_word(text1: str, text2: str) -> bool:
    return (
        len(text1) >= 2
        and text1[-1] in LINK_FLAGS
        and is_latin_letter(text1[-2])
        and is_latin_letter(text2[0])
    )
