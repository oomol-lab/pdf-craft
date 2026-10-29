"""Preserve source graphics and fonts; flatten only retained visual Stamps."""
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, DictionaryObject, FloatObject, NameObject

from ...runtime import IO_DOMAIN
from .storage import compress_streams, deduplicate_objects


class OriginalVisualBaseCompiler:
    """Keep the original display list for subsequent character-level removal."""

    async def compile(self, source_path: Path, target_path: Path) -> None:
        await IO_DOMAIN.run(_compile, source_path, target_path)


def _compile(source_path: Path, target_path: Path) -> None:
    reader = PdfReader(source_path)
    writer = PdfWriter()
    for source in reader.pages:
        page: Any = writer.add_page(source)
        content = page.get_contents()
        if content is None:
            content = ContentStream(None, writer)
        operations = [([], b'q'), *content.operations, ([], b'Q')]
        resources = DictionaryObject(page['/Resources'])
        xobjects = DictionaryObject(resources.get('/XObject', DictionaryObject()).get_object())
        for item in page.get('/Annots', ()):
            annotation = item.get_object()
            appearance = annotation['/AP']['/N']
            # The PDF annotation appearance mapping fits the transformed BBox
            # to Rect. Its own Matrix is applied by the Form's Do operator.
            a, b, c, d, e, f = map(float, appearance.get('/Matrix', (1, 0, 0, 1, 0, 0)))
            x0, y0, x1, y1 = map(float, appearance['/BBox'])
            corners = [(a*x+c*y+e, b*x+d*y+f) for x in (x0, x1) for y in (y0, y1)]
            left, bottom = map(min, zip(*corners))
            right, top = map(max, zip(*corners))
            rx0, ry0, rx1, ry1 = map(float, annotation['/Rect'])
            if right <= left or top <= bottom:
                raise ValueError('Visual Stamp has an empty appearance box')
            sx, sy = (rx1-rx0)/(right-left), (ry1-ry0)/(top-bottom)
            name = NameObject(f'/PCCStamp{len(xobjects)}')
            while name in xobjects:
                name = NameObject(name + '_')
            xobjects[name] = appearance.indirect_reference
            transform = [FloatObject(v) for v in (sx, 0, 0, sy, rx0-sx*left, ry0-sy*bottom)]
            operations.extend([([], b'q'), (transform, b'cm'), ([name], b'Do'), ([], b'Q')])
        resources[NameObject('/XObject')] = xobjects
        page[NameObject('/Resources')] = resources
        page.pop('/Annots', None)
        content.operations = operations
        page.replace_contents(content)
    compress_streams(writer)
    deduplicate_objects(writer)
    writer.write(target_path)
