"""Small source-local regressions for service #314's three expression barriers."""
# pylint: disable=protected-access
import json
from typing import Any, cast
from copy import deepcopy
from unittest import IsolatedAsyncioTestCase, TestCase
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import RetryPolicy
from pdf_craft.pdf import decode as decode_page
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.extractor.chapter.chapter import (
    Chapter, SourceTextFragment, TextFlowItem, InlineExpression, encode, decode,
)
from pdf_craft.extractor.chapter.page_analysis import restore_streams
from pdf_craft.extractor.chapter.page_repair import (
    RepairedLayout, RepairedPage, _compile_repaired_page,
    build_page_repair_messages, repair_page_with_llm,
)
from pdf_craft.extractor.chapter.source_ranges import slice_content
from pdf_craft.extractor.chapter.analysis_types import _AnalysedHTMLTag, _AnalysedInlineExpression
from pdf_craft.expression import ExpressionKind
from pdf_craft.transformer.translation_coverage import paragraph_identity
from pdf_craft.transformer.narrative_xml import _source_unit_identity


def page(body, notes=''):
    from pdf_craft.extractor.chapter.analysis_types import PageAnalysis
    raw = decode_page(fromstring(f'<page index="1"><body>{body}</body><footnotes>{notes}</footnotes></page>'))
    result = _project_window((None, raw, None))[1]
    assert isinstance(result, PageAnalysis)
    return result


def box(text):
    return f'<layout ref="text" det="10,10,190,150">{text}</layout>'


def proposal(source) -> dict[str, Any]:
    messages = build_page_repair_messages(previous_page=None, target_page=source, next_page=None,
                                         previous_pass_probability=None, next_pass_probability=None)
    packet = json.loads(messages[1].message)['target_page']
    fields = set(RepairedLayout.model_fields)
    return dict(page_index=1, layouts=[{k: v for k, v in item.items() if k in fields}
                                     for item in packet['layouts']], citations=packet['citations'], references=[])


def split_notes(source):
    data = proposal(source)
    note = data['layouts'].pop()
    ident = note['layout_id']
    cut = dict(left='First note.\n', right='7 Second note.')
    for index, start, end in ((1, 'source_start', cut), (2, cut, 'source_end')):
        data['layouts'].append(dict(note, source_range=dict(start=start, end=end),
                                    ownership='citation', citation_id=f'new-1-{index}', citation_index=index,
                                    continues_from_previous=False, continues_to_next=False,
                                    paragraph_role='body', paragraph_level=-1))
    data['citations'] = [dict(citation_id=f'new-1-{i}', index=i, mark=str(i+5), stream_order=i-1) for i in (1, 2)]
    return data, ident


def compiled(source, data):
    return _compile_repaired_page(source, RepairedPage.model_validate(data))


