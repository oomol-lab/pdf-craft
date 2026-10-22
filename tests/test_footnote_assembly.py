"""Whole-path contracts for complete sources, assembly and PDF translation."""

from pdf_craft import RetryPolicy
# pylint: disable=protected-access
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree.ElementTree import fromstring, parse

from pypdf import PageObject, PdfReader
from reportlab.pdfgen.canvas import Canvas

from pdf_craft.common import save_xml
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceTextFragment, TextFlowItem, decode as decode_chapter,
    encode, narrative_texts, search_references_in_chapter,
)
from pdf_craft.extractor.chapter.generation import _generate_chapters, _resolve_pages
from pdf_craft.extractor.chapter.page_analysis import analyse_pages, restore_streams
from pdf_craft.extractor.chapter.page_repair import (
    AllPageLlmRepairProcessor, RepairedPage, _compile_repaired_page, _page_packet,
)
from pdf_craft.extractor.chapter.page_review import build_jev_review_requests
from pdf_craft.extractor.toc.types import TocInfo
from pdf_craft.pdf import decode
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.transformer.package import ChapterExtractionTransformer
from pdf_craft.transformer.translation_coverage import read_coverage
from tests.extraction_helpers import make_extraction
from tests.test_footnote_contracts import fragment, response, source_page


class _TextTranslator:
    async def transform(self, chapter):
        chapter = deepcopy(chapter)
        for text in narrative_texts(chapter):
            for child in text.children:
                if isinstance(child, SourceTextFragment):
                    child.content = [part.replace('Source', 'Translated')
                                     if isinstance(part, str) else part for part in child.content]
        return chapter


