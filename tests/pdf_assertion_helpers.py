"""Small generated PDF fixtures for backend assertions and configuration tests."""
from typing import cast
import pypdf
from pypdf.generic import NameObject, NumberObject, RectangleObject


def write_pdf(path, *, labelled=False, pages=2):
    writer = pypdf.PdfWriter()
    writer.add_metadata({'/Title': 'Preflight title', '/Author': 'One; Two'})
    for index in range(pages):
        page = writer.add_blank_page(200 + index * 40, 300 + index * 40)
        if index == 1:
            page.mediabox = RectangleObject((10, 20, 250, 360))
            page.cropbox = RectangleObject((20, 30, 230, 340))
            page[NameObject('/Rotate')] = NumberObject(90)
    if labelled:
        writer.set_page_label(0, pages - 1, cast(pypdf.constants.PageLabelStyle, pypdf.constants.PageLabelStyle.UPPERCASE_ROMAN))
    writer.write(path)
    writer.close()
