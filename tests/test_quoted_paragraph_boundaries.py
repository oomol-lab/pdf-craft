"""Quoted words and attribution must survive dialogue boundary handling."""
import unittest
from xml.etree.ElementTree import fromstring

from pdf_craft.pdf import decode
from pdf_craft.extractor.refinement import _project_window

from pdf_craft.extractor.chapter.mergeable import check_mergeable


class QuotedParagraphBoundaryTests(unittest.TestCase):
    def test_complete_quoted_turns_remain_separate(self):
        cases = [
            ('"Proceed!"', '"Wait!"'),
            ('“到站了！”', '“请下车。”'),
            ('「確認済みです。」', '「次へ進みます。」'),
            ("'Finished.'", "'Already?'"),
            ('The guard answered: "No."', '"Then we must return."'),
        ]
        for left, right in cases:
            with self.subTest(left=left, right=right):
                self.assertFalse(check_mergeable([left], [right]))

    def test_quoted_terms_and_attribution_do_not_force_a_boundary(self):
        cases = [
            ('an "interface"', 'is a contract'),
            ('"A sample,"', 'the author wrote.'),
            ('"Stop."', 'she whispered.'),
            ('“接下来，”', '他继续说道。'),
            ('an "open', 'quotation"'),
        ]
        for left, right in cases:
            with self.subTest(left=left, right=right):
                self.assertTrue(check_mergeable([left], [right]))

    def test_real_local_projection_separates_turns_in_both_modes(self):
        for enabled in (False, True):
            pages = [decode(fromstring(
                f"<page index='{index}'><body>"
                f"<layout ref='text' det='10,10,90,30'>{text}</layout>"
                "</body></page>"
            )) for index, text in [(1, '&quot;Proceed!&quot;'), (2, '&quot;Wait!&quot;')]]
            with self.subTest(footnotes=enabled):
                result = _project_window([*pages, None], enabled)
                target = result[1]
                assert target is not None
                self.assertFalse(target.layouts[0].continues_from_previous)
                self.assertEqual(pages[0].body_layouts[0].text, '"Proceed!"')

    def test_local_projection_keeps_a_quoted_term_connected(self):
        pages = [decode(fromstring(
            f"<page index='{index}'><body>"
            f"<layout ref='text' det='10,10,90,30'>{text}</layout>"
            "</body></page>"
        )) for index, text in [(1, 'an &quot;interface&quot;'), (2, 'is a contract.')]]
        result = _project_window([*pages, None], False)
        target = result[1]
        assert target is not None
        self.assertTrue(target.layouts[0].continues_from_previous)
