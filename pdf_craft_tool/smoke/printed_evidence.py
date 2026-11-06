"""Recover raw printed-line observations for historical OCR replays.

Current PDF extraction already measures these fields. Historical OCR captures
can recover the same physical evidence without repeating OCR or semantic JEV.
Never infer ownership/paragraph answers here or edit the original fixtures.
"""
import hashlib
import json
from pathlib import Path
import shutil
from xml.etree import ElementTree

from pdf_craft.common import ASSET_TAGS
from pdf_craft.pdf.handler import DefaultPDFHandler
from pdf_craft.pdf.line_geometry import first_line_inset


def _bbox(layout) -> tuple[int, int, int, int]:
    result = tuple(map(int, layout.attrib['det'].split(',')))
    if len(result) != 4:
        raise ValueError('Invalid cached OCR rectangle for printed evidence.')
    return result


def recover_printed_evidence(ocr_path: Path, output: Path, pdf_path: Path, dpi: int) -> Path:
    if not pdf_path.is_file() or type(dpi) is not int or dpi <= 0:
        raise ValueError('Printed evidence requires an existing original PDF and positive integer DPI.')
    shutil.copytree(ocr_path, output)
    observations = []
    document = DefaultPDFHandler().open(pdf_path)
    try:
        for path in sorted(output.glob('page_*.xml')):
            tree = ElementTree.parse(path)
            index = int(tree.getroot().attrib['index'])
            obstructions = [_bbox(layout)
                            for layout in tree.getroot().iter('layout') if layout.get('ref') in ASSET_TAGS]
            image = document.render_page(index, dpi)
            try:
                # Match the current extractor: only plain OCR body text,
                # with no existing measurement. Notes/HTML remain untouched.
                for order, layout in enumerate(tree.getroot().findall('body/layout')):
                    if (layout.get('ref') != 'text' or layout.get('first_line_inset') is not None
                            or '<' in (layout.text or '')):
                        continue
                    bbox = _bbox(layout)
                    inset = first_line_inset(image, bbox, obstructions)
                    observations.append(dict(page=index, order=order, bbox=bbox,
                                             first_line_inset=inset, image_size=image.size))
                    if inset is not None:
                        layout.set('first_line_inset', str(inset))
            finally:
                image.close()
            tree.write(path, encoding='utf-8', xml_declaration=True)
    finally:
        document.close()
    ledger = dict(pdf=str(pdf_path.resolve()), pdf_sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                  dpi=dpi, method='pdf_craft.pdf.line_geometry.first_line_inset', observations=observations)
    (output.parent/'printed-evidence.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    return output
