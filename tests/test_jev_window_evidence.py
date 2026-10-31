"""JEV sees settled local edges and unabridged evidence, never refined neighbors."""
import unittest
from xml.etree.ElementTree import fromstring

from pdf_craft.pdf import decode
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.extractor.chapter.page_review import build_jev_review_requests, _review_questions


def page(index, text):
    return decode(fromstring(f"<page index='{index}'><body><layout ref='text' det='10,10,90,90'>{text}</layout></body></page>"))


class JEVWindowEvidenceTests(unittest.TestCase):
    def test_each_review_gets_its_own_mode_specific_rubric(self):
        body_only = _review_questions(False)
        with_notes = _review_questions(True)
        name = "page_passes_strict_standard"
        self.assertNotIn("citation_standard", body_only[name]["instructions"])
        self.assertIn("citation_standard", with_notes[name]["instructions"])
        body_only[name]["instructions"]["scope"].clear()
        with_notes[name]["instructions"]["citation_standard"].clear()
        self.assertTrue(_review_questions(False)[name]["instructions"]["scope"])
        self.assertTrue(_review_questions(True)[name]["instructions"]["citation_standard"])

    def test_target_outgoing_edge_uses_available_raw_next_page(self):
        pages = [page(1, 'An earlier paragraph.'), page(2, 'This sentence continues'), page(3, 'on the next page.')]
        for footnotes in (False, True):
            with self.subTest(footnotes=footnotes):
                before = [p.body_layouts[0].text for p in pages]
                _, target, following = _project_window(pages, footnotes)
                assert target is not None and following is not None
                self.assertTrue(target.layouts[-1].continues_to_next)
                self.assertTrue(following.layouts[0].continues_from_previous)
                self.assertEqual(before, [p.body_layouts[0].text for p in pages])

    def test_distinct_paragraphs_are_not_forced_to_connect(self):
        _, target, following = _project_window([None, page(2, 'A finished paragraph.'), page(3, '## New heading')], False)
        assert target is not None and following is not None
        self.assertFalse(target.layouts[-1].continues_to_next)
        self.assertFalse(following.layouts[0].continues_from_previous)

    def test_middle_ownership_evidence_is_not_silently_removed(self):
        text = '前文。' * 100 + '① 这是一条藏在中间的脚注解释。' + '后文。' * 100
        source = page(1, text)
        target = _project_window([None, source, None], False)[1]
        assert target is not None
        packet = dict(build_jev_review_requests([source], [target], {1: (100, 100)}, includes_footnotes=False))[1]
        observed = packet['state']['target_page']['layouts'][0]
        self.assertIn('这是一条藏在中间的脚注解释', observed['raw_text'])
        self.assertNotIn('continues_to_next', observed)
        self.assertIn('continues_from_previous', observed)


    def test_neighbor_ownership_mistakes_do_not_hide_real_body_evidence(self):
        previous = decode(fromstring("""<page index='1'><body>
            <layout ref='text' det='10,10,90,30'>The analyst asked: was he angry?</layout>
            <layout ref='text' det='10,60,90,70'>① A misplaced footnote.</layout>
            <layout ref='text' det='10,70,90,80'>Another note paragraph.</layout>
            <layout ref='text' det='10,80,90,90'>② A second misplaced footnote.</layout>
            </body></page>"""))
        current = page(2, 'Or did he simply misunderstand her?')
        projected = _project_window([previous, current, None], False)
        packet = dict(build_jev_review_requests(
            [previous, current], [p for p in projected if p],
            {1: (100, 100), 2: (100, 100)}, includes_footnotes=False))[2]
        texts = [item['raw_text'] for item in packet['state']['previous_page']['paragraph_boundary']]
        self.assertIn('The analyst asked: was he angry?', texts)
        self.assertIn('② A second misplaced footnote.', texts)
