"""Conservative, reversible folio hypotheses from local OCR observations."""
import re

from ...common import ASSET_TAGS
from ...pdf import TITLE_TAGS
from .jointer import _parse_block_content
from .numeric_marks import explicit_numbers
from .mark import Mark, search_marks


def marginal_numbers(page):
    """Select only isolated short numbers outside both main reading axes.

    A page-bottom position is insufficient. Keep anything within text columns,
    beside continuing text, in an asset, or mentioned by the current body.
    The caller must retain the excluded observation and its original identity.
    """
    # Four-digit tokens may be years; leave that domain to semantic review.
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and re.fullmatch(r'[0-9]{1,3}', item.text)]
    if not candidates:
        return []
    text_kinds = ('text', *TITLE_TAGS)
    body = [item for item in page.body_layouts if item.ref in text_kinds
            and any(char.isalnum() for char in item.text)]
    if not body:
        return []
    left, right = min(item.det[0] for item in body), max(item.det[2] for item in body)
    top, bottom = min(item.det[1] for item in body), max(item.det[3] for item in body)
    all_layouts = [*page.body_layouts, *page.footnotes_layouts]
    text = ' '.join(item.text for item in body)
    referenced = {mark for item in body for mark in explicit_numbers(_parse_block_content(item.text))}
    referenced.update(str(mark.number) for mark in search_marks(text) if isinstance(mark, Mark))
    found = []
    for item in candidates:
        x1, y1, x2, y2 = item.det
        if (x1 >= x2 or y1 >= y2 or not (x2 < left or x1 > right)
                or bottom - top < 6 * (y2 - y1) or right - left < 4 * (x2 - x1)):
            continue
        number = item.text.lstrip('0') or '0'
        if number in referenced or re.search(r'(?<![0-9])' + re.escape(number) + r'(?![0-9])', text):
            continue
        if any(other is not item and other.ref in text_kinds and other.text.strip()
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
