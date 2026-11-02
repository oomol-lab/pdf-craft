"""Conservative, reversible page-apparatus hypotheses from local OCR."""
from copy import deepcopy
from collections.abc import Iterable, Iterator, Mapping

import re
from statistics import median

from ...common import ASSET_TAGS
from ...pdf import TITLE_TAGS, Page
from .content import _parse_block_content
from .chapter import SourceTextFragment, TextSource
from .source import _is_empty_text_layout
from .paragraph_geometry import text_scale_estimate
from .numeric_marks import explicit_numbers, flattened_numbers
from .mark import Mark, search_marks

_CHINESE_DIGITS = str.maketrans('〇零一二三四五六七八九', '00123456789')
_SHORT_NUMBER = re.compile(r'(?:[0-9]{1,3}|[〇零一二三四五六七八九]{1,3})')
_CHAPTER_FOOTER = re.compile(
    r'^([1-9][0-9]{0,2})\s+(?:chapter|chapitre|kapitel|cap[ií]tulo|capitolo)\s+'
    r'(?:[1-9][0-9]{0,2}|[IVXLCDM]+)\s+([\w-]+(?:\s+[\w-]+){0,5})$', re.I)


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
        x1, y1, x2, y2 = item.number_ink_bbox or item.det
        if not (item.det[0] <= x1 < x2 <= item.det[2] and item.det[1] <= y1 < y2 <= item.det[3]):
            continue
        width, height = x2 - x1, y2 - y1
        centered = (not chinese and height > 0
                    and abs((x1 + x2) - (left + right)) <= 0.12 * (right - left)
                    and y1 - bottom >= 1.5 * height
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
    ignored = {id(item) for item in marginal_layouts(page)}
    return [item for item in page.footnotes_layouts if id(item) not in ignored]


def marginal_layouts(page):
    """Share exclusions between note selection and retained ignored sources."""
    candidates = [*marginal_numbers(page), *_marginal_codes(page), *_chapter_footers(page),
                  *_side_folios(page), *_stacked_folios(page), *_table_codes(page), *_sole_apparatus(page)]
    return list({id(item): item for item in candidates}.values())


def _marginal_codes(page):
    """A short outer code needs a substantial, unreferenced main reading band.

    This is a reversible initial hypothesis. Keep the raw source for review;
    uppercase alone, an illustration caption, or a note-bearing page is not
    sufficient evidence. No book-specific language codes are recognised.
    """
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and re.fullmatch(r'[A-Z]{2,5}', item.text)
                  and not re.fullmatch(r'[IVXLCDM]+', item.text)]
    if len(candidates) != 1:
        return []
    candidate = candidates[0]
    if any(item is not candidate and any(char.isalnum() for char in item.text)
           and not _SHORT_NUMBER.fullmatch(item.text) for item in page.footnotes_layouts):
        return []
    body = [item for item in page.body_layouts if item.ref == 'text'
            and any(char.isalnum() for char in item.text)]
    if len(body) < 2 or sum(len(item.text) for item in body) < 150:
        return []
    if any(explicit_numbers(_parse_block_content(item.text))
           or flattened_numbers(_parse_block_content(item.text))
           or any(isinstance(mark, Mark) for mark in search_marks(item.text))
           or any(mark in item.text for mark in ('*', '†', '‡', '<sup', '^{'))
           or re.search(r'\b' + re.escape(candidate.text) + r'\b', item.text)
           for item in body):
        return []
    left, right = min(item.det[0] for item in body), max(item.det[2] for item in body)
    top = min(item.det[1] for item in body)
    bottom = max(item.det[3] for item in page.body_layouts)
    x1, y1, x2, y2 = candidate.det
    height, width = y2 - y1, x2 - x1
    if (height > 0 and 0 < width <= 5 * height and y1 - bottom >= 4 * height
            and y1 - top >= 15 * height and (x2 <= left or x1 >= right)):
        return [candidate]
    return []


