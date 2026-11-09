"""Whitespace context for document text and explicitly preserved format payloads."""
from enum import Enum
from xml.etree.ElementTree import Element

from ..common.whitespace import normalize_whitespace, is_whitespace


class WhiteSpace(str, Enum):
    NORMAL = 'normal'
    NOWRAP = 'nowrap'
    PRE = 'pre'
    PRE_WRAP = 'pre-wrap'
    PRE_LINE = 'pre-line'
    BREAK_SPACES = 'break-spaces'


def collapses_breaks(mode: WhiteSpace) -> bool:
    return mode in (WhiteSpace.NORMAL, WhiteSpace.NOWRAP)


def normalize(text: str, mode: WhiteSpace = WhiteSpace.NORMAL, *, trim: bool = False) -> str:
    """Normalize ordinary prose; preserve explicitly protected text syntax."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if mode in (WhiteSpace.PRE, WhiteSpace.PRE_WRAP, WhiteSpace.BREAK_SPACES):
        return text
    if mode == WhiteSpace.PRE_LINE:
        lines = [normalize_whitespace(line) for line in text.split('\n')]
        for index, line in enumerate(lines):
            if index:
                line = line.lstrip(' ')
            lines[index] = line.rstrip(' ') if index < len(lines)-1 else line
        text = '\n'.join(lines)
        return text.strip(' ') if trim else text
    return normalize_whitespace(text, trim=trim)


def mode_for(element: Element, inherited: WhiteSpace = WhiteSpace.NORMAL) -> WhiteSpace:
    """Resolve inherited mode, HTML defaults and explicit transport/inline CSS.

    External stylesheets and selectors are not evaluated. xml:space=preserve is
    an additional lossless XML contract, not an assertion about CSS precedence.
    Unsupported explicit modes are conservatively preserved.
    """
    if element.tag in ('expression', 'inline_expr', 'math', 'source-math'):
        return WhiteSpace.PRE  # TeX/MathML payload is syntax, not ordinary prose.
    if element.get('{http://www.w3.org/XML/1998/namespace}space', element.get('xml:space')) == 'preserve':
        return WhiteSpace.PRE
    mode = {'pre': WhiteSpace.PRE, 'textarea': WhiteSpace.PRE_WRAP}.get(element.tag.lower(), inherited)
    value = element.get('white-space')
    important = False
    for declaration in element.get('style', '').split(';'):
        name, separator, setting = declaration.partition(':')
        if separator and name.strip().lower() == 'white-space':
            setting = setting.strip().lower()
            priority = setting.endswith('!important')
            if priority or not important:
                value = setting.removesuffix('!important').strip()
                important = priority
    if value is None:
        return mode
    value = value.strip().lower()
    if value in ('inherit', 'unset'):
        return inherited
    if value == 'initial':
        return WhiteSpace.NORMAL
    try:
        return WhiteSpace(value)
    except ValueError:
        return WhiteSpace.PRE


def mode_in_stack(elements) -> WhiteSpace:
    mode = WhiteSpace.NORMAL
    for element in elements:
        mode = mode_for(element, mode)
    return mode


def can_indent(element: Element, mode: WhiteSpace) -> bool:
    """Only block-only containers have discardable inter-block indentation."""
    # Lazy import keeps the format-neutral policy reusable by segment utilities.
    from .xml_translator.xml.inline import is_inline_element
    return (collapses_breaks(mode) and not is_inline_element(element) and len(element) > 0
            and all(is_whitespace(char) for char in element.text or '')
            and all(not is_inline_element(child)
                    and all(is_whitespace(char) for char in child.tail or '') for child in element))
