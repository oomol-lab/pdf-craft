"""Resource, content and link invariants for bounded PDF composition."""
# pypdf virtual page lists confuse pylint's overload inference.
# pylint: disable=protected-access,no-member,unsupported-assignment-operation
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, ContentStream, DecodedStreamObject, DictionaryObject,
    NameObject, NumberObject,
)

from pdf_craft.pipeline.pdf.patcher import PDFPatcher
from pdf_craft.pipeline.pdf.storage import append_windows, compress_streams
from pdf_craft.pipeline.pdf.visual_base import (
    extract_annotations, reattach_annotations, write_annotation_free_copy,
)


def reader_from(writer):
    stream = BytesIO()
    writer.write(stream)
    stream.seek(0)
    return PdfReader(stream)


def page_objects(writer):
    return sum(isinstance(obj, dict) and obj.get('/Type') == '/Page' for obj in writer._objects)


class PDFStorageTests(unittest.TestCase):
    def test_lazy_merged_content_survives_compression_and_roundtrip(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=100, height=100)
        content = ContentStream(None, writer)
        content.operations = [([], b'q'), ([NumberObject(1)] * 4, b're'), ([], b'f'), ([], b'Q')]
        page[NameObject('/Contents')] = writer._add_object(content)
        compress_streams(writer)
        result: Any = reader_from(writer).pages[0]
        self.assertEqual([operator for _, operator in result.get_contents().operations],
                         [b'q', b're', b'f', b'Q'])
        self.assertEqual(result['/Contents']['/Filter'], '/FlateDecode')

    def test_repeated_icc_streams_are_compressed_and_shared_on_write(self):
        writer = PdfWriter()
        data = b'color profile fixture ' * 50000
        for _ in range(3):
            page = writer.add_blank_page(width=100, height=100)
            profile = DecodedStreamObject()
            profile.set_data(data)
            profile[NameObject('/N')] = NumberObject(4)
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/ColorSpace'): DictionaryObject({
                    NameObject('/CS'): ArrayObject([NameObject('/ICCBased'), writer._add_object(profile)]),
                }),
            })
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'out.pdf'
            PDFPatcher._write_output(writer, path)
            self.assertLess(path.stat().st_size, 20000)
            result = PdfReader(path)
            profiles = [cast(Any, page['/Resources'])['/ColorSpace']['/CS'][1] for page in result.pages]
            self.assertEqual(len({ref.idnum for ref in profiles}), 1)
            self.assertEqual(profiles[0].get_object().get_data(), data)

    def test_window_clones_survive_reader_release_and_keep_page_order(self):
        source = PdfWriter()
        for width in (101, 102, 103):
            source.add_blank_page(width=width, height=100)
        source_reader = reader_from(source)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'window.pdf'
            source.write(path)
            writer = PdfWriter()
            append_windows(writer, {i: (path, i - 1) for i in (1, 2, 3)}, source_reader)
            self.assertFalse(writer._id_translated)
            result = reader_from(writer)
            self.assertEqual([float(page.mediabox.width) for page in result.pages], [101, 102, 103])

    def test_dangling_link_never_imports_hidden_page_tree(self):
        source = PdfWriter()
        for _ in range(3):
            source.add_blank_page(width=100, height=100)
        hidden = source.pages[2].indirect_reference
        valid = source.pages[1].indirect_reference
        annotations = ArrayObject()
        for destination in (hidden, valid):
            annotation = DictionaryObject({
                NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
                NameObject('/Rect'): ArrayObject([NumberObject(0)] * 4),
                NameObject('/A'): DictionaryObject({
                    NameObject('/S'): NameObject('/GoTo'),
                    NameObject('/D'): ArrayObject([destination, NameObject('/Fit')]),
                }),
            })
            annotations.append(source._add_object(annotation))
        source.pages[0][NameObject('/Annots')] = annotations
        source.remove_page(2)
        reader = reader_from(source)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'visual.pdf'
            write_annotation_free_copy(reader, path)
            writer = PdfWriter(clone_from=path)
            self.assertEqual(page_objects(writer), 2)
            reattach_annotations(writer, extract_annotations(reader),
                                 (page.indirect_reference for page in reader.pages))
            self.assertEqual(page_objects(writer), 2)
            result = reader_from(writer)
            links: Any = result.pages[0]['/Annots']
            self.assertNotIn('/A', links[0].get_object())
            self.assertEqual(links[1].get_object()['/A']['/D'][0], cast(Any, result.pages[1]).indirect_reference)

            # The same rule must hold when both pages are original-page
            # fallbacks rather than translated pages.
            raw = PdfWriter()
            for page in reader.pages:
                raw.add_page(page, excluded_keys=('/Annots',))
            reattach_annotations(raw, extract_annotations(reader),
                                 (page.indirect_reference for page in reader.pages), page_indexes=set())
            self.assertEqual(page_objects(raw), 2)
            raw_result = reader_from(raw)
            raw_links: Any = raw_result.pages[0]['/Annots']
            self.assertNotIn('/A', raw_links[0].get_object())
            self.assertEqual(raw_links[1].get_object()['/A']['/D'][0],
                             cast(Any, raw_result.pages[1]).indirect_reference)