def _chapter_footers(page):
    """Require both a bounded footer grammar and an isolated outer reading band.

    This local hypothesis never depends on a whole-book header inventory.
    Real reference typography or other note content keeps the source for review.
    """
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and _CHAPTER_FOOTER.fullmatch(item.text)]
    if len(candidates) != 1:
        return []
    candidate = candidates[0]
    if any(item is not candidate and any(char.isalnum() for char in item.text)
           for item in page.footnotes_layouts):
        return []
    body = [item for item in page.body_layouts if item.ref == 'text'
            and any(char.isalnum() for char in item.text)]
    if len(body) < 2 or sum(len(item.text) for item in body) < 150:
        return []
    if any(explicit_numbers(_parse_block_content(item.text))
           or flattened_numbers(_parse_block_content(item.text))
           or any(isinstance(mark, Mark) for mark in search_marks(item.text))
           or any(mark in item.text for mark in ('*', '†', '‡', '<sup'))
           or re.search(r'(?:^|[\s($])(?:\{\s*\})?\^\{', item.text)
           for item in body):
        return []
    left, right = min(item.det[0] for item in body), max(item.det[2] for item in body)
    top = min(item.det[1] for item in body)
    bottom = max(item.det[3] for item in page.body_layouts)
    x1, y1, x2, y2 = candidate.det
    height, width = y2 - y1, x2 - x1
    edge = ((x1 <= left + .5 * height and x2 <= left + .4 * (right - left))
            or (x2 >= right - .5 * height and x1 >= right - .4 * (right - left)))
    if (height > 0 and 3 * height <= width <= 30 * height
            and y1 - bottom >= height and y1 - top >= 15 * height and edge):
        return [candidate]
    return []


def _side_folios(page):
    """Recognize a single outer folio without treating raised/list marks as one.

    The shared glyph-area estimate is only a size gate. Isolation, reading-band
    position, absence of a first-line/list alignment and reference guards must
    independently hold; no neighboring refined state is consulted.
    """
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and re.fullmatch('[0-9]{1,3}', item.text)]
    if len(candidates) != 1:
        return []
    candidate = candidates[0]
    if any(item is not candidate and any(char.isalnum() for char in item.text)
           for item in page.footnotes_layouts):
        return []
    body = [item for item in page.body_layouts if item.ref == 'text'
            and any(char.isalnum() for char in item.text)]
    if len(body) < 2 or sum(len(item.text) for item in body) < 200:
        return []
    x1, y1, x2, y2 = candidate.det
    height, width = y2 - y1, x2 - x1
    if height <= 0 or not .15 * len(candidate.text) * height <= width <= 1.25 * len(candidate.text) * height:
        return []
    left, right = min(item.det[0] for item in body), max(item.det[2] for item in body)
    top, bottom = min(item.det[1] for item in body), max(item.det[3] for item in body)
    gap = left - x2 if x2 <= left else x1 - right if x1 >= right else -1
    if gap < height or y1 < top or y2 > bottom or bottom - top < 15 * height or right - left < 8 * width:
        return []
    scale = median(text_scale_estimate(item.text, item.det) for item in body)
    candidate_scale = text_scale_estimate(candidate.text, candidate.det)
    if not .6 * scale <= candidate_scale <= 1.1 * scale:
        return []
    center = (y1 + y2) / 2
    if any(item.det[1] - .5 * height <= center <= item.det[1] + 1.5 * height for item in body):
        return []
    if any(item.ref in (*ASSET_TAGS, 'formula', 'equation')
           and min(x2, item.det[2]) > max(x1, item.det[0])
           and min(y2, item.det[3]) > max(y1, item.det[1]) for item in page.body_layouts):
        return []
    number = candidate.text.lstrip('0') or '0'
    text = ' '.join(item.text for item in body)
    referenced = {mark for item in body for mark in explicit_numbers(_parse_block_content(item.text))}
    referenced.update(str(mark.number) for mark in search_marks(text) if isinstance(mark, Mark))
    if number in referenced or re.search(r'(?<![0-9])' + re.escape(number) + r'(?![0-9])', text):
        return []
    return [candidate]


def _stacked_folios(page):
    """A stacked side numeral supplies its own local orientation evidence."""
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and re.fullmatch('[〇零一二三四五六七八九]{2,3}', item.text)]
    if len(candidates) != 1:
        return []
    candidate = candidates[0]
    if any(item is not candidate and any(char.isalnum() for char in item.text)
           for item in page.footnotes_layouts):
        return []
    body = [item for item in page.body_layouts if item.ref == 'text'
            and any(char.isalnum() for char in item.text)]
    text = ' '.join(item.text for item in body)
    letters = [char for char in text if char.isalnum()]
    if len(letters) < 200 or sum('\u3400' <= char <= '\u9fff' for char in letters) < .8 * len(letters):
        return []
    left, right = min(item.det[0] for item in body), max(item.det[2] for item in body)
    top, bottom = min(item.det[1] for item in body), max(item.det[3] for item in body)
    x1, y1, x2, y2 = candidate.det
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height <= 0 or not .6 * width <= height / len(candidate.text) <= 1.5 * width:
        return []
    if (not (x2 <= left or x1 >= right) or y1 < top + .5 * (bottom - top)
            or y2 > bottom or bottom - top < 6 * height or right - left < 4 * width):
        return []
    if any(item.ref in (*ASSET_TAGS, 'formula', 'equation')
           and min(x2, item.det[2]) > max(x1, item.det[0])
           and min(y2, item.det[3]) > max(y1, item.det[1]) for item in page.body_layouts):
        return []
    number = candidate.text.translate(_CHINESE_DIGITS).lstrip('0') or '0'
    referenced = {mark for item in body for mark in explicit_numbers(_parse_block_content(item.text))}
    referenced.update(str(mark.number) for mark in search_marks(text) if isinstance(mark, Mark))
    if (number in referenced or candidate.text in ''.join(text.split())
            or re.search(r'(?<![0-9])' + re.escape(number) + r'(?![0-9])', text)):
        return []
    return [candidate]