class FootnoteAssemblyTests(unittest.IsolatedAsyncioTestCase):
    def test_all_supported_star_marks_keep_repeated_occurrences(self):
        for mark in ('*', '**', '***', '****', '*****', '******'):
            with self.subTest(mark=mark):
                page = source_page(mark)
                page.body_layouts[0].text = f'Body{mark}. Again{mark}.'
                paragraphs, citations = _resolve_pages([page])
                restored = restore_streams(analyse_pages([1], paragraphs, citations))
                paragraph = restored.paragraphs[0]
                assert isinstance(paragraph, TextFlowItem)
                occurrences = [part for child in paragraph.children for part in child.content
                               if isinstance(part, Reference)]
                self.assertEqual(len(occurrences), 2)
                self.assertIs(occurrences[0], occurrences[1])
                self.assertEqual(str(occurrences[0].mark), mark)

    def test_unmatched_definitions_and_unresolved_source_are_exported(self):
        page = source_page()
        page.body_layouts[0].text = 'No matching marker.'
        page.footnotes_layouts[0].text = 'Unresolved source.'
        # A separately indexed definition follows the unresolved source.
        page.footnotes_layouts.append(deepcopy(page.footnotes_layouts[0]))
        page.footnotes_layouts[1].order = 1
        page.footnotes_layouts[1].det = (0, 120, 100, 140)
        page.footnotes_layouts[1].text = '① Unmatched definition.'
        paragraphs, citations = _resolve_pages([page])
        pages = analyse_pages([1], paragraphs, citations)
        chapters = list(_generate_chapters(Path('/unused'), TocInfo([], []), pages))
        root = encode(chapters[0])
        self.assertIn('Unresolved source.', ''.join(root.itertext()))
        self.assertEqual(root.findtext('./references/ref/flow/text/fragment'),
                         'Unmatched definition.')
        reopened = decode_chapter(root, allow_legacy=False)
        self.assertEqual(len(list(search_references_in_chapter(reopened))), 1)
        self.assertEqual(len(encode(reopened).findall('./references/ref')), 1)

    def test_reclassification_preserves_column_order_and_source_facts(self):
        raw = decode(fromstring(
            '<page index="1"><body>'
            '<layout ref="text" det="0,0,100,20">A.</layout>'
            '<layout ref="text" det="0,70,100,90">C.</layout>'
            '<layout ref="text" det="140,0,240,20">D.</layout>'
            '<layout ref="text" det="140,70,240,90">F.</layout>'
            '</body><footnotes>'
            '<layout ref="text" det="140,35,240,55">① E.</layout>'
            '</footnotes></page>'))
        paragraphs, citations = _resolve_pages([raw])
        page = analyse_pages([1], paragraphs, citations)[0]
        proposed = response(page)
        proposed['citations'], proposed['references'] = [], []
        for layout in proposed['layouts']:
            layout.update(ownership='paragraph', citation_id=None, citation_index=None,
                          continues_from_previous=False, continues_to_next=False)
        repaired = _compile_repaired_page(page, RepairedPage.model_validate(proposed))
        restored = restore_streams([repaired])
        sources = [child for item in restored.paragraphs if isinstance(item, TextFlowItem)
                   for child in item.children if isinstance(child, SourceTextFragment)]
        self.assertEqual([child.content for child in sources],
                         [['A.'], ['C.'], ['D.'], ['① E.'], ['F.']])
        self.assertEqual([child.source_order for child in sources], [0, 1, 2, 4, 3])
        self.assertEqual(sources[3].bbox, (140, 35, 240, 55))
        origin = sources[3].source
        assert origin is not None
        self.assertEqual((origin.stream, origin.stream_order, origin.classification, origin.text),
                         ('footnote', 0, 'text', '① E.'))
        # JEV consumes the source record, never indexes OCR by persisted identity.
        packet = list(build_jev_review_requests([raw], [repaired], {1: (240, 150)}))[0][1]
        self.assertEqual(packet['state']['target_page']['layouts'][3]['raw_text'], '① E.')

    def test_full_width_reclassification_splits_column_reading_bands(self):
        page = decode(fromstring(
            '<page index="1"><body>'
            '<layout ref="text" det="0,0,100,20">Left above.</layout>'
            '<layout ref="text" det="0,70,100,90">Left below.</layout>'
            '<layout ref="text" det="140,0,240,20">Right above.</layout>'
            '<layout ref="text" det="140,70,240,90">Right below.</layout>'
            '</body><footnotes><layout ref="text" det="0,35,240,55">① Spanning.</layout>'
            '</footnotes></page>'))
        paragraphs, citations = _resolve_pages([page])
        analysis = analyse_pages([1], paragraphs, citations)[0]
        proposed = response(analysis)
        proposed['citations'], proposed['references'] = [], []
        for layout in proposed['layouts']:
            layout.update(ownership='paragraph', citation_id=None, citation_index=None,
                          continues_from_previous=False, continues_to_next=False)
        repaired = _compile_repaired_page(analysis, RepairedPage.model_validate(proposed))
        texts = [child.content for item in restore_streams([repaired]).paragraphs
                 if isinstance(item, TextFlowItem) for child in item.children]
        self.assertEqual(texts, [['Left above.'], ['Right above.'], ['① Spanning.'],
                                ['Left below.'], ['Right below.']])

    def test_paragraph_to_definition_projects_prefix_without_losing_raw_source(self):
        page = source_page()
        page.body_layouts.extend(page.footnotes_layouts)
        page.body_layouts[-1].order = 1
        page.footnotes_layouts = []
        paragraphs, citations = _resolve_pages([page])
        analysis = analyse_pages([1], paragraphs, citations)[0]
        proposed = response(analysis)
        proposed['citations'] = [dict(citation_id='new-1-1', index=1, mark='①', stream_order=0)]
        proposed['layouts'][1].update(ownership='citation', citation_id='new-1-1', citation_index=1)
        proposed['references'] = [dict(layout_id=proposed['layouts'][0]['layout_id'],
                                       citation_index=1, anchor=dict(before='Body', mark='①', after='.'))]
        repaired = _compile_repaired_page(analysis, RepairedPage.model_validate(proposed))
        restored = restore_streams([repaired])
        text = restored.citations[0].flow_items[0]
        assert isinstance(text, TextFlowItem)
        self.assertEqual(text.children[0].content, ['Footnote.'])
        self.assertEqual(_page_packet(repaired)['layouts'][1]['text'], '① Footnote.')
        paragraph = restored.paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertIs(paragraph.children[0].content[1], restored.citations[0])

    async def test_cross_page_origin_deletion_retries_and_preserves_tail(self):
        note = Reference(1, 1, '①', [TextFlowItem('body', -1, [
            fragment(1, 2, 'START'), fragment(2, 2, 'TAIL')])])
        pages = analyse_pages([1, 2], [], [note])
        attempts = []

        async def request(messages, index, _maximum):
            packet = json.loads(messages[1].message)['target_page']
            proposed = response(pages[packet['page_index'] - 1])
            attempts.append(index)
            if index == 0:
                proposed['citations'] = []
                for layout in proposed['layouts']:
                    layout.update(ownership='paragraph', citation_id=None, citation_index=None,
                                  continues_from_previous=False, continues_to_next=False)
            return json.dumps(proposed)

        result = await AllPageLlmRepairProcessor(request, page_indexes=[1], retry_policy=RetryPolicy(analysis=1))([], pages, {})
        self.assertEqual(attempts, [0, 1])
        note_text = restore_streams(result).citations[0].flow_items[0]
        assert isinstance(note_text, TextFlowItem)
        self.assertEqual([child.content for child in note_text.children], [['START'], ['TAIL']])
        # Removing an origin in local deterministic code is an algorithm error.
        with self.assertRaisesRegex(ValueError, 'no origin'):
            restore_streams([pages[1]])

    async def test_ignored_predecessor_requires_correcting_successor_connection(self):
        pages = analyse_pages([1, 2], [TextFlowItem('body', -1, [
            fragment(1, 0, '207'), fragment(2, 0, 'Body.')])], [])

        async def request(_messages, _index, _maximum):
            proposed = response(pages[0])
            layout = proposed['layouts'][0]
            layout.pop('continues_to_next')
            layout.update(ownership='ignored', continues_from_previous=False,
                          paragraph_role=None, paragraph_level=None)
            return json.dumps(proposed)

        # The successor's incoming edge must be explicitly corrected too; an
        # ignored predecessor is not a valid continuation. Candidate is rejected.
        from pdf_craft.llm.guaranteed import GuaranteedBusinessError
        with self.assertRaises(GuaranteedBusinessError):
            await AllPageLlmRepairProcessor(request, page_indexes=[1], retry_policy=RetryPolicy(analysis=0))([], pages, {})

    async def test_ignored_layout_does_not_inherit_a_derived_outgoing_edge(self):
        page = analyse_pages([1], [TextFlowItem('body', -1, [
            fragment(1, 0, '207'), fragment(1, 1, 'Body.')])], [])[0]

        async def request(_messages, _index, _maximum):
            proposed = response(page)
            layout = proposed['layouts'][0]
            layout.pop('continues_to_next')
            layout.update(ownership='ignored', continues_from_previous=False,
                          paragraph_role=None, paragraph_level=None)
            proposed['layouts'][1]['continues_from_previous'] = False
            return json.dumps(proposed)

        repaired = await AllPageLlmRepairProcessor(request, retry_policy=RetryPolicy(analysis=0))([], [page], {})
        self.assertFalse(repaired[0].layouts[0].continues_to_next)
        paragraphs = restore_streams(repaired).paragraphs
        self.assertEqual(len(paragraphs), 1)
        paragraph = paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(paragraph.children[0].content, ['Body.'])

    def test_word_joining_is_a_projection_and_keeps_complete_source_fragments(self):
        pages = [decode(fromstring(
            f'<page index="{index}"><body><layout ref="text" det="0,0,100,20">'
            f'{text}</layout></body></page>'))
                 for index, text in ((1, 'A trans‐'), (2, 'lation'))]
        paragraphs, citations = _resolve_pages(pages)
        restored = restore_streams(analyse_pages([1, 2], paragraphs, citations))
        paragraph = restored.paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual([child.content for child in paragraph.children], [['A translation'], []])
        self.assertEqual([child.source.text for child in paragraph.children
                          if isinstance(child, SourceTextFragment) and child.source is not None],
                         ['A trans‐', 'lation'])
        self.assertEqual([(child.page_index, child.source_order) for child in paragraph.children
                          if isinstance(child, SourceTextFragment)], [(1, 0), (2, 0)])

    def test_local_definition_merge_and_split_preserve_internal_printed_marks(self):
        first = Reference(1, 1, '①', [TextFlowItem('body', -1, [
            SourceTextFragment(1, 1, (0, 50, 100, 65), ['① First.'])])])
        second = Reference(1, 2, '②', [TextFlowItem('body', -1, [
            SourceTextFragment(1, 2, (0, 70, 100, 85), ['② Second.'])])])
        page = analyse_pages([1], [], [first, second])[0]
        proposed = response(page)
        proposed['citations'] = proposed['citations'][:1]
        proposed['layouts'][1].update(citation_id=proposed['citations'][0]['citation_id'],
                                      citation_index=1)
        merged = _compile_repaired_page(page, RepairedPage.model_validate(proposed))
        texts = [child.content for item in restore_streams([merged]).citations[0].flow_items
                 if isinstance(item, TextFlowItem) for child in item.children]
        self.assertEqual(texts, [['First.'], ['② Second.']])
        split = response(merged)
        split['citations'].append(dict(citation_id='new-1-2', index=2, mark='②', stream_order=1))
        split['layouts'][1].update(citation_id='new-1-2', citation_index=2)
        restored = restore_streams([_compile_repaired_page(merged, RepairedPage.model_validate(split))])
        self.assertEqual([child.content for note in restored.citations for item in note.flow_items
                          if isinstance(item, TextFlowItem) for child in item.children],
                         [['First.'], ['Second.']])
        self.assertEqual(_page_packet(merged)['layouts'][1]['text'], '② Second.')

    async def test_unresolved_tail_can_link_to_existing_previous_page_definition(self):
        from pdf_craft.extractor.chapter.page_analysis import UnindexedCitation
        note = Reference(1, 1, '①', [TextFlowItem('body', -1, [fragment(1, 1, 'START')])])
        pages = analyse_pages([1, 2], [], [note, UnindexedCitation(2, (
            TextFlowItem('body', -1, [fragment(2, 1, 'TAIL')]),))])
        previous_id = pages[0].citations[0].citation_id

        async def request(_messages, _index, _maximum):
            proposed = response(pages[1])
            proposed['citations'][0].update(citation_id=previous_id)
            proposed['layouts'][0].update(citation_id=previous_id, continues_from_previous=True)
            proposed['layouts'][0].pop('continues_to_next')
            return json.dumps(proposed)

        repaired = await AllPageLlmRepairProcessor(request, page_indexes=[2], retry_policy=RetryPolicy(analysis=0))([], pages, {})
        restored = restore_streams(repaired)
        self.assertEqual(restored.paragraphs, [])
        self.assertEqual(len(restored.citations), 1)
        text = restored.citations[0].flow_items[0]
        assert isinstance(text, TextFlowItem)
        self.assertEqual([child.content for child in text.children], [['START'], ['TAIL']])

    async def test_translate_archive_reopen_and_real_pdf_body_and_note_fill(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extraction = make_extraction(root / 'source', page_pixel_sizes={1: (300, 300)}, render_dpi=72)
            note = Reference(1, 1, '*', [TextFlowItem('body', -1, [
                SourceTextFragment(1, 1, (20, 220, 280, 250), ['Source note'])])])
            chapter = Chapter(None, -1, [TextFlowItem('body', -1, [
                SourceTextFragment(1, 0, (20, 20, 280, 50), ['Source body', note, note])])])
            save_xml(encode(chapter), root / 'source/chapters/chapter_head.xml')
            translated = await ChapterExtractionTransformer(_TextTranslator()).transform(
                extraction, root / 'translated.pcex')
            reopened = PDFCraftExtraction._open(root / 'translated.pcex')
            with reopened._materialize() as workspace:
                coverage = read_coverage(workspace.translation)
                self.assertEqual(coverage.narrative,
                                 {('head', 1, 0): 'translated', ('head', 1, 1): 'translated'})
                xml = parse(workspace.chapters / 'chapter_head.xml')
                self.assertEqual(len(xml.findall('.//fragment/ref')), 2)
            canvas = Canvas(str(root / 'source.pdf'), pagesize=(300, 300))
            canvas.drawString(20, 260, 'Source body**')
            canvas.drawString(20, 60, '* Source note')
            canvas.save()
            await PDFTranslationPipeline().patch(root / 'source.pdf', root / 'result.pdf', translated)
            page = PdfReader(root / 'result.pdf').pages[0]
            assert isinstance(page, PageObject)
            text = ' '.join(PageObject.extract_text(page).split())
            self.assertIn('Translated body', text)
            self.assertEqual(text.count('Translated note'), 1)
            self.assertNotIn('Source note', text)
            self.assertNotIn('Source body', text)
