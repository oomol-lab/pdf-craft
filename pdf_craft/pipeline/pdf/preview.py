"""Export final Qt decisions without retaining a window's placements in RAM."""
# pylint: disable=protected-access
import pickle
import sqlite3

from ...document.paragraph import PDFParagraphContent, PDFSourceRegion, TextLine, FormulaPlacement, EraseArea
from .text_layout import (_qt_modules, _ensure_qt_application, _LAYOUT_SCALE,
                          _HEADLINE_OVERFLOW_LINE_WIDTH, _python_index_for_utf16, _utf16_index_for_python)
from .geometry import region_in_page_points


def lines_for_placement(filler, placement):
    """Use the drawing path's QTextLayout and frozen widths/tops, not heuristics."""
    QtCore, QtGui = _qt_modules()
    _ensure_qt_application(QtGui)
    text = placement.assigned_text or placement.remaining_text
    layout = filler._create_layout(QtCore, QtGui, text, placement.style, placement.font_size, _LAYOUT_SCALE)
    width = (_HEADLINE_OVERFLOW_LINE_WIDTH if placement.allows_horizontal_overflow else
             placement.rectangle.width - 2 * placement.style.horizontal_padding) * _LAYOUT_SCALE
    x = placement.rectangle.x + placement.style.horizontal_padding
    result = []
    layout.beginLayout()
    try:
        for top in placement.line_tops:
            line = layout.createLine()
            if not line.isValid():
                raise RuntimeError('Qt layout changed while exporting paragraph')
            line.setLineWidth(width)
            start = _python_index_for_utf16(text, line.textStart())
            end = _python_index_for_utf16(text, line.textStart() + line.textLength())
            # Formula proxies are geometry only; their real vector atoms are separate.
            runs, cursor = [], start
            for formula in sorted(placement.formula_draws, key=lambda value: value.proxy_start):
                lo, hi = max(start, formula.proxy_start), min(end, formula.proxy_start + formula.proxy_length)
                if lo < hi:
                    if cursor < lo:
                        runs.append((cursor, lo))
                    cursor = hi
            if cursor < end:
                runs.append((cursor, end))
            for lo, hi in runs:
                position = line.cursorToX(_utf16_index_for_python(text, lo))
                left = position[0] if isinstance(position, tuple) else position
                result.append(TextLine(text[lo:hi].replace("\u00a0", " "), x + left / _LAYOUT_SCALE,
                                       top + line.ascent() / _LAYOUT_SCALE))
    finally:
        layout.endLayout()
    return result


class WindowPreview:
    def __init__(self, root, window, filler, page_sizes):
        self.window, self.filler, self.sizes = window, filler, page_sizes
        self.db = sqlite3.connect(root / 'preview.sqlite')
        self.db.execute('DROP TABLE IF EXISTS preview')
        self.db.execute('CREATE TABLE preview (paragraph INTEGER, page INTEGER, number INTEGER, value BLOB)')

    def page(self, page_index, contributions, erasures):
        masks = iter(erasures)
        for contribution in contributions:
            summary = self.window._summaries[contribution.paragraph_index]
            content = summary.replacement.preview
            for region in contribution.regions:
                mask = next(masks, None)
                if content is None:
                    continue
                source = next((source for source in content.regions if
                               source.page.page_index == page_index and
                               source.source_order_in_page == region.reading_order and source.bbox == region.bbox), None)
                if source is None:
                    continue
                width, height = self.sizes[page_index]
                sx, sy = source.page.width / width, source.page.height / height
                rectangle = region_in_page_points(region, width, height)
                placement = next((placement for placement in contribution.placements if
                                  placement.page_index == page_index and placement.rectangle == rectangle), None)
                lines, formulas, size = [], [], None
                if placement is not None:
                    lines = [TextLine(line.text, line.x * sx, line.baseline * sy)
                             for line in lines_for_placement(self.filler, placement)]
                    size = placement.font_size * sy
                    formulas = [FormulaPlacement(draw.latex, draw.x * sx, draw.baseline * sy, draw.descent * sy,
                                                  draw.pdf, sx / draw.scale, sy / draw.scale)
                                for draw in placement.formula_draws]
                areas = []
                if mask is not None:
                    r = mask.rectangle
                    areas.append(EraseArea((r.x*sx, r.top*sy, (r.x+r.width)*sx, r.bottom*sy), mask.color))
                value = PDFSourceRegion(source.page, source.source_order_in_page, source.bbox,
                                        size, lines, formulas, areas)
                self.db.execute('INSERT INTO preview VALUES (?,?,?,?)',
                                (contribution.paragraph_index, page_index, region.reading_order, pickle.dumps(value)))

    def contents(self, failed_pages):
        for index, summary in enumerate(self.window._summaries):
            content = summary.replacement.preview
            if content is None or any(region.page.page_index in failed_pages for region in content.regions):
                continue
            regions = []
            for source in content.regions:
                row = self.db.execute('SELECT value FROM preview WHERE paragraph=? AND page=? AND number=?',
                                      (index, source.page.page_index, source.source_order_in_page)).fetchone()
                regions.append(pickle.loads(row[0]) if row else
                               PDFSourceRegion(source.page, source.source_order_in_page, source.bbox, None, []))
            yield summary.replacement.preview_index, summary.replacement.preview_is_footnote, PDFParagraphContent(content.text, content.original, regions)

    def close(self):
        self.db.close()
