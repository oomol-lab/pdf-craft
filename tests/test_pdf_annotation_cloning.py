"""PDF graph cuts must follow object roles, never recursive key names."""
# pylint: disable=protected-access,no-member,unsupported-assignment-operation
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject,
)

from pdf_craft.pipeline.pdf.storage import append_windows
from pdf_craft.pipeline.pdf.visual_base import (
    add_page_without_annotations, extract_annotations, reattach_annotations, write_annotation_free_copy,
)


RESOURCE_NAMES = ('/B', '/AA', '/Annots', '/P', '/A', '/Dest', '/Parent', '/Kids', '/D', '/Next', '/Keep')


def roundtrip(writer):
    stream = BytesIO()
    writer.write(stream)
    stream.seek(0)
    return PdfReader(stream)


def image_resources(writer):
    image = DecodedStreamObject()
    image.set_data(b'\x00')
    image.update({
        NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
        NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1),
        NameObject('/ColorSpace'): NameObject('/DeviceGray'), NameObject('/BitsPerComponent'): NumberObject(8),
    })
    reference = writer._add_object(image)
    return DictionaryObject({NameObject('/XObject'): DictionaryObject({
        NameObject(name): reference for name in RESOURCE_NAMES
    })})


def annotation(writer, subtype, full_page=False) -> Any:
    appearance = DecodedStreamObject()
    appearance.set_data(' '.join(f'{name} Do' for name in RESOURCE_NAMES).encode())
    appearance.update({
        NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
        NameObject('/BBox'): ArrayObject([NumberObject(n) for n in (0, 0, 100, 100)]),
        NameObject('/Resources'): image_resources(writer),
    })
    return writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject(subtype),
        NameObject('/Rect'): ArrayObject([NumberObject(n) for n in (0, 0, 100 if full_page else 20, 100)]),
        NameObject('/P'): writer.pages[0].indirect_reference,
        NameObject('/AP'): DictionaryObject({NameObject('/N'): writer._add_object(appearance)}),
    }))


def page_count(writer):
    return sum(isinstance(obj, dict) and obj.get('/Type') == '/Page' for obj in writer._objects)