class SourceRangeRepairTests(IsolatedAsyncioTestCase):
    async def test_final_order_is_used_by_validation_and_assembly(self):
        source = page(box('Stromversor-') + box('Ankündigung. Independent.') + box('gung berücksichtigt.'))
        data = proposal(source)
        a, b, c = data['layouts']
        for item in data['layouts']:
            item['continues_from_previous'] = False
            item.pop('continues_to_next')
        c['continues_from_previous'] = True
        data['layouts'] = [a, c, b]
        async def request(*_args):
            return json.dumps(data)
        result = await repair_page_with_llm(previous_page=None, target_page=source, next_page=None,
                                          previous_pass_probability=None, next_pass_probability=None,
                                          request=request, retry_policy=RetryPolicy(analysis=0))
        restored = restore_streams([result]).paragraphs
        self.assertEqual(len(restored), 2)
        self.assertEqual([cast(SourceTextFragment, f).source_order for f in cast(TextFlowItem, restored[0]).children], [0, 2])
        self.assertEqual(cast(SourceTextFragment, cast(TextFlowItem, restored[1]).children[0]).content, ['Ankündigung. Independent.'])
        self.assertEqual([x.source_order for x in source.layouts], [0, 1, 2])

    async def test_one_box_two_definitions_survive_codec_and_translation_identity(self):
        source = page(box('Body.'), box('6 First note.\n7 Second note.'))
        data, _ = split_notes(source)
        result = compiled(source, data)
        streams = restore_streams([result])
        self.assertEqual([note.mark for note in streams.citations], ['6', '7'])
        first, second = [cast(SourceTextFragment, cast(TextFlowItem, note.flow_items[0]).children[0]) for note in streams.citations]
        self.assertEqual(first.content, ['First note.\n'])
        self.assertEqual(second.content, ['Second note.'])
        self.assertEqual(first.source_order, second.source_order)
        self.assertEqual(first.bbox, second.bbox)
        self.assertNotEqual(first.identity_order, second.identity_order)
        chapter = Chapter(None, -1, [cast(TextFlowItem, item) for item in streams.paragraphs], streams.citations)
        encoded = encode(chapter)
        reopened = decode(fromstring(tostring(encoded)))
        self.assertEqual(reopened, chapter)
        flows = [cast(TextFlowItem, note.flow_items[0]) for note in reopened.definitions]
        self.assertNotEqual(paragraph_identity(reopened, flows[0]), paragraph_identity(reopened, flows[1]))
        nodes = encoded.findall('./references/ref/flow/text')
        self.assertNotEqual(_source_unit_identity(nodes[0]), _source_unit_identity(nodes[1]))
        self.assertEqual(first.source_text, '6 First note.\n7 Second note.')

    async def test_split_note_translations_and_coverage_survive_export_and_reopen(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from pdf_craft import AsyncPDFCraft, RenderMode
        from pdf_craft.common import save_xml, read_xml
        from pdf_craft.transformer.translation_coverage import read_coverage
        from tests.extraction_helpers import make_extraction
        from tests.test_structured_translation import offline_translator
        source = page(box('Body 6 and 7.'), box('6 First note.\n7 Second note.'))
        data, _ = split_notes(source)
        body_id = data['layouts'][0]['layout_id']
        data['references'] = [dict(layout_id=body_id, citation_index=i,
                                   anchor=dict(before=before, mark=mark, after=after))
                              for i, before, mark, after in ((1, 'Body ', '6', ' and '),
                                                            (2, ' and ', '7', '.'))]
        streams = restore_streams([compiled(source, data)])
        chapter = Chapter(None, -1, [cast(TextFlowItem, x) for x in streams.paragraphs], streams.citations)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root/'source', page_pixel_sizes={1:(200,200)})
            save_xml(encode(chapter), root/'source/chapters/chapter_head.xml')
            craft = AsyncPDFCraft()
            await craft.export_extraction(extraction, root/'source.pcex')
            opened = await craft.open_extraction(root/'source.pcex')
            with patch('tests.test_structured_translation.neutral_translation',
                       side_effect=lambda value: value.replace('First', '第一').replace('Second', '第二')):
                translated = await craft.translate_extraction(opened, root/'translated.pcex', offline_translator(),
                                                              translation_id='notes', target_language='zh-CN', error_policy=False)
            reopened = await craft.open_extraction(root/'translated.pcex')
            with reopened._materialize() as paths:
                source_chapter = decode(read_xml(paths.chapters/'chapter_head.xml'))
                target_chapter = decode(read_xml(paths.translations/'notes/chapters/chapter_head.xml'))
                self.assertEqual([cast(SourceTextFragment, cast(TextFlowItem, note.flow_items[0]).children[0]).content for note in target_chapter.definitions],
                                 [['第一 note.'], ['第二 note.']])
                states = read_coverage(paths.translations/'notes/coverage.xml').narrative
                for note in source_chapter.definitions:
                    identity = paragraph_identity(source_chapter, cast(TextFlowItem, note.flow_items[0]))
                    assert identity is not None
                    self.assertEqual(states[identity], 'translated')
            await craft.render_markdown(translated, root/'bilingual.md', mode=RenderMode.BILINGUAL, translation_id='notes')
            rendered = (root/'bilingual.md').read_text()
            self.assertIn('第一', rendered)
            self.assertIn('第二', rendered)

    async def test_shared_source_titles_keep_semantic_order_and_distinct_toc_anchors(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from pdf_craft.common import save_xml
        from pdf_craft.extractor.commit import project_adopted
        from pdf_craft.extractor.toc.analysing import analyse_toc
        from pdf_craft.extractor.chapter.generation import _partition_chapters
        evidence = 'Chapter OneChapter Two'
        first = SourceTextFragment(1, 0, (10,10,190,150), ['Chapter One'], source_end=11, source_text=evidence)
        second = SourceTextFragment(1, 0, first.bbox, ['Chapter Two'], source_start=11, source_end=22, source_text=evidence)
        items = [TextFlowItem('heading', 0, [part]) for part in (first, second)]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'chapters').mkdir()
            (root/'ocr').mkdir()
            save_xml(encode(Chapter(None, -1, [*items])), root/'chapters/chapter_head.xml')
            save_xml(fromstring('<page index="1"><body>' + box(evidence) + '</body></page>'), root/'ocr/page_1.xml')
            project_adopted(root/'chapters', root/'ocr', root/'projection', [])
            toc = await analyse_toc(root/'projection', root/'toc.xml', False)
            self.assertEqual([item.order for item in toc.content], [first.identity_order, second.identity_order])
            chapters = list(_partition_chapters(items, toc, assembled=True))
            self.assertEqual([chapter.id for chapter in chapters], [1, 2])

    async def test_cross_page_note_binds_to_adopted_range_in_a_shared_source(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from pdf_craft.extractor.chapter.assembly import PageAssembly
        source = page(box('Body.'), box('6 First note.\n7 Second note.'))
        data, _ = split_notes(source)
        first = compiled(source, data)
        raw = decode_page(fromstring('<page index="2"><body>' + box('Next body.') +
                                    '</body><footnotes>' + box('Continued explanation.') + '</footnotes></page>'))
        second = _project_window((None, raw, None))[1]
        assert second is not None
        follow = proposal(second)
        follow['page_index'] = 2
        follow['citations'] = [dict(citation_id='tail', index=None, mark=None, stream_order=0,
                                    continues_previous=True,
                                    predecessor=dict(page_index=1, source_order=1, start=14, end=26))]
        follow['layouts'][-1].update(ownership='citation', citation_id='tail', citation_index=None,
                                    paragraph_role='body', paragraph_level=-1,
                                    continues_from_previous=False, continues_to_next=False)
        candidate = compiled(second, follow)
        with TemporaryDirectory() as directory:
            notes = []
            assembly = PageAssembly(Path(directory)/'assembly.sqlite', on_footnote=notes.append)
            assembly.append(first, refined=True)
            assembly.append(candidate, refined=True)
            assembly.finish()
            definitions = {node.attrib['id']: node for node in notes}
            self.assertEqual(len(definitions['1-2'].findall('./flow/text')), 2)
            self.assertIn('Continued explanation.', ''.join(definitions['1-2'].itertext()))
            self.assertNotIn('Continued explanation.', ''.join(definitions['1-1'].itertext()))

    async def test_explicit_formula_annotation_preserves_year_and_binds_note(self):
        source = page(box('Year \\(2005^{9}\\).'), box('9 A historical note.'))
        data = proposal(source)
        data['citations'] = [dict(citation_id='new-1-1', index=1, mark='9', stream_order=0)]
        note = data['layouts'][-1]
        note.update(citation_id='new-1-1', citation_index=1, ownership='citation',
                    paragraph_role='body', paragraph_level=-1)
        data['references'] = [dict(layout_id=data['layouts'][0]['layout_id'], citation_index=1,
                                   anchor=dict(before='2005', mark='^{9}', after='.'),
                                   extract_from_expression=True)]
        result = restore_streams([compiled(source, data)])
        content = cast(SourceTextFragment, cast(TextFlowItem, result.paragraphs[0]).children[0]).content
        assert isinstance(content[1], InlineExpression)
        self.assertEqual(content[1].content, '2005')
        from pdf_craft.extractor.chapter.chapter import Reference
        assert isinstance(content[2], Reference)
        self.assertEqual(content[2].id, result.citations[0].id)
        data['references'][0]['extract_from_expression'] = False
        with self.assertRaisesRegex(ValueError, 'complete atomic'):
            compiled(source, data)
        data['references'][0]['extract_from_expression'] = True
        data['citations'][0]['mark'] = '8'
        with self.assertRaisesRegex(ValueError, 'printed note marker'):
            compiled(source, data)

    async def test_terminal_annotation_supports_printed_unicode_note_marks(self):
        source = page(box(r'Year \(2005^{①}\).'), box('① A note.'))
        data = proposal(source)
        data['citations'] = [dict(citation_id='new-1-1', index=1, mark='①', stream_order=0)]
        data['layouts'][-1].update(ownership='citation', citation_id='new-1-1', citation_index=1,
                                   paragraph_role='body', paragraph_level=-1)
        data['references'] = [dict(layout_id=data['layouts'][0]['layout_id'], citation_index=1,
                                   anchor=dict(before='2005', mark='^{①}', after='.'), extract_from_expression=True)]
        restored = restore_streams([compiled(source, data)])
        content = cast(SourceTextFragment, cast(TextFlowItem, restored.paragraphs[0]).children[0]).content
        self.assertEqual(cast(InlineExpression, content[1]).content, '2005')
        self.assertEqual(restored.citations[0].mark, '①')

    async def test_shared_note_identity_does_not_freeze_exclusive_box_ownership(self):
        from pdf_craft.extractor.chapter.page_repair import _validate_shared_citations
        source = page(box('Body.'), box('6 First note.\n7 Second note.'))
        data, _ = split_notes(source)
        source.citations[0].index = 1
        source.citations[0].mark = '6'
        source.citations[0].origin_page_index = 1
        original = source.citations[0].citation_id
        for layout in source.layouts:
            if layout.citation_id == original:
                layout.annotations.citation_index = 1
        data['citations'][0]['citation_id'] = original
        data['layouts'][-2]['citation_id'] = original
        candidate = compiled(source, data)
        _validate_shared_citations(source, candidate, {original})
        self.assertEqual([item.citation_id for item in candidate.layouts if item.ownership == 'citation'],
                         [original, 'new-1-2'])

    async def test_missing_duplicate_overlap_and_ambiguous_ranges_reject_atomically(self):
        source = page(box('Body.'), box('6 First note.\n7 Second note.'))
        data, _ = split_notes(source)
        before = deepcopy(source)
        for mode in ('missing', 'duplicate', 'overlap', 'ambiguous'):
            bad = deepcopy(data)
            if mode == 'missing':
                bad['layouts'].pop()
            elif mode == 'duplicate':
                bad['layouts'].append(deepcopy(bad['layouts'][-1]))
            elif mode == 'overlap':
                bad['layouts'][-1]['source_range'] = None
            else:
                bad['layouts'][-1]['source_range']['start'] = dict(left='', right='')
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'SOURCE_RANGE'):
                compiled(source, bad)
            self.assertEqual(source, before)

    async def test_body_only_mixed_source_retains_body_but_not_note(self):
        source = page(box('Body.6 First note.\n7 Second note.'))
        data = proposal(source)
        original = data['layouts'][0]
        cut = dict(left='Body.', right='6 First note.')
        body = dict(original, source_range=dict(start='source_start', end=cut),
                    ownership='paragraph', citation_id=None, citation_index=None,
                    paragraph_role='body', paragraph_level=-1,
                    continues_from_previous=False, continues_to_next=False)
        ignored = dict(original, source_range=dict(start=cut, end='source_end'),
                       ownership='ignored', citation_id=None, citation_index=None,
                       paragraph_role=None, paragraph_level=None,
                       continues_from_previous=False, continues_to_next=False)
        data.update(layouts=[body, ignored], citations=[])
        async def request(*_args):
            return json.dumps(data)
        repaired = await repair_page_with_llm(previous_page=None, target_page=source, next_page=None,
                                            previous_pass_probability=None, next_pass_probability=None,
                                            request=request, includes_footnotes=False, retry_policy=RetryPolicy(analysis=0))
        self.assertEqual(cast(SourceTextFragment, cast(TextFlowItem, restore_streams([repaired]).paragraphs[0]).children[0]).content, ['Body.'])
        self.assertEqual(repaired.layouts[1].ownership, 'ignored')


