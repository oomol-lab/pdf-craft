"""Heuristics propose before review; adoption only materializes declarations."""
from tests.repair_plan_fixtures import expanded_packet
# pylint: disable=protected-access
from tests.repair_plan_fixtures import serialize_plan
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft.common import read_xml
from pdf_craft.document.content import chapter_paths
from pdf_craft.extractor.chapter.analysis_content import layout_reference_text
from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.chapter import decode, SourceTextFragment, SourceAsset, TextFlowItem
from pdf_craft.extractor.chapter.analysis_types import _AnalysedTextSource
from pdf_craft.extractor.chapter.generation import generate_chapter_files
from pdf_craft.extractor.chapter.page_analysis import restore_streams, analyse_pages
from pdf_craft.extractor.chapter.page_repair import repair_page_with_llm
from pdf_craft.extractor.chapter.primary import project_window
from pdf_craft.extractor.commit import partition_adopted
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.pdf import decode as decode_page
from tests.test_asset_editing import scene, candidate, compile_page, roundtrip


def initial_join(*items):
    raw = scene(*items)
    fragments: list[SourceTextFragment | SourceAsset] = []
    for item in raw.layouts:
        assert isinstance(item.source, _AnalysedTextSource)
        assert item.source.origin is not None
        fragments.append(SourceTextFragment(item.page_index, item.source.source_order, item.bbox,
                                            list(item.source.origin.content), source=item.source.origin))
    return analyse_pages([1], [TextFlowItem('body', -1, fragments)], [])[0]


def text_of(chapter):
    return ''.join(part for flow in chapter.flow_items if isinstance(flow, TextFlowItem)
                   for fragment in flow.children if isinstance(fragment, SourceTextFragment)
                   for part in fragment.content if isinstance(part, str))