class PDFAnnotationCloningTests(unittest.TestCase):
    def test_visual_and_fallback_pages_preserve_colliding_resource_names(self):
        source = PdfWriter()
        page: Any = source.add_blank_page(100, 100)
        hidden = source.add_blank_page(100, 100).indirect_reference
        page[NameObject('/Resources')] = image_resources(source)
        content = DecodedStreamObject()
        content.set_data(b'/B Do /AA Do /Annots Do')
        page[NameObject('/Contents')] = source._add_object(content)
        # Navigation edges would import a hidden page if cut after cloning.
        for name in ('/AA', '/B', '/Annots'):
            page[NameObject(name)] = ArrayObject([hidden])
        source.remove_page(1)
        reader = roundtrip(source)
        detached = PdfWriter()
        add_page_without_annotations(detached, reader.pages[0], visual_only=True)
        self.assertEqual(page_count(detached), 1)  # Check before any unreachable-object pruning.
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'visual.pdf'
            write_annotation_free_copy(reader, path)
            visual = PdfReader(path)
            self.assertEqual(len(visual.pages), 1)
            result: Any = visual.pages[0]
            self.assertTrue(all(name not in result for name in ('/AA', '/B', '/Annots')))
            self.assertEqual(set(result['/Resources']['/XObject']), set(RESOURCE_NAMES))
            self.assertIn(b'/B Do', result.get_contents().get_data())
            self.assertIn('/B', reader.pages[0])
            self.assertIn('/Annots', reader.pages[0])
        # Both direct fallback and final window assembly omit only root Annots.
        for assemble in (False, True):
            writer = PdfWriter()
            if assemble:
                append_windows(writer, {}, reader)
            else:
                add_page_without_annotations(writer, reader.pages[0])
            result: Any = roundtrip(writer).pages[0]
            self.assertNotIn('/Annots', result)
            self.assertEqual(set(result['/Resources']['/XObject']), set(RESOURCE_NAMES))

    def test_stamp_and_widget_appearances_survive_without_importing_form_siblings(self):
        for subtype, full_page in (('/Stamp', False), ('/Stamp', True), ('/Widget', False)):
            with self.subTest(subtype=subtype, full_page=full_page), TemporaryDirectory() as directory:
                source = PdfWriter()
                page: Any = source.add_blank_page(100, 100)
                hidden = source.add_blank_page(100, 100).indirect_reference
                annot = annotation(source, subtype, full_page)
                page[NameObject('/Annots')] = ArrayObject([annot])
                if subtype == '/Widget':
                    sibling = source._add_object(DictionaryObject({NameObject('/P'): hidden}))
                    parent = source._add_object(DictionaryObject({
                        NameObject('/Kids'): ArrayObject([annot, sibling]),
                        NameObject('/T'): TextStringObject('selected'),
                        NameObject('/DR'): image_resources(source),
                    }))
                    annot.get_object()[NameObject('/Parent')] = parent
                    annot.get_object()[NameObject('/Kids')] = ArrayObject([sibling])
                    source._root_object[NameObject('/AcroForm')] = DictionaryObject({
                        NameObject('/Fields'): ArrayObject([parent]),
                    })
                source.remove_page(1)
                reader = roundtrip(source)
                path = Path(directory) / 'visual.pdf'
                write_annotation_free_copy(reader, path)
                writer = PdfWriter(clone_from=path)
                self.assertEqual(page_count(writer), 1)
                if not full_page:
                    reattach_annotations(writer, extract_annotations(reader),
                                         [reader.pages[0].indirect_reference],
                                         cast(Any, reader.trailer['/Root']).get('/AcroForm'))
                self.assertEqual(page_count(writer), 1)
                result = roundtrip(writer)
                restored: Any = cast(Any, result.pages[0])['/Annots'][0].get_object()
                self.assertEqual(set(restored['/AP']['/N']['/Resources']['/XObject']), set(RESOURCE_NAMES))
                if subtype == '/Widget':
                    self.assertNotIn('/Kids', restored)
                    parent = restored['/Parent']
                    self.assertEqual(len(parent['/Kids']), 1)
                    self.assertEqual(set(parent['/DR']['/XObject']), set(RESOURCE_NAMES))

    def test_next_action_graph_remaps_destinations_and_preserves_sharing_and_cycles(self):
        source = PdfWriter()
        source.add_blank_page(100, 100)
        source.add_blank_page(100, 100)
        hidden = source.add_blank_page(100, 100).indirect_reference
        goto: Any = source._add_object(DictionaryObject({
            NameObject('/S'): NameObject('/GoTo'),
            NameObject('/D'): ArrayObject([source.pages[1].indirect_reference, NameObject('/Fit')]),
        }))
        named: Any = source._add_object(DictionaryObject({
            NameObject('/S'): NameObject('/GoTo'), NameObject('/D'): TextStringObject('chapter'),
        }))
        invalid = source._add_object(DictionaryObject({
            NameObject('/S'): NameObject('/GoTo'), NameObject('/D'): ArrayObject([hidden, NameObject('/Fit')]),
        }))
        uri = source._add_object(DictionaryObject({
            NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject('https://example.org'),
            NameObject('/Next'): ArrayObject([goto, named, invalid, goto]),
            NameObject('/Metadata'): DictionaryObject({NameObject('/D'): TextStringObject('nested data')}),
        }))
        goto.get_object()[NameObject('/Next')] = uri
        named.get_object()[NameObject('/Next')] = goto
        annot = annotation(source, '/Link')
        annot.get_object()[NameObject('/A')] = uri
        annot.get_object()[NameObject('/AA')] = DictionaryObject({NameObject('/E'): goto})
        source.pages[0][NameObject('/Annots')] = ArrayObject([annot])
        source.remove_page(2)
        reader = roundtrip(source)
        writer = PdfWriter()
        for _ in reader.pages:
            writer.add_blank_page(100, 100)
        reattach_annotations(writer, extract_annotations(reader),
                             [page.indirect_reference for page in reader.pages],
                             source_named_destinations={'chapter': ArrayObject([
                                 reader.pages[1].indirect_reference, NameObject('/Fit'),
                             ])})
        self.assertEqual(page_count(writer), 2)
        result = roundtrip(writer)
        restored: Any = cast(Any, result.pages[0])['/Annots'][0].get_object()
        action = restored['/A']
        following = action['/Next']
        self.assertEqual(len(following), 3)
        self.assertEqual(following[0], following[2])
        self.assertEqual(restored['/AA'].raw_get('/E'), following[0])
        self.assertEqual(following[0].get_object().raw_get('/Next'), action.indirect_reference)
        self.assertEqual(following[1].get_object().raw_get('/Next'), following[0])
        for child in following:
            self.assertEqual(child.get_object()['/D'][0], result.pages[1].indirect_reference)
        self.assertEqual(action['/Metadata']['/D'], 'nested data')
