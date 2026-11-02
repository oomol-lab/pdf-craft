"""JEV sees settled local edges and unabridged evidence, never refined neighbors."""
import unittest
from copy import deepcopy
from dataclasses import replace
from xml.etree.ElementTree import fromstring

from pdf_craft.pdf import decode, PageLayout
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.extractor.chapter.page_review import build_jev_review_requests, build_jev_review_states


def page(index, text):
    return decode(fromstring(f"<page index='{index}'><body><layout ref='text' det='10,10,90,90'>{text}</layout></body></page>"))


class JEVWindowEvidenceTests(unittest.TestCase):
    def test_image_caption_reaches_initial_review_in_both_modes(self):
        source = page(1, 'Body passage.')
        source.body_layouts.append(PageLayout(
            ref='image', det=(10, 30, 90, 80), text='Illustration credit', order=1, hash=None))
        before = deepcopy(source)
        for footnotes in (False, True):
            projected = _project_window([None, source, None], footnotes)[1]
            assert projected is not None
            _, state = next(iter(build_jev_review_states(
                [source], [projected], {1: (100, 100)}, includes_footnotes=footnotes)))
            image = next(x for x in state['target_page']['layouts'] if x['source_type'] == 'image')
            self.assertEqual(image['raw_text'], 'Illustration credit')
            self.assertEqual(image['analysed_text'], 'Illustration credit')
        self.assertEqual(source, before)

    def test_image_review_uses_visible_fields_without_opaque_content_fallback(self):
        source = page(1, 'Body passage.')
        source.body_layouts.append(PageLayout(
            ref='image', det=(10, 30, 90, 80), text='', order=1, hash=None))
        projected = _project_window([None, source, None], False)[1]
        assert projected is not None
        index = next(i for i, x in enumerate(projected.layouts) if getattr(x.source, 'ref', None) == 'image')
        layout = projected.layouts[index]
        asset = replace(layout.source, title=('Heading',), caption=('Description',), content=('OPAQUE_PIXEL_TEXT',))
        projected.layouts[index] = replace(layout, source=asset)
        def observed():
            _, state = next(iter(build_jev_review_states(
                [source], [projected], {1: (100, 100)}, includes_footnotes=False)))
            return next(x for x in state['target_page']['layouts'] if x['source_type'] == 'image')['raw_text']
        self.assertEqual(observed(), 'Heading Description')
        projected.layouts[index] = replace(layout, source=replace(asset, title=(), caption=()))
        self.assertEqual(observed(), '')
        self.assertEqual(asset.content, ('OPAQUE_PIXEL_TEXT',))

    def test_neighbor_image_geometry_and_caption_remain_read_only_evidence(self):
        previous = page(1, 'A detached illustration description.')
        previous.body_layouts.append(PageLayout(
            ref='image', det=(10, 30, 90, 80), text='Image credit', order=1, hash=None))
        current = page(2, 'Running prose resumes.')
        for footnotes in (False, True):
            analyses = _project_window([previous, current, None], footnotes)
            _, state = next(iter(build_jev_review_states(
                [previous, current], [x for x in analyses if x], {1: (100, 100), 2: (100, 100)},
                includes_footnotes=footnotes, target_page_index=2)))
            assets = state['previous_page']['assets']
            self.assertEqual(len(assets), 1)
            self.assertEqual(assets[0]['raw_text'], 'Image credit')
            self.assertEqual(assets[0]['bbox_normalized'], [.1, .3, .9, .8])

    def test_footnote_review_gets_its_own_rubric_copy(self):
        source = page(1, 'A reading passage.')
        target = _project_window([None, source, None], True)[1]
        assert target is not None
        def packets():
            return list(build_jev_review_requests([source], [target], {1: (100, 100)}))
        with_notes = packets()
        name = next(iter(with_notes[0][1]['questions']))
        with_notes[0][1]['questions'][name]['instructions'].clear()
        self.assertTrue(packets()[0][1]['questions'][name]['instructions'])

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
        observed = packet['state']['local_raw_evidence']['target_page']['layouts'][0]
        self.assertIn('这是一条藏在中间的脚注解释', observed['raw_text'])
        self.assertNotIn('continues_to_next', observed)
        self.assertNotIn('continues_from_previous', observed)
        self.assertIn('owner_0', packet['state']['checks'])


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
        texts = [item['raw_text'] for item in packet['state']['local_raw_evidence']['previous_page']['paragraph_boundary']]
        self.assertIn('The analyst asked: was he angry?', texts)
        self.assertIn('② A second misplaced footnote.', texts)