class InitialTextOperationsTests(TestCase):
    def test_suggestion_can_be_accepted_or_rejected_without_moving_source_offsets(self):
        source = initial_join(('text', (0, 0, 100, 20), 'An inter‐ '),
                       ('text', (0, 20, 100, 40), ' national word.'))
        before = deepcopy(source)
        data = candidate(source)
        self.assertTrue(data['layouts'][0]['text_removals'])
        self.assertTrue(data['layouts'][1]['text_removals'])
        self.assertEqual(layout_reference_text(source.layouts[0]), 'An inter‐ ')
        data['layouts'][1]['continues_from_previous'] = True
        # The initial suggestion is a set of literal operations, not a new
        # source text or a new coordinate system for range/reference anchors.
        joined = roundtrip(compile_page(source, data))
        self.assertEqual(text_of(joined), 'An international word.')
        for item in data['layouts']:
            item['text_removals'] = []
        untouched = roundtrip(compile_page(source, data))
        self.assertEqual(text_of(untouched), 'An inter‐  national word.')
        self.assertEqual(source, before)
        self.assertEqual([fragment.bbox for flow in joined.flow_items if isinstance(flow, TextFlowItem) for fragment in flow.children],
                         [(0, 0, 100, 20), (0, 20, 100, 40)])
        first_flow = joined.flow_items[0]
        assert isinstance(first_flow, TextFlowItem)
        fragment = first_flow.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual(fragment.source_text, 'An inter‐ ')

    def test_literal_removal_uses_original_range_even_after_source_slicing(self):
        source = scene(('text', (0, 0, 100, 40), 'Prefix. A co‐operation.'))
        data = candidate(source)
        original = data['layouts'][0]
        cut = dict(left='Prefix. ', right='A co‐operation.')
        data['layouts'] = [dict(original, source_range=dict(start='source_start', end=cut)),
                           dict(original, source_range=dict(start=cut, end='source_end'),
                                text_removals=[dict(before='Prefix. A co', mark='‐', after='operation.')])]
        result = compile_page(source, data)
        assert isinstance(result.layouts[1].source, _AnalysedTextSource)
        self.assertEqual(result.layouts[1].source.source_start, len('Prefix. '))
        self.assertEqual(result.layouts[1].annotations.text_removals, ((4, 5),))
        self.assertEqual(text_of(roundtrip(result)), 'Prefix. A cooperation.')
        # The raw source partition and anchor positions did not shrink.
        self.assertEqual(layout_reference_text(result.layouts[1]), 'A co‐operation.')

    def test_removal_cannot_hide_words_references_or_formula_nodes(self):
        source = scene(('text', (0, 0, 100, 30), r'Keep word and $x-y$.'))
        for before, mark, after in [('Keep ', 'word', ' and'), ('Keep word and x', '-', 'y.')]:
            data = candidate(source)
            data['layouts'][0]['text_removals'] = [dict(before=before, mark=mark, after=after)]
            with self.assertRaisesRegex(ValueError, 'TEXT_REMOVAL'):
                compile_page(source, data)

    def test_prepared_heading_levels_are_visible_and_later_model_levels_survive(self):
        source = scene(('title', (0, 0, 100, 40), 'Large heading'),
                       ('text', (0, 50, 100, 70), 'Content, 中文!'),
                       ('sub_title', (0, 80, 100, 90), 'Small heading'))
        initial_level = source.layouts[-1].paragraph_level
        assert initial_level is not None
        self.assertGreaterEqual(initial_level, 0)
        data = candidate(source)
        data['layouts'][0].update(paragraph_role='heading', paragraph_level=4)
        data['layouts'][-1].update(paragraph_role='heading', paragraph_level=2)
        adopted = compile_page(source, data)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = PageAssembly(root/'adopted.sqlite')
            assembly.append(adopted)
            assembly.finish()
            # Executing a heuristic here is a regression even if it happens to
            # produce the same output for a particular fixture.
            with patch('pdf_craft.extractor.chapter.analyse_level.analyse_chapter_internal_levels',
                       side_effect=AssertionError('post-adoption heading inference')):
                generate_chapter_files(root/'unused', root/'chunks', TocInfo([], []),
                                       resolved_database=root/'adopted.sqlite', chunk_size=128)
                partition_adopted(root/'chunks', root/'final', TocInfo([], []))
            final = [decode(read_xml(p)) for p in chapter_paths(root/'final')]
            headings = [x.level for c in final for x in c.flow_items if isinstance(x, TextFlowItem) and x.role == 'heading']
            self.assertEqual(headings, [4, 2])
            # Adjacent punctuation normalization must not rewrite adopted text.
            self.assertIn('Content, 中文!', ''.join(text_of(c) for c in final))

    def test_raw_cross_page_suggestions_are_prepared_before_current_page_adoption(self):
        pages = [decode_page(fromstring(
            f'<page index="{index}"><body><layout ref="text" det="1,1,100,30">{text}'
            '</layout></body></page>')) for index, text in [(1, 'A re‐'), (2, 'entry works.')]]
        first, second = project_window(pages, False)
        assert first is not None and second is not None
        self.assertTrue(first.layouts[0].annotations.text_removals)
        one, two = candidate(first), candidate(second)
        two['layouts'][0]['continues_from_previous'] = True
        result = restore_streams([compile_page(first, one), compile_page(second, two)])
        text = ''.join(p for flow in result.paragraphs if isinstance(flow, TextFlowItem)
                       for f in flow.children if isinstance(f, SourceTextFragment)
                       for p in f.content if isinstance(p, str))
        self.assertEqual(text, 'A reentry works.')
        # A later model may preserve the literal spelling. Nothing after
        # adoption detects a hyphenated pair or undoes that explicit choice.
        one['layouts'][0]['text_removals'] = []
        result = restore_streams([compile_page(first, one), compile_page(second, two)])
        text = ''.join(p for flow in result.paragraphs if isinstance(flow, TextFlowItem)
                       for f in flow.children if isinstance(f, SourceTextFragment)
                       for p in f.content if isinstance(p, str))
        self.assertEqual(text, 'A re‐entry works.')


