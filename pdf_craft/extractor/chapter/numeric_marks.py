"""Explicit numeric reference typography, not ordinary body numbers."""
import re

from ...expression import ExpressionKind
from ...markdown.paragraph import HTMLTag
from .chapter import InlineExpression

SUPERSCRIPT_NUMBERS = re.compile('[⁰¹²³⁴⁵⁶⁷⁸⁹]+')
_DIGITS = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')
_DECIMAL = re.compile(r'[1-9][0-9]{0,2}')
_RAISED = re.compile(r'(?:\{\s*\}\s*)?\^(?:\{\s*([1-9][0-9]{0,2})\s*\}|([1-9]))')


def superscript_number(text):
    """A consecutive raised run represents one mark, including multiple digits."""
    value = text.translate(_DIGITS)
    return value if _DECIMAL.fullmatch(value) else None


def inline_number(part):
    """Accept only an explicit raised mark, never x^4 or the constant (4)."""
    if not isinstance(part, InlineExpression) or part.kind not in (
            ExpressionKind.INLINE_PAREN, ExpressionKind.INLINE_DOLLAR):
        return None
    text = part.content.strip()
    if SUPERSCRIPT_NUMBERS.fullmatch(text):
        return superscript_number(text)
    match = _RAISED.fullmatch(text)
    return (match[1] or match[2]) if match else None


def html_number(part):
    """Only a whole, plain superscript node carries this numeric identity."""
    if (isinstance(part, HTMLTag) and part.definition.name.lower() == 'sup'
            and all(isinstance(child, str) for child in part.children)):
        value = ''.join(child for child in part.children if isinstance(child, str)).strip()
        return value if _DECIMAL.fullmatch(value) else None
    return None


def explicit_numbers(content):
    """Read immutable local evidence; do not interpret plain numbers as marks."""
    found = set()
    for part in content:
        if isinstance(part, str):
            found.update(value for match in SUPERSCRIPT_NUMBERS.finditer(part)
                         if (value := superscript_number(match[0])) is not None)
        else:
            value = inline_number(part) or html_number(part)
            if value is not None:
                found.add(value)
            elif isinstance(part, HTMLTag):
                found.update(explicit_numbers(part.children))
    return found
