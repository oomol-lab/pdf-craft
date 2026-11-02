"""Conservative paragraph evidence from visible horizontal single lines."""

from unicodedata import east_asian_width
from statistics import median
from math import sqrt
import re

from ...pdf import PageLayout
from ...expression import parse_latex_expressions, ExpressionKind
from .chapter import SourceTextFragment, TextFlowItem
from .content import last
from .mark import NumberClass, transform2mark

_CLOSING_QUOTES = '\"\'”’」』»'
_COMPLETED_ENDINGS = (".", "!", "?", "。", "！", "？", ":", "：", "…")


def text_scale_estimate(text, bbox):
    """Soft layout evidence, not font metrics or an ownership decision.

    OCR block height alone conflates font size and line count. Approximate
    occupied glyph area makes long small-print continuations comparable with
    short marked notes. Whitespace and Latin's narrower advance are accounted
    for, but formulas/leading make this only a hint to the semantic reviewer.
    """
    units = sum(1 if east_asian_width(char) in {'W', 'F'} else .5
                for char in text if not char.isspace())
    return round(sqrt(max(0, bbox[2]-bbox[0])*max(0, bbox[3]-bbox[1])/max(1, units)), 2)

def printed_separator_boundary(before: TextFlowItem, after: TextFlowItem) -> bool:
    """Keep a detached centered asterism separate, without removing its source.

    A lone left-aligned star may instead be a note marker. Require a narrow
    centered source on its own printed line; inline symbols and mathematical
    objects do not establish this boundary.
    """
    left, right = before.children[-1], after.children[0]
    if (not isinstance(left, SourceTextFragment) or not isinstance(right, SourceTextFragment)
            or left.page_index != right.page_index):
        return False
    for narrow, wide in ((left, right), (right, left)):
        if not all(isinstance(item, str) for item in narrow.content):
            continue
        text = ''.join(item for item in narrow.content if isinstance(item, str))
        symbols = ''.join(text.split())
        if not symbols or not (set(symbols) == {'*'} or symbols == '⁂'):
            continue
        x1, y1, x2, y2 = narrow.bbox
        a, b, c, d = wide.bbox
        height, width = y2 - y1, x2 - x1
        if (height > 0 and 0 < width <= 8 * height and c - a >= 4 * width
                and abs((x1 + x2) - (a + c)) <= height
                and (y2 <= b or d <= y1)):
            return True
    return False


def indented_line_boundary(
    before: TextFlowItem, after: TextFlowItem, *, column_indented: bool = False,
) -> bool:
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
    if (column_indented and isinstance(text, str) and _compatible_line_end(text)
            and (left.page_index != right.page_index
                 or right.bbox[1] >= left.bbox[3] - 0.1 * (right.bbox[3] - right.bbox[1]))):
        return True
    if left.page_index != right.page_index:
        return False
    if not isinstance(text, str) or not _compatible_line_end(text):
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
    return tail.rstrip(_CLOSING_QUOTES).rstrip('—').rstrip().endswith(_COMPLETED_ENDINGS)


def _indented_single_line(narrow: SourceTextFragment, wide: SourceTextFragment) -> bool:
    if not _single_line(narrow):
        return False
    left, top, right, bottom = narrow.bbox
    height = bottom - top
    return (left - wide.bbox[0] >= 0.75 * height
            and wide.bbox[2] - right >= 0.75 * height)


def indented_line_starts(layouts: list[PageLayout]) -> set[tuple[int, int, int, int]]:
    """Locate short indented lines against nearby wider text in the same column.

    Two successive dialogue lines can share an inset. Their mutual alignment
    does not establish the column margin. Use the nearest enclosing multiline
    box among three neighbours on either side, without changing OCR evidence.
    Existing pixel measurements take precedence over this fallback estimate.
    """
    starts = set()
    math_lines = _math_single_lines(layouts)
    for index, layout in enumerate(layouts):
        if (layout.ref != "text" or layout.first_line_inset is not None
                or (not _single_line_text(layout.text, layout.det) and layout.det not in math_lines)):
            continue
        left, top, right, bottom = layout.det
        height, width = bottom - top, right - left
        # A long indented line may occupy most of its column. Compare the
        # extra margin to glyph height rather than requiring a width ratio.
        candidates = []
        for other in layouts[max(0, index - 3):index + 4]:
            if other.ref != "text" or other is layout:
                continue
            a, b, c, d = other.det
            if (c - a >= width + 0.75 * height and a <= left and c >= right
                    and (d <= top or b >= bottom)
                    and not _single_line_text(other.text, other.det) and other.det not in math_lines):
                candidates.append((max(top - d, b - bottom), a))
        if candidates:
            distance = min(value[0] for value in candidates)
            margins = [value[1] for value in candidates if value[0] == distance]
            if max(margins) - min(margins) <= 0.3 * height and left - max(margins) >= 0.75 * height:
                starts.add(layout.det)
    return starts