class StructuredRangeTests(TestCase):
    def test_zero_width_rich_nodes_belong_to_one_slice_even_at_the_end(self):
        br = _AnalysedHTMLTag('br', (), ())
        empty = _AnalysedInlineExpression(ExpressionKind.INLINE_PAREN, '')
        original = ('A', br, 'B', empty)
        self.assertEqual(slice_content(original, 0, 1), ('A',))
        self.assertEqual(slice_content(original, 1, 2), (br, 'B', empty))

    def test_html_cuts_keep_wrappers_but_do_not_split_formulas(self):
        expression = _AnalysedInlineExpression(ExpressionKind.INLINE_PAREN, 'x^{2}')
        content = (_AnalysedHTMLTag('b', (), ('One. Two.',)), expression)
        sliced = slice_content(content, 5, 9)
        assert isinstance(sliced[0], _AnalysedHTMLTag)
        self.assertEqual(sliced[0].children, ('Two.',))
        with self.assertRaisesRegex(ValueError, 'atomic expression'):
            slice_content(content, 9, 11)


class SharedSourceSafetyTests(TestCase):
    def test_missing_or_excluded_ranges_never_erase_another_owner(self):
        from pdf_craft.pipeline.pdf.shared_sources import SharedSources
        from pdf_craft.pipeline.pdf.models import PDFReplacement
        from pdf_craft.pipeline.pdf.content import source_key
        evidence = 'Body.Note.'
        source_box = (0, 0, 100, 50)
        first = SourceTextFragment(1, 4, source_box, ['Body.'], source_end=5, source_text=evidence)
        second = SourceTextFragment(1, 4, source_box, ['Note.'], source_start=5, source_end=10, source_text=evidence)
        values = [PDFReplacement(1, source_box, text, (100, 100)) for text in ('Translated body.', 'Translated note.')]
        keys = [source_key(TextFlowItem('body', -1, [f])) for f in (first, second)]
        for omitted in (True, False):
            plan = SharedSources()
            plan.observe(keys[0], [first], values[0])
            if not omitted:
                plan.observe(keys[1], [second], values[1])
            plan.finish()
            result = plan.accept(keys[0], values[0], 0, None, False)
            if not omitted:
                self.assertIsNone(result)
                result = plan.accept(keys[1], None, 1, None, True)
            assert result is not None
            self.assertTrue(all(value is None for _, value, _, _ in result))
            from pdf_craft.error import NoUsableFillPagesError
            with self.assertRaises(NoUsableFillPagesError):
                plan.ensure_usable()

    def test_equal_boxes_are_not_source_identity(self):
        from pdf_craft.pipeline.pdf.shared_sources import SharedSources
        from pdf_craft.pipeline.pdf.models import PDFReplacement
        from pdf_craft.pipeline.pdf.content import source_key
        plan = SharedSources()
        for order in (0, 1):
            part = SourceTextFragment(1, order, (0, 0, 100, 50), ['Text.'])
            key = source_key(TextFlowItem('body', -1, [part]))
            plan.observe(key, [part], PDFReplacement(1, part.bbox, 'Translation.', (100, 100)))
        plan.finish()
        self.assertEqual(plan.groups, {})
