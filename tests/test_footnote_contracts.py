"""Business regressions recorded before the footnote contract migration."""
# pylint: disable=protected-access
import json
import unittest
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import fromstring

from pdf_craft.pdf import decode, Page
from pdf_craft.extractor.chapter.chapter import (
    Chapter, Reference, SourceTextFragment, TextFlowItem, encode,
)
from pdf_craft.extractor.chapter.generation import _resolve_pages, _assemble_flow_items
from pdf_craft.extractor.chapter.mark import transform2mark
from pdf_craft.extractor.chapter.page_analysis import analyse_pages, restore_streams
from pdf_craft.extractor.chapter.page_repair import (
    RepairedPage, _compile_repaired_page, _page_packet,
    _compile_page_connections, repair_page_with_llm,
)
from pdf_craft.pipeline.pdf.pipeline import PDFTranslationPipeline
from pdf_craft.transformer.translation_coverage import TranslationCoverage, paragraph_identity


def fragment(page, order, text):
    return SourceTextFragment(page, order, (0, 0, 100, 20), [text])


def response(page) -> dict[str, Any]:
    packet = _page_packet(page)
    fields = {'layout_id', 'ownership', 'continues_from_previous',
              'continues_to_next', 'paragraph_role', 'paragraph_level',
              'citation_id', 'citation_index'}
    return dict(page_index=page.page_index,
                layouts=[{k: v for k, v in item.items() if k in fields}
                         for item in packet['layouts']],
                citations=packet['citations'], references=packet['references'])


def source_page(mark='①') -> Page:
    return decode(fromstring(
        '<page index="1"><body><layout ref="text" det="0,0,100,20">'
        f'Body{mark}.</layout></body><footnotes>'
        f'<layout ref="text" det="0,90,100,110">{mark} Footnote.</layout>'
        '</footnotes></page>'
    ))