def _single_line(fragment: SourceTextFragment) -> bool:
    text = (fragment.source.text if fragment.source is not None
            else "".join(x for x in fragment.content if isinstance(x, str)))
    return _single_line_text(text, fragment.bbox)


def _single_line_text(text: str, bbox: tuple[int, int, int, int]) -> bool:
    # Math/HTML source lengths do not estimate printed advances; leave them to
    # the existing rule rather than treating markup characters as visible ink.
    if "\n" in text or "\\" in text or "<" in text:
        return False
    units = sum(1 if east_asian_width(char) in ("W", "F") else 0.5
                for char in text.strip())
    left, top, right, bottom = bbox
    height = bottom - top
    return (units >= 4 and height > 0
            and 0.6 * units * height <= right - left <= 1.8 * units * height)


def _math_single_lines(layouts: list[PageLayout]) -> set[tuple[int, int, int, int]]:
    """Use consistent neighboring line heights, never LaTeX source advances."""
    lines = set()
    for index, layout in enumerate(layouts):
        if (layout.ref != 'text' or layout.first_line_inset is not None
                or '\n' in layout.text or not ('\\(' in layout.text or '$' in layout.text)):
            continue
        left, top, right, bottom = layout.det
        height = bottom - top
        if height <= 0 or right - left < 3 * height:
            continue
        references = []
        for position in range(max(0, index - 3), min(len(layouts), index + 4)):
            other = layouts[position]
            if other is layout or other.ref != 'text' or not _single_line_text(other.text, other.det):
                continue
            a, b, c, d = other.det
            if ((d <= top or b >= bottom)
                    and min(right, c) - max(left, a) >= .5 * min(right - left, c - a)):
                references.append((d - b, abs(position - index), max(top - d, b - bottom)))
        if len(references) >= 2:
            heights = [reference[0] for reference in references]
            scale = median(heights)
            if (min(heights) >= .8 * scale and max(heights) <= 1.2 * scale
                    and .6 * scale <= height <= 1.5 * scale):
                lines.add(layout.det)
        elif len(references) == 1:
            scale, steps, gap = references[0]
            if steps == 1 and gap <= 2 * scale and .8 * scale <= height <= 1.25 * scale:
                lines.add(layout.det)
    return lines


_ISBN_ROW = re.compile(r'^ISBN(?:-1[03])?\s*[:：]?\s*([0-9Xx][0-9Xx -]{8,24})$', re.I)
_EDITION_ROW = re.compile(r'^[0-9]{4}年[0-9]{1,2}月第[一二三四五六七八九十0-9]+版(?:\s|[0-9]|$)')
_PRINT_COUNT_ROW = re.compile(r'^(?:印数|印數)\s*[:：]\s*[0-9][0-9\s,，—–\-]*[册冊]\s*$')


def colophon_record_starts(layouts: list[PageLayout]) -> set[tuple[int, int, int, int]]:
    """Keep explicit Chinese edition/count rows apart in an ISBN colophon.

    Require both record conventions and an isolated ISBN-shaped source. A bare
    date, a prose mention, or a multiline bbox does not establish this context.
    This is local printed structure, not a book metadata dependency.
    """
    text = [layout for layout in layouts if layout.ref == 'text']
    if not any((match := _ISBN_ROW.fullmatch(layout.text.strip()))
               and len(re.sub('[^0-9Xx]', '', match[1])) in (10, 13) for layout in text):
        return set()
    editions = [layout for layout in text if _EDITION_ROW.match(layout.text.strip())
                and _single_line_text(layout.text, layout.det)]
    counts = [layout for layout in text if _PRINT_COUNT_ROW.fullmatch(layout.text.strip())
              and _single_line_text(layout.text, layout.det)]
    return {layout.det for layout in editions + counts} if editions and counts else set()


_MATH_CONNECTIVE = re.compile(r'(?:whence|or|so that|and|hence|then|thus|where)', re.I)


def completed_equation_rows(layouts: list[PageLayout]) -> dict[tuple[int, int, int, int], int]:
    """Find a short connective beside the end of a punctuated equation row.

    OCR may group multiple equations before the word printed beside their last
    row. Its last literal expression and actual box supply completion evidence;
    the caller must still require a separately observed next-paragraph indent.
    """
    formulas = []
    for layout in layouts:
        if layout.ref not in ('equation', 'formula'):
            continue
        parts = [part for part in parse_latex_expressions(layout.text) if part.kind != ExpressionKind.TEXT]
        if parts and parts[-1].content.rstrip().endswith('.'):
            formulas.append(layout)
    rows = {}
    for layout in layouts:
        if layout.ref != 'text' or not _MATH_CONNECTIVE.fullmatch(layout.text.strip()):
            continue
        _, top, right, bottom = layout.det
        height = bottom - top
        if height <= 0:
            continue
        matching = [item for item in formulas if right + height <= item.det[0]
                    and item.det[1] <= top and bottom <= item.det[3]
                    and item.det[3] - bottom <= 2 * height]
        if len(matching) == 1:
            rows[layout.det] = matching[0].det[3]
    return rows
