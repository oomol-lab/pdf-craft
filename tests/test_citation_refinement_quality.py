"""Unresolved OCR guesses must not prevent semantic footnote repair."""
# pylint: disable=protected-access,unused-argument
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import fromstring

from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.page_repair import _validate_shared_citations, _validate_reading_boundary
from pdf_craft.extractor.chapter.primary import PrimaryPages
from pdf_craft.extractor.chapter.page_analysis import project_definition_mark, _restore_fragment
from pdf_craft.pdf import decode
from tests.test_page_repair import _page_with_reference


class CitationRefinementQualityTests(TestCase):
    def test_reference_display_metadata_is_not_translated_or_double_counted(self):
        from pdf_craft.document.content import ContentItem
        from pdf_craft.extractor.chapter.chapter import Reference, TextFlowItem, SourceTextFragment
        from pdf_craft.transformer.content import _unit
        from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
        from pdf_craft.transformer.pcex_schedule import PCEXSchedule
        flow = TextFlowItem('body', -1, [SourceTextFragment(1,0,(0,0,100,20),
                                                          ['Body', Reference(1,1,'1',[])])])
        source = ContentItem('chapter_head.xml', None, -1, 0, flow)
        unit = _unit(source)
        self.assertEqual(NarrativeXMLTransformer.source_word_count(unit), 1)
        combined, _, _ = PCEXSchedule.combine(source, [('body', (unit, []))])
        self.assertEqual(combined.value.children[0].content[-1].mark, '1')

    def test_explicit_note_continuation_can_start_a_new_paragraph(self):
        first = _page_with_reference()
        second = deepcopy(first)
        second.page_index = 2
        second.citations = [replace(first.citations[0], citation_id='pending', page_index=2,
                                    index=None, mark=None, origin_page_index=None, continues_previous=True)]
        second.layouts = [replace(item, source=replace(item.source, page_index=2), annotations=replace(
            item.annotations, citation_id='pending', citation_index=None, references=[],
            continues_from_previous=False)) for item in second.layouts if item.ownership == 'citation']
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'content.sqlite')
            assembly.append(first)
            assembly.append(second, refined=True)
            ref, _ = next(iter(assembly.notes.values()))
            assembly.finish()
            self.assertEqual(len(ref.flow_items), 2)
            self.assertEqual([item.children[0].page_index for item in ref.flow_items], [1, 2])

    def test_explicit_graphic_keeps_source_identity_and_original_pixels(self):
        from PIL import Image
        from pdf_craft.extractor.page_graphics import preserve_graphics
        page = PrimaryPages().append(decode(fromstring(
            '<page index="1"><body><layout ref="text" det="10,10,40,30">'
            'nonlinear labels</layout></body><footnotes/></page>')))
        original = page.layouts[0]
        original.annotations.preserve_as_image = True
        class Document:
            def render_page(self, index, dpi):
                return Image.new('RGB', (100, 100), 'red')
            def close(self):
                pass
        class Handler:
            def open(self, path):
                return Document()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            result = preserve_graphics(page, root/'source.pdf', Handler(), (100,100), root, 72, lambda: None)
            layout = result.layouts[0]
            self.assertEqual(layout.source.ref, 'image')
            self.assertEqual(layout.source.origin, original.source.origin)
            self.assertEqual(layout.document_order, original.document_order)
            self.assertIsNone(layout.source_order)
            with Image.open(root/f'{layout.source.asset_hash}.png') as image:
                self.assertEqual(image.size, (30,20))
                self.assertEqual(image.getpixel((0,0)), (255,0,0))
            self.assertEqual(page.layouts[0].source_order, 0)

    def test_streamed_body_keeps_printed_reference_mark_without_copying_definition(self):
        from pdf_craft.document.content import file_content
        from pdf_craft.extractor.chapter.chapter import Reference, TextFlowItem
        with TemporaryDirectory() as directory:
            path = Path(directory)/'chapter_head.xml'
            path.write_text('<chapter><flow><text role="body"><fragment page_index="1" source_order="0" '
                            'bbox="0,0,100,20">Body<ref id="1-1"/></fragment></text></flow>'
                            '<references><ref id="1-1"><mark>①</mark><flow><text role="body">'
                            '<fragment page_index="1" source_order="1" bbox="0,30,100,50">Note.</fragment>'
                            '</text></flow></ref></references></chapter>')
            items = iter(file_content(path))
            next(items)  # chapter envelope
            body = next(items).value
            assert isinstance(body, TextFlowItem)
            occurrence = body.children[0].content[-1]
            assert isinstance(occurrence, Reference)
            self.assertEqual(str(occurrence.mark), '①')
            self.assertEqual(occurrence.flow_items, [])
            self.assertIsInstance(next(items).value, Reference)

    def test_known_shared_origin_remains_protected(self):
        page = _page_with_reference()
        page.citations[0].origin_page_index = None  # An indexed definition is known without this derived hint.
        candidate = deepcopy(page)
        candidate.citations.clear()
        with self.assertRaisesRegex(ValueError, 'CROSS_PAGE_IDENTITY'):
            _validate_shared_citations(page, candidate, {page.citations[0].citation_id})

    def test_formula_wrapped_printed_mark_is_projected_once_and_reversible(self):
        page = PrimaryPages().append(decode(fromstring(
            '<page index="1"><body><layout ref="text" det="0,0,100,20">'
            r'\(①\) Note text.</layout></body><footnotes/></page>')))
        source = page.layouts[0]
        note = replace(source, annotations=replace(source.annotations, ownership='citation'))
        projected = project_definition_mark(note, '①')
        self.assertEqual(_restore_fragment(projected, {}).content, ['Note text.'])
        restored = project_definition_mark(source, None)
        self.assertEqual(restored.source.content, source.source.content)
        self.assertEqual(len(_restore_fragment(restored, {}).content), 2)

    def test_bracketed_main_flow_cannot_detour_through_a_recognized_note_band(self):
        primary = PrimaryPages()
        first = primary.append(decode(fromstring(
            '<page index="1"><body>'
            '<layout ref="text" det="0,0,100,20">The argument (including</layout>'
            '<layout ref="text" det="0,40,100,50">① An explanatory note.</layout>'
            '<layout ref="text" det="0,50,100,70">Another way to explain that note.</layout>'
            '</body><footnotes/></page>')))
        second = primary.append(decode(fromstring(
            '<page index="2"><body><layout ref="text" det="0,0,100,20">'
            'this conclusion) continues.</layout></body><footnotes/></page>')))
        first.layouts[1] = replace(first.layouts[1], annotations=replace(
            first.layouts[1].annotations, ownership='ignored'))
        with self.assertRaisesRegex(ValueError, 'READING_FLOW'):
            _validate_reading_boundary(first, second)
        first.layouts[2] = replace(first.layouts[2], annotations=replace(
            first.layouts[2].annotations, ownership='ignored'))
        _validate_reading_boundary(first, second)
        _validate_reading_boundary(first, None)

    def test_unresolved_shared_guess_can_be_removed(self):
        page = _page_with_reference()
        page.citations[0].index = None
        page.citations[0].origin_page_index = None
        candidate = deepcopy(page)
        candidate.citations.clear()
        candidate.layouts = [replace(item, annotations=replace(
            item.annotations, ownership='paragraph', citation_id=None,
            citation_index=None, references=[])) for item in candidate.layouts]
        _validate_shared_citations(page, candidate, {page.citations[0].citation_id})

    def test_ordered_assembly_binds_unresolved_incoming_note_to_adopted_origin(self):
        first = _page_with_reference()
        identifier = first.citations[0].citation_id
        second = deepcopy(first)
        second.page_index = 2
        second.citations = [replace(first.citations[0], citation_id='local-unknown',
                                    page_index=2, index=None, mark=None, origin_page_index=None)]
        second.layouts = [replace(item, source=replace(item.source, page_index=2),
                                  annotations=replace(item.annotations, citation_id='local-unknown',
                                                      citation_index=None, references=[],
                                                      continues_from_previous=True))
                          for item in second.layouts if item.ownership == 'citation']
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'content.sqlite')
            assembly.append(first)
            assembly.append(second)
            self.assertEqual(list(assembly.notes), [identifier])
            adopted = pickle.loads(assembly._run('SELECT value FROM adopted WHERE page=2')[0][0])
            self.assertEqual(adopted.citations[0].citation_id, identifier)
            self.assertEqual(adopted.citations[0].origin_page_index, 1)
            self.assertEqual(second.citations[0].citation_id, 'local-unknown')
            ref, _ = assembly.notes[identifier]
            assembly.finish()
            fragments = [fragment for item in ref.flow_items for fragment in item.children]
            self.assertEqual([fragment.page_index for fragment in fragments], [1, 2])

    def test_validated_unmarked_note_does_not_fall_back_into_body(self):
        page = _page_with_reference()
        page.citations[0].index = None
        page.citations[0].mark = None
        page.citations[0].origin_page_index = None
        page.layouts = [replace(item, annotations=replace(item.annotations, citation_index=None))
                        for item in page.layouts if item.ownership == 'citation']
        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'content.sqlite')
            assembly.append(page, refined=True)
            self.assertEqual(len(assembly.notes), 1)
            ref, _ = next(iter(assembly.notes.values()))
            self.assertEqual(ref.mark, '')
            self.assertEqual(ref.order, 1)
            self.assertFalse(assembly.body.pending)
            assembly.finish()
            from pdf_craft.extractor.chapter.chapter import Chapter, encode, decode as decode_chapter
            restored = decode_chapter(encode(Chapter(None, -1, [], [ref])))
            self.assertEqual(restored.definitions[0].mark, '')
