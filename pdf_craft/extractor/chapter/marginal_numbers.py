"""Conservative, reversible folio hypotheses from local OCR observations."""
import re

from ...common import ASSET_TAGS
from ...pdf import TITLE_TAGS
from .jointer import _parse_block_content
from .numeric_marks import explicit_numbers
from .mark import Mark, search_marks

_CHINESE_DIGITS = str.maketrans('〇零一二三四五六七八九', '00123456789')
_SHORT_NUMBER = re.compile(r'(?:[0-9]{1,3}|[〇零一二三四五六七八九]{1,3})')

def marginal_numbers(page):
    """Select isolated outer folios or single-line centered bottom folios.

    A position is insufficient. Centered folios require a larger blank gap and
    numeric-line proportions. Side folios need observed vertical writing.
    The caller must retain the excluded observation and its original identity.
    """
    # Four-digit tokens may be years; leave that domain to semantic review.
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and _SHORT_NUMBER.fullmatch(item.text)]
    if not candidates:
        return []
    text_kinds = ('text', *TITLE_TAGS)
    body = [item for item in page.body_layouts if item.ref in text_kinds
            and any(char.isalnum() for char in item.text)]
    if not body:
        return []
    vertical = [item for item in body if item.vertical_columns]
    vertical_writing = len(vertical) >= 2 and sum(len(item.text.strip()) for item in vertical) > (
        sum(len(item.text.strip()) for item in body) / 2)
    multiline = any(item.ref == 'text' and item.first_line_inset is not None for item in body)
    left, right = min(item.det[0] for item in body), max(item.det[2] for item in body)
    top, bottom = min(item.det[1] for item in body), max(item.det[3] for item in body)
    all_layouts = [*page.body_layouts, *page.footnotes_layouts]
    text = ' '.join(item.text for item in body)
    referenced = {mark for item in body for mark in explicit_numbers(_parse_block_content(item.text))}
    referenced.update(str(mark.number) for mark in search_marks(text) if isinstance(mark, Mark))
    found = []
    for item in candidates:
        chinese = not item.text.isascii()
        if chinese and not vertical_writing:
            continue
        x1, y1, x2, y2 = item.det
        width, height = x2 - x1, y2 - y1
        centered = (not chinese and height > 0
                    and abs((x1 + x2) - (left + right)) <= 0.12 * (right - left)
                    and y1 - bottom >= 2 * height
                    and 0.15 * len(item.text) * height <= width <= 1.25 * len(item.text) * height)
        if (x1 >= x2 or y1 >= y2 or not (x2 <= left or x1 >= right or centered)
                or bottom - top < (3 if multiline else 6) * (y2 - y1)
                or right - left < 4 * (x2 - x1)):
            continue
        number = item.text.translate(_CHINESE_DIGITS).lstrip('0') or '0'
        if (number in referenced or re.search(r'(?<![0-9])' + re.escape(number) + r'(?![0-9])', text)
                or (chinese and item.text in ''.join(text.split()))):
            continue
        if not vertical_writing and any(other is not item and other.ref in text_kinds and other.text.strip()
                                      and other.det[3] > y1 for other in all_layouts):
            continue
        if any(other.ref in (*ASSET_TAGS, 'formula', 'equation')
               and min(x2, other.det[2]) > max(x1, other.det[0])
               and min(y2, other.det[3]) > max(y1, other.det[1]) for other in all_layouts):
            continue
        found.append(item)
    return found


def reading_notes(page):
    """Filter a projection without editing the raw Page or its source offsets."""
    ignored = {id(item) for item in marginal_numbers(page)}
    return [item for item in page.footnotes_layouts if id(item) not in ignored]