class ModelTextOperationsTests(IsolatedAsyncioTestCase):
    async def test_model_can_reject_initial_hyphen_suggestion_in_complete_candidate(self):
        source = initial_join(('text', (0, 0, 100, 20), 'Well‐'),
                       ('text', (0, 20, 100, 40), 'known fact.'))
        messages_seen = []

        async def request(messages, _index, _maximum):
            messages_seen.append(messages)
            packet = expanded_packet(json.loads(messages[1].message)['target_page'])
            self.assertEqual(packet['layouts'][0]['text'], 'Well‐')
            self.assertEqual(packet['layouts'][0]['projected_text'], 'Well')
            self.assertTrue(packet['layouts'][0]['initial_proposal']['text_removals'])
            data = candidate(source)
            for item in data['layouts']:
                item['text_removals'] = []
            data['layouts'][1]['continues_from_previous'] = True
            return serialize_plan(data)

        result = await repair_page_with_llm(previous_page=None, target_page=source, next_page=None,
            previous_pass_probability=None, next_pass_probability=None, request=request)
        self.assertEqual(len(messages_seen), 1)
        self.assertEqual(text_of(roundtrip(result)), 'Well‐known fact.')

    async def test_ready_stream_and_batch_share_pre_model_word_suggestions(self):
        from pdf_craft.extractor.refinement import refine_ready
        pages = [decode_page(fromstring(
            f'<page index="{index}"><body><layout ref="text" det="1,1,100,30">{text}'
            '</layout></body></page>')) for index, text in [(1, 'An inter‐'), (2, 'national fact.')]]
        batch = project_window(pages, False)
        observed = []

        async def raw():
            for page in pages:
                yield page

        async def accept(_previous, target, _following):
            observed.append(target)
            return target

        with TemporaryDirectory() as directory:
            assembly = PageAssembly(Path(directory)/'stream.sqlite')
            async for result in refine_ready(raw(), accept, Path(directory)/'refinement',
                                             lambda: [1, 2], includes_footnotes=False):
                assembly.append(result.page)
            assembly.finish()
            from pdf_craft.extractor.chapter.stream import chapters_from_disk
            chapters = list(chapters_from_disk(assembly.database, TocInfo([], [])))
        self.assertEqual(''.join(text_of(c) for c in chapters), 'An international fact.')
        assert batch[0] is not None
        self.assertEqual(observed[0].layouts[0].annotations.text_removals,
                         batch[0].layouts[0].annotations.text_removals)

    async def test_recovery_of_adopted_pages_keeps_heading_and_literal_text_decisions(self):
        from types import SimpleNamespace
        from pdf_craft._policy import policy_operation
        from pdf_craft.common import save_xml
        from pdf_craft.document import ExtractionPaths
        from pdf_craft.extractor.recovery import recover_chapters
        from pdf_craft.extractor.chapter.analysis_types import PageStructureError
        from pdf_craft.pdf import encode as encode_page
        from tests.test_anchored_page_recovery import RecoveryOCR
        pages = [decode_page(fromstring(
            f'<page index="{i}"><body><layout ref="text" det="1,1,190,40">{text}'
            '</layout></body></page>')) for i, text in [(1, 'Well‐'), (2, 'known text.'), (3, 'Failure.')]]
        first, second, third = project_window(pages, False)
        assert first is not None and second is not None and third is not None
        one, two = candidate(first), candidate(second)
        one['layouts'][0].update(text_removals=[], paragraph_role='heading', paragraph_level=4)
        two['layouts'][0].update(continues_from_previous=True, paragraph_role='heading', paragraph_level=4)
        adopted = [compile_page(first, one), compile_page(second, two), third]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root/'ocr'
            raw.mkdir()
            for page in pages:
                save_xml(encode_page(page), raw/f'page_{page.index}.xml')
            assembly = PageAssembly(root/'original.sqlite')
            for page in adopted:
                assembly.append(page)
            assembly.finish()
            draft = SimpleNamespace(pdf_path=root/'source.pdf', pages_path=raw,
                                    extraction_paths=ExtractionPaths.at(root/'extraction'),
                                    toc=TocInfo([], []), dpi=300, includes_footnotes=False)

            @policy_operation
            async def recover(*, error_policy):
                del error_policy  # The decorator installs the operation policy.
                return await recover_chapters(draft, RecoveryOCR(), PageStructureError('page failure', 3),
                    root/'recovery', adopted_database=assembly.database, aborted=lambda: False)

            # Already adopted pages are not re-analyzed on restoration.
            with patch('pdf_craft.extractor.chapter.primary.PrimaryPages.append',
                       side_effect=AssertionError('re-analyzing adopted pages')):
                result = await recover(error_policy=True)
            partition_adopted(result.chapters, root/'final', TocInfo([], []))
            chapters = [decode(read_xml(p)) for p in chapter_paths(root/'final')]
            self.assertEqual(''.join(text_of(c) for c in chapters), 'Well‐known text.')
            self.assertEqual([flow.level for c in chapters for flow in c.flow_items
                              if isinstance(flow, TextFlowItem)], [4])
