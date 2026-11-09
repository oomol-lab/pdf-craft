"""Shared whitespace policy for ordinary document text, not serialized syntax."""
import re

# Explicit padding, not the Unicode Cf category: join controls and variation
# selectors participate in real writing systems and emoji and must survive.
_ZERO_WIDTH_PADDING = '\u200b\u2060\ufeff'
_PADDING_TABLE = str.maketrans('', '', _ZERO_WIDTH_PADDING)
_WHITESPACE = re.compile(r'[\s\x08]+')


def normalize_whitespace(text: str, *, trim: bool = False) -> str:
    """Collapse Unicode whitespace/backspace and remove zero-width padding.

    Keep one boundary space by default: independently trimming inline fields
    would concatenate words. Callers owning a complete field may request trim.
    Code, TeX, source offsets and protocol syntax need their own boundaries.
    """
    text = _WHITESPACE.sub(' ', text.translate(_PADDING_TABLE))
    return text.strip(' ') if trim else text


def is_whitespace(character: str) -> bool:
    """Recognize both collapsible spaces and removable document padding."""
    return character.isspace() or character in '\x08' + _ZERO_WIDTH_PADDING
