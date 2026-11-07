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
from pdf_craft.pdf.line_geometry import first_line_inset, printed_line_metrics


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
                # Recover the same raw observations as live extraction; never
                # overwrite an existing measurement or change OCR membership.
                for stream in ('body', 'footnotes'):
                    for order, layout in enumerate(tree.getroot().findall(f'{stream}/layout')):
                        if layout.get('ref') != 'text' or '<' in (layout.text or ''):
                            continue
                        bbox = _bbox(layout)
                        inset = None
                        if stream == 'body' and layout.get('first_line_inset') is None:
                            inset = first_line_inset(image, bbox, obstructions)
                            if inset is not None:
                                layout.set('first_line_inset', str(inset))
                        lines = printed_line_metrics(image, bbox)
                        if lines is not None:
                            for name, value in zip(('printed_line_count', 'printed_line_height', 'printed_line_lefts'),
                                                   (str(lines.line_count), str(lines.median_ink_height),
                                                    ','.join(map(str, lines.left_offsets)))):
                                if layout.get(name) is None:
                                    layout.set(name, value)
                        observations.append(dict(page=index, stream=stream, order=order, bbox=bbox,
                                                 first_line_inset=inset, printed_lines=lines, image_size=image.size))
            finally:
                image.close()
            tree.write(path, encoding='utf-8', xml_declaration=True)
    finally:
        document.close()
    ledger = dict(pdf=str(pdf_path.resolve()), pdf_sha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                  dpi=dpi, method='pdf_craft.pdf.line_geometry', observations=observations)
    (output.parent/'printed-evidence.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    return output
