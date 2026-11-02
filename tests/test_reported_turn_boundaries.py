"""A completed utterance and a fresh reporting frame need not be one paragraph."""
import unittest
from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
from pdf_craft.extractor.chapter.jointer import Jointer


def paragraph(text, order=0):
    return TextFlowItem('body', -1, [SourceTextFragment(1, order,
        (0, order * 30, 1000, order * 30 + 20), [text])])


def connected(left, right):
    return Jointer([])._can_merge_paragraphs(paragraph(left), paragraph(right, 1))  # pylint: disable=protected-access


class ReportedTurnBoundaryTests(unittest.TestCase):
    def test_fresh_reporting_frame_after_completed_utterance_is_separate(self):
        for left in ('客人问：“现在可以出发了吗？”', '客人說：「現在可以出發了嗎？」',
                     '他回答:"事情已经完成了。"', '几个人向门外望去……'):
            for right in ('另一人说：“还需要等一会儿。”', '他认真地回了一句：“已经准备好了。”'):
                with self.subTest(left=left, right=right):
                    self.assertFalse(connected(left, right))

    def test_an_unfinished_frame_outside_the_quote_is_not_closed(self):
        for left in ('如果客人问：“现在可以走吗？”', '要是对方说：“事情已经完成了。”',
                     '倘若他说：“已经准备好了！”', '虽然他回答：“事情已经结束了。”',
                     '若客人问：“现在出发吗？”', '当客人问：“现在出发吗？”',
                     '只有客人说：“已经准备好了！”'):
            self.assertTrue(connected(left, '另一人说：“那就出发。”'))

    def test_inline_quote_and_postpositive_attribution_keep_existing_connection(self):
        for left, right in (('他说了这个词的前半inter', '另一个人说：“等一下。”'),
                            ('这是一种“方法”', '而不是另一种不同的工具。'),
                            ('“请稍等。”', '他轻声说，然后走向门边。'),
                            ('他刚说到：“请', '另一个人说：“等一下。”')):
            self.assertTrue(connected(left, right))

    def test_origin_frame_is_kept_across_source_fragments(self):
        left = paragraph('如果客人问：“现在')
        left.children.append(SourceTextFragment(2, 0, (0, 0, 1000, 20), ['可以出发了吗？”']))
        self.assertTrue(Jointer([])._can_merge_paragraphs(left, paragraph('另一人说：“还要等。”', 1)))  # pylint: disable=protected-access