class FootnoteContractTests(unittest.IsolatedAsyncioTestCase):
    def test_f1_ocr_streams_have_distinct_persisted_fragment_identities(self):
        paragraphs, citations = _resolve_pages([source_page()])
        root = encode(Chapter(None, -1, list(_assemble_flow_items(paragraphs))))
        identities = [(f.get('page_index'), f.get('source_order'))
                      for f in root.iter('fragment')]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len(citations), 1)

    async def test_f2_cross_page_identity_change_is_rejected_before_acceptance(self):
        note = Reference(1, 1, '①', [TextFlowItem('body', -1, [
            fragment(1, 2, 'START'), fragment(2, 2, 'TAIL')])])
        pages = analyse_pages([1, 2], [TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), ['Body', note])])], [note])
        attempts = []

        async def request(_messages, index, _maximum):
            attempts.append(index)
            proposed = response(pages[0])
            if index == 0:
                proposed['citations'][0]['citation_id'] = 'arbitrary-rename'
                for layout in proposed['layouts']:
                    if layout['ownership'] == 'citation':
                        layout['citation_id'] = 'arbitrary-rename'
            return json.dumps(proposed)

        pages[0] = await repair_page_with_llm(
            previous_page=None, target_page=pages[0], next_page=pages[1],
            previous_pass_probability=None, next_pass_probability=None,
            request=request, max_retries=1,
        )
        self.assertEqual(attempts, [0, 1])
        _compile_page_connections(pages)
        restored = restore_streams(pages)
        self.assertEqual([f.content for t in restored.citations[0].flow_items
                          if isinstance(t, TextFlowItem) for f in t.children], [['START'], ['TAIL']])

    def test_f3_star_definition_and_occurrence_survive_chapter_encoding(self):
        paragraphs, _ = _resolve_pages([source_page('*')])
        root = encode(Chapter(None, -1, list(_assemble_flow_items(paragraphs))))
        self.assertEqual(len(root.findall('./references/ref')), 1)
        self.assertEqual(len(root.findall('.//fragment/ref')), 1)
        self.assertEqual(root.findtext('./references/ref/flow/text/fragment'), 'Footnote.')

    def test_f4_reclassification_restores_detached_printed_mark(self):
        paragraphs, citations = _resolve_pages([source_page()])
        page = analyse_pages([1], paragraphs, citations)[0]
        proposed = response(page)
        proposed['citations'] = []
        proposed['references'] = []
        for layout in proposed['layouts']:
            if layout['ownership'] == 'citation':
                layout.update(ownership='paragraph', citation_id=None, citation_index=None)
        repaired = _compile_repaired_page(page, RepairedPage.model_validate(proposed))
        texts = [f.content for t in restore_streams([repaired]).paragraphs if isinstance(t, TextFlowItem) for f in t.children]
        self.assertEqual(''.join(str(part) for part in texts[-1]), '① Footnote.')

    def test_f5_reclassification_preserves_source_reading_position(self):
        page = decode(fromstring(
            '<page index="1"><body><layout ref="text" det="0,0,100,20">A</layout>'
            '<layout ref="text" det="0,70,100,90">C</layout></body><footnotes>'
            '<layout ref="text" det="0,35,100,55">① B</layout></footnotes></page>'
        ))
        paragraphs, citations = _resolve_pages([page])
        analysis = analyse_pages([1], paragraphs, citations)[0]
        proposed = response(analysis)
        proposed['citations'], proposed['references'] = [], []
        for layout in proposed['layouts']:
            if layout['ownership'] == 'citation':
                layout.update(ownership='paragraph', citation_id=None, citation_index=None)
        repaired = _compile_repaired_page(analysis, RepairedPage.model_validate(proposed))
        self.assertEqual([f.bbox[1] for t in restore_streams([repaired]).paragraphs
                          if isinstance(t, TextFlowItem) if isinstance(t, TextFlowItem) for f in t.children], [0, 35, 70])

    def test_f6_repeated_occurrences_can_round_trip_without_changing_definition(self):
        mark = transform2mark('①')
        assert mark is not None
        note = Reference(1, 1, mark, [TextFlowItem('body', -1, [fragment(1, 1, 'Note')])])
        paragraph = TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), ['A', note, 'B', note])])
        page = analyse_pages([1], [paragraph], [note])[0]
        repaired = _compile_repaired_page(page, RepairedPage.model_validate(response(page)))
        restored = restore_streams([repaired])
        paragraph = restored.paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        occurrences = [part for part in paragraph.children[0].content
                       if isinstance(part, Reference)]
        self.assertEqual(len(occurrences), 2)
        self.assertIs(occurrences[0], occurrences[1])

    async def test_f7_missing_metadata_is_repaired_inside_guaranteed_json(self):
        for field, value in (('paragraph_role', None), ('paragraph_level', None),
                             ('paragraph_role', ''), ('paragraph_role', 'unknown'),
                             ('paragraph_level', -2), ('paragraph_level', 'bad')):
            with self.subTest(field=field, value=value):
                await self._check_missing_metadata(field, value)

    async def _check_missing_metadata(self, field, value):
        page = analyse_pages([1], [TextFlowItem('body', -1, [fragment(1, 0, 'Text')])], [])[0]
        attempts = []

        async def request(_messages, index, _maximum):
            attempts.append(index)
            proposed = response(page)
            if index == 0:
                proposed['layouts'][0][field] = value
            return json.dumps(proposed)

        repaired = await repair_page_with_llm(
            previous_page=None, target_page=page, next_page=None,
            previous_pass_probability=None, next_pass_probability=None,
            request=request, max_retries=1,
        )
        self.assertEqual(attempts, [0, 1])
        paragraph = restore_streams([repaired]).paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(paragraph.children[0].content, ['Text'])

    def test_f8_pdf_replacements_include_translated_footnote_once(self):
        note_text = TextFlowItem('body', -1, [fragment(1, 1, 'Translated note')])
        note = Reference(1, 1, '①', [note_text])
        body = TextFlowItem('body', -1, [
            SourceTextFragment(1, 0, (0, 0, 100, 20), ['Translated body', note, note])])
        chapter = Chapter(None, -1, [body])
        coverage = TranslationCoverage({identity: 'translated'
                                        for item in (body, note_text)
                                        if (identity := paragraph_identity(chapter, item)) is not None}, {}, {}, {})
        pipeline = object.__new__(PDFTranslationPipeline)
        replacements = list(pipeline._iter_covered_replacements(
            (chapter,), Path('/nonexistent/furnitures.xml'), coverage, {1: (100, 120)}, 300))
        self.assertEqual([r.text for r in replacements],
                         ['Translated body①①', '① Translated note'])
