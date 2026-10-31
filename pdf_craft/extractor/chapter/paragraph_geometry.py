"""Conservative paragraph evidence from visible horizontal single lines."""

from unicodedata import east_asian_width

from .chapter import SourceTextFragment, TextFlowItem
from .content import last
from .mark import NumberClass, transform2mark

_CLOSING_QUOTES = '\"\'”’」』»'
_COMPLETED_ENDINGS = (".", "!", "?", "。", "！", "？", ":", "：", "…")


def indented_line_boundary(before: TextFlowItem, after: TextFlowItem) -> bool:
    """A short indented line can prove a boundary; multiline bboxes cannot.

    Punctuation only enables this geometric check, never proves a paragraph
    boundary by itself. The caller skips this check across intervening assets.
    Raw OCR text is not changed or interpreted semantically.
    """
    left, right = before.children[-1], after.children[0]
    if not isinstance(left, SourceTextFragment) or not isinstance(right, SourceTextFragment):
        return False
    text = last(left.content)
    if (right.source is not None and right.source.first_line_inset is not None
            and right.source.first_line_inset >= 0.75
            and isinstance(text, str) and _compatible_line_end(text)):
        return True
    if left.page_index != right.page_index:
        return False
    if not isinstance(text, str) or not text.rstrip().rstrip(_CLOSING_QUOTES).endswith(_COMPLETED_ENDINGS):
        return False
    # Side-by-side quote and attribution are not separate printed lines.
    height = min(left.bbox[3] - left.bbox[1], right.bbox[3] - right.bbox[1])
    if height <= 0 or right.bbox[1] < left.bbox[3] - 0.1 * height:
        return False
    return _indented_single_line(left, right) or _indented_single_line(right, left)


def _compatible_line_end(text: str) -> bool:
    """Use measured inset only after a boundary-compatible text ending.

    Circled references may follow punctuation without becoming paragraph text
    themselves. Inspect that suffix only; preserve the actual source content.
    """
    tail = text.rstrip()
    if tail and not any(char.isalnum() for char in tail):
        return True
    while tail:
        mark = transform2mark(tail[-1])
        if mark is None or mark.clazz not in (
                NumberClass.CIRCLED_NUMBER, NumberClass.CIRCLED_CHINESE, NumberClass.BLACK_CIRCLED_NUMBER):
            break
        tail = tail[:-1].rstrip()
    return tail.rstrip(_CLOSING_QUOTES).endswith(_COMPLETED_ENDINGS)


def _indented_single_line(narrow: SourceTextFragment, wide: SourceTextFragment) -> bool:
    if not _single_line(narrow):
        return False
    left, top, right, bottom = narrow.bbox
    height = bottom - top
    return (left - wide.bbox[0] >= 0.75 * height
            and wide.bbox[2] - right >= 0.75 * height)


def _single_line(fragment: SourceTextFragment) -> bool:
    text = (fragment.source.text if fragment.source is not None
            else "".join(x for x in fragment.content if isinstance(x, str)))
    # Math/HTML source lengths do not estimate printed advances; leave them to
    # the existing rule rather than treating markup characters as visible ink.
    if "\n" in text or "\\" in text or "<" in text:
        return False
    units = sum(1 if east_asian_width(char) in ("W", "F") else 0.5
                for char in text.strip())
    left, top, right, bottom = fragment.bbox
    height = bottom - top
    return (units >= 4 and height > 0
            and 0.6 * units * height <= right - left <= 1.8 * units * height)