def _table_codes(page):
    """Keep near captions and reference keys; recognize only remote table codes."""
    if len(page.body_layouts) != 1:
        return []
    table = page.body_layouts[0]
    if (table.ref != 'table' or len(table.text) < 150 or '<table' not in table.text
            or table.text.count('<tr') < 2):
        return []
    candidates = [item for item in page.footnotes_layouts
                  if item.ref == 'text' and re.fullmatch('[A-Z]{2,5}', item.text)
                  and not re.fullmatch('[IVXLCDM]+', item.text)]
    if len(candidates) != 1:
        return []
    candidate = candidates[0]
    if any(item is not candidate and any(char.isalnum() for char in item.text)
           for item in page.footnotes_layouts):
        return []
    text = table.text
    if (explicit_numbers(_parse_block_content(text)) or flattened_numbers(_parse_block_content(text))
            or any(isinstance(mark, Mark) for mark in search_marks(text))
            or any(mark in text for mark in ('*', '†', '‡', '<sup', '^{'))
            or re.search(r'\b' + re.escape(candidate.text) + r'\b', text)):
        return []
    left, top, right, bottom = table.det
    x1, y1, x2, y2 = candidate.det
    height, width = y2 - y1, x2 - x1
    aligned = x2 <= left or x1 >= right or abs(x1 - left) <= height or abs(x2 - right) <= height
    # A two-row table provides less repeated structure; require much stronger
    # whitespace and table scale instead of treating its nearby caption as code.
    rows = table.text.count('<tr')
    gap_ratio, table_ratio = (30, 20) if rows == 2 else (10, 10)
    if (height > 0 and 0 < width <= 5 * height and y1 - bottom >= gap_ratio * height
            and bottom - top >= table_ratio * height and right - left >= 10 * width and aligned):
        return [candidate]
    return []


def reading_body(page, *, includes_footnotes=True):
    """A paired apparatus-only page must not become a spurious body paragraph."""
    ignored = {id(item) for item in _sole_apparatus(page)} if includes_footnotes else set()
    return [item for item in page.body_layouts if id(item) not in ignored]


def _sole_apparatus(page):
    """Require a chapter-folio footer plus a remote, same-baseline code.

    Neither a sparse page nor a lone short label proves apparatus. Both OCR
    streams must independently describe the same isolated lower printing band.
    """
    if len(page.body_layouts) != 1 or len(page.footnotes_layouts) != 1:
        return []
    footer, code = page.body_layouts[0], page.footnotes_layouts[0]
    if (footer.ref != 'text' or not _CHAPTER_FOOTER.fullmatch(footer.text)
            or code.ref != 'text' or not re.fullmatch('[A-Z]{2,5}', code.text)
            or re.fullmatch('[IVXLCDM]+', code.text)):
        return []
    left, top, right, bottom = footer.det
    x1, y1, x2, y2 = code.det
    height, code_height = bottom - top, y2 - y1
    if (height <= 0 or code_height <= 0 or not .5 * height <= code_height <= 1.5 * height
            or not 3 * height <= right - left <= 30 * height
            or not 0 < x2 - x1 <= 5 * code_height):
        return []
    if (min(top, y1) >= 30 * max(height, code_height)
            and abs((top + bottom) - (y1 + y2)) <= max(height, code_height)
            and max(x1 - right, left - x2) >= 10 * max(height, code_height)):
        return [footer, code]
    return []


def excluded_sources(
    pages: Iterable[Page],
    sources: Mapping[tuple[int, int], tuple[int, TextSource]],
    *, includes_footnotes=False,
) -> Iterator[SourceTextFragment]:
    """Retain blank and excluded observations without semantic paragraphs."""
    for page in pages:
        ignored = {id(item) for item in marginal_layouts(page)} if includes_footnotes else set()
        for layout in [*page.body_layouts, *page.footnotes_layouts]:
            if _is_empty_text_layout(layout) or id(layout) in ignored:
                order, origin = sources[(page.index, id(layout))]
                yield SourceTextFragment(page.index, order, layout.det, list(deepcopy(origin.content)), origin)
