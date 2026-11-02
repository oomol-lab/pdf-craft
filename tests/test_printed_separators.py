"""A separately printed centered asterism is not unfinished prose."""
import unittest

from pdf_craft.extractor.chapter.chapter import SourceTextFragment, TextFlowItem
from pdf_craft.extractor.chapter.jointer import Jointer
from pdf_craft.extractor.chapter.primary import PrimaryPages, project_window
from pdf_craft.pdf import Page, PageLayout


def layout(order, box, text):
    return PageLayout(ref='text', det=box, text=text, order=order, hash=None)


class PrintedSeparatorTests(unittest.TestCase):
    def test_both_projection_paths_preserve_sources_in_both_footnote_modes(self):
        source = [layout(0, (100, 100, 900, 150), 'A publication line'),
                  layout(1, (475, 180, 525, 205), '*'),
                  layout(2, (200, 230, 800, 280), 'Another publication line')]
        page = Page(1, None, source, [], 0, 0)
        for footnotes in (False, True):
            for target in (PrimaryPages(footnotes).append(page),
                           project_window([None, page, None], footnotes)[1]):
                with self.subTest(footnotes=footnotes):
                    self.assertIsNotNone(target)
                    assert target is not None
                    self.assertEqual([item.source_order for item in target.layouts], [0, 1, 2])
                    self.assertEqual([item.continues_from_previous for item in target.layouts],
                                     [False, False, False])
                    for item, original in zip(target.layouts, source):
                        origin = item.source.origin
                        self.assertIsNotNone(origin)
                        assert origin is not None
                        self.assertEqual(origin.text, original.text)
                        self.assertEqual(item.source.bbox, original.det)

    def test_centered_separator_keeps_both_neighbors_separate_without_losing_text(self):
        for mark in ('*', '* * *', '⁂'):
            with self.subTest(mark=mark):
                source = [layout(0, (100, 100, 900, 150), 'A publication line'),
                          layout(1, (475, 180, 525, 205), mark),
                          layout(2, (200, 230, 800, 280), 'Another publication line')]
                result = list(Jointer([(1, source)]).execute())
                self.assertEqual([[child.source_order for child in item.children
                                   if isinstance(child, SourceTextFragment)]
                                  for item in result if isinstance(item, TextFlowItem)],
                                 [[0], [1], [2]])

    def test_inline_asterisk_is_not_a_separator(self):
        source = [layout(0, (100, 100, 900, 150), 'See the remark* about'),
                  layout(1, (100, 150, 900, 200), 'this expression')]
        paragraphs = [TextFlowItem(role='body', level=-1, children=[
            SourceTextFragment(page_index=1, source_order=item.order,
                               bbox=item.det, content=[item.text])]) for item in source]
        self.assertTrue(Jointer([])._can_merge_paragraphs(*paragraphs))  # pylint: disable=protected-access

    def test_side_by_side_symbol_is_not_a_separate_printed_line(self):
        source = [layout(0, (100, 100, 450, 150), 'An unfinished expression'),
                  layout(1, (460, 100, 480, 150), '*'),
                  layout(2, (490, 100, 900, 150), 'another expression')]
        self.assertEqual(len(list(Jointer([(1, source)]).execute())), 1)

    def test_left_aligned_isolated_mark_remains_ambiguous(self):
        source = [layout(0, (100, 100, 900, 150), 'A note reference'),
                  layout(1, (100, 180, 120, 205), '*'),
                  layout(2, (100, 230, 900, 280), 'might begin here')]
        self.assertEqual(len(list(Jointer([(1, source)]).execute())), 1)
