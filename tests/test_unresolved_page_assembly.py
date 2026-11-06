"""Service #291: unresolved notes retain their own paragraph stream at adoption."""
# pylint: disable=protected-access
from collections import Counter
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.assembly import PageAssembly
from pdf_craft.extractor.chapter.chapter import SourceAsset, SourceTextFragment, TextFlowItem, decode
from pdf_craft.extractor.chapter.page_analysis import UnindexedCitation, analyse_pages
from pdf_craft.extractor.chapter.analysis_types import PageStructureError
from pdf_craft.extractor.chapter.stream import chapters_from_disk
from pdf_craft.extractor.toc.types import TocInfo
from tests.test_page_analysis import _fragment


def text(role, *parts):
    return TextFlowItem(role, -1, [_fragment(page, order, value) for page, order, value in parts])


def fragments(items):
    return [(part.page_index, part.source_order, ''.join(value for value in part.content if isinstance(value, str)))
            for item in items if isinstance(item, TextFlowItem)
            for part in item.children if isinstance(part, SourceTextFragment)]


class UnresolvedPageAssemblyTests(TestCase):
    def open_assembly(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'assembly.sqlite'
        events = []
        # Decode callbacks through the public chapter codec, including metadata.
        def receive(node):
            root, flow = Element('chapter'), Element('flow')
            flow.append(node)
            root.append(flow)
            events.extend(decode(root).flow_items)
        assembly = PageAssembly(path, on_body=receive)
        return assembly, events, path

    def final_bodies(self, path):
        return [item for chapter in chapters_from_disk(path, TocInfo([], [])) for item in chapter.flow_items]

    def test_page_15_note_continuation_does_not_join_a_body_heading(self):
        pages = analyse_pages([14, 15], [
            text('body', (14, 0, 'Earlier body.')),
            text('heading', (15, 0, 'Section heading')),
        ], [UnindexedCitation(14, (text('body', (14, 1, 'Detached note '), (15, 1, 'continues.')),))])
        original = deepcopy(pages)
        assembly, events, path = self.open_assembly()
        assembly.append(pages[0])
        assembly.append(pages[1])
        self.assertEqual(fragments(events), [(14, 0, 'Earlier body.')])
        self.assertIn((14, 15), assembly._run('SELECT left_page,right_page FROM edges'))
        assembly.finish()
        self.assertEqual([item.role for item in events], ['body', 'body', 'heading'])
        self.assertEqual(fragments(events[1:2]), [(14, 1, 'Detached note '), (15, 1, 'continues.')])
        self.assertEqual(self.final_bodies(path), events)
        self.assertEqual(pages, original)
        self.assertFalse(assembly._run('SELECT * FROM refs'))

    def test_page_16_body_continuation_skips_the_previous_orphan_heading(self):
        pages = analyse_pages([15, 16, 17], [
            text('body', (15, 0, 'Body starts '), (16, 0, 'and ends.')),
            text('body', (17, 0, 'Later paragraph.')),
        ], [UnindexedCitation(15, (text('heading', (15, 1, 'Detached heading')),))])
        assembly, events, path = self.open_assembly()
        assembly.append(pages[0])
        assembly.append(pages[1])
        self.assertFalse(events, 'An orphan must not overtake the earlier open body paragraph')
        # The closed orphan is already on disk while the body remains open.
        self.assertEqual(len(assembly._run('SELECT value FROM bodies')), 1)
        assembly.append(pages[2])
        self.assertEqual(len(events), 2, 'Closed units must not wait for EOF')
        self.assertEqual(fragments(events[:1]), [(15, 0, 'Body starts '), (16, 0, 'and ends.')])
        self.assertEqual(events[1].role, 'heading')
        assembly.finish()
        self.assertEqual(self.final_bodies(path), events)
        self.assertEqual(len(events), 3)

    def test_distinct_unresolved_notes_do_not_silently_merge_with_body_or_each_other(self):
        pages = analyse_pages([1, 2], [text('body', (1, 0, 'Main text.'))], [
            UnindexedCitation(1, (text('body', (1, 1, 'First unmarked note.')),)),
            UnindexedCitation(1, (text('body', (1, 2, 'Second note '), (2, 0, 'continues.')),)),
        ])
        assembly, events, path = self.open_assembly()
        for page in pages:
            assembly.append(page)
        assembly.finish()
        self.assertEqual([fragments([item]) for item in events], [
            [(1, 0, 'Main text.')], [(1, 1, 'First unmarked note.')],
            [(1, 2, 'Second note '), (2, 0, 'continues.')]])
        self.assertEqual(self.final_bodies(path), events)

    def test_trailing_body_asset_does_not_overtake_an_earlier_orphan(self):
        pages = analyse_pages([1, 2, 3], [
            text('body', (1, 0, 'Body starts '), (2, 0, 'and ends.')),
            SourceAsset(2, 'image', (0, 30, 100, 70)),
            text('body', (3, 0, 'Next.')),
        ], [UnindexedCitation(1, (text('heading', (1, 1, 'Orphan')),))])
        assembly, events, path = self.open_assembly()
        for page in pages:
            assembly.append(page)
        assembly.finish()
        self.assertEqual(fragments(events[:2]), [
            (1, 0, 'Body starts '), (2, 0, 'and ends.'), (1, 1, 'Orphan')])
        self.assertEqual(events[2].asset.ref, 'image')
        self.assertEqual(self.final_bodies(path), events)

    def test_orphan_with_missing_initial_predecessor_is_preserved_without_mutating_input(self):
        page = analyse_pages([8], [text('heading', (8, 0, 'Heading'))], [
            UnindexedCitation(8, (text('body', (8, 1, 'Tail with an unknown origin.')),))])[0]
        orphan = next(item for item in page.layouts if item.ownership == 'citation')
        orphan.annotations.continues_from_previous = True
        page.citations[0].continues_previous = True
        saved = deepcopy(page)
        assembly, events, _ = self.open_assembly()
        assembly.append(page)
        assembly.finish()
        self.assertEqual([item.role for item in events], ['heading', 'body'])
        self.assertEqual(page, saved)
        self.assertFalse(assembly._run('SELECT * FROM refs'), 'Do not invent a numbered definition')

    def test_cross_page_metadata_differences_do_not_veto_current_result(self):
        pages = analyse_pages([1, 2], [], [UnindexedCitation(1, (
            text('heading', (1, 0, 'Heading starts '), (2, 0, 'and continues.')),))])
        pages[1].layouts[0].annotations.paragraph_role = 'body'
        assembly, events, _ = self.open_assembly()
        for page in pages:
            assembly.append(page)
        assembly.finish()
        self.assertEqual(fragments(events), [(1, 0, 'Heading starts '), (2, 0, 'and continues.')])
        self.assertEqual(len(events), 1)

    def test_rejected_page_rolls_back_orphan_closure_new_streams_and_ready_callbacks(self):
        pages = analyse_pages([1, 2, 3], [
            text('body', (1, 0, 'Opening.')),
            text('body', (2, 0, 'Next '), (2, 1, 'paragraph.')),
            text('body', (3, 0, 'Last.')),
        ], [UnindexedCitation(1, (text('heading', (1, 1, 'Old orphan')),)),
            UnindexedCitation(2, (text('body', (2, 2, 'New orphan.')),))])
        invalid = deepcopy(pages[1])
        next(item for item in invalid.layouts if item.source_order == 1).annotations.paragraph_role = 'heading'
        assembly, events, path = self.open_assembly()
        assembly.append(pages[0])
        saved_keys = set(assembly.unresolved)
        with self.assertRaisesRegex(PageStructureError, 'GAP_PARAGRAPH_METADATA'):
            assembly.append(invalid)
        self.assertFalse(events)
        self.assertEqual(set(assembly.unresolved), saved_keys)
        self.assertFalse(assembly._run('SELECT value FROM bodies'))
        self.assertEqual(assembly._run('SELECT page FROM adopted'), [(1,)])
        for page in pages[1:]:
            assembly.append(page)
        assembly.finish()
        self.assertEqual(Counter(fragments(events)), Counter([
            (1, 0, 'Opening.'), (1, 1, 'Old orphan'), (2, 0, 'Next '),
            (2, 1, 'paragraph.'), (2, 2, 'New orphan.'), (3, 0, 'Last.')]))
        self.assertEqual(self.final_bodies(path), events)
