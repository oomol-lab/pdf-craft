"""Source-range round trips independent of the CFI generator's index."""

import unittest
from xml.etree.ElementTree import fromstring

from pdf_craft.pipeline.epub.cfi import ContentCFILocator
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.segment.text_segment import TextPosition


def resolve(root, path, offset):
    steps = [int(step) for step in path.split('/') if step]
    current = root
    for step in steps[:-1]:
        current = list(current)[step // 2 - 1]
    step = steps[-1]
    text = current.text or '' if step == 1 else list(current)[(step - 3) // 2].tail or ''
    encoded = text.encode('utf-16-le')
    return text, encoded[:offset * 2].decode('utf-16-le')


def endpoints(root, cfi):
    base, start, end = cfi[len('epubcfi('):-1].split(',')
    base = base.split('!')[1]
    results = []
    for endpoint in (start, end):
        path, offset = endpoint.rsplit(':', 1)
        results.append(resolve(root, base + path, int(offset)))
    return results[0], results[1]


class CFITests(unittest.TestCase):
    def test_cross_inline_text_tail_entities_and_utf16(self):
        root = fromstring('<html><head/><body><p> 😀 A &amp; B <em>inside</em> tail😀 </p></body></html>')
        locator = ContentCFILocator(root)
        segments = list(search_text_segments(root, track_origins=True))
        location = locator.capture(segments)
        start, end = endpoints(root, location.cfi('/6/2'))
        self.assertEqual(start[1], '')
        self.assertEqual(end[1], ' tail😀 ')
        self.assertEqual(location.end.offset, len(' tail😀 '.encode('utf-16-le')) // 2)

    def test_same_text_partial_range(self):
        root = fromstring('<html><body><p>A😀BC</p></body></html>')
        locator = ContentCFILocator(root)
        paragraph = root.find('./body/p')
        assert paragraph is not None
        start = locator.point(paragraph, TextPosition.TEXT, 1)
        end = locator.point(paragraph, TextPosition.TEXT, 2)
        from pdf_craft.pipeline.epub.cfi import ContentRange
        cfi = ContentRange(start, end).cfi('/6/2')
        first, last = endpoints(root, cfi)
        self.assertEqual(first[1], 'A')
        self.assertEqual(last[1], 'A😀')

    def test_normalized_whitespace_keeps_original_bounds(self):
        root = fromstring('<html><body><p>  a\n    b  </p></body></html>')
        segments = list(search_text_segments(root, track_origins=True))
        self.assertEqual(segments[0].text, ' a b ')
        location = ContentCFILocator(root).capture(segments)
        self.assertEqual(endpoints(root, location.cfi('/6/2'))[1][1], '  a\n    b  ')

    def test_capture_survives_tree_mutation(self):
        root = fromstring('<html><body><p>Original</p></body></html>')
        locator = ContentCFILocator(root)
        location = locator.capture(list(search_text_segments(root, track_origins=True)))
        expected = location.cfi('/6/2')
        root.clear()
        self.assertEqual(location.cfi('/6/2'), expected)

    def test_math_projection_carries_original_spans(self):
        root = fromstring('<html><body><p>Before <math><mi>x</mi><mo>+</mo><mn>1</mn></math> after.</p></body></html>')
        locator = ContentCFILocator(root)
        interrupter = XMLInterrupter()
        projected = list(interrupter.interrupt_source_text_segments(
            search_text_segments(root, track_origins=True)))
        formula = next(segment for segment in projected if segment.parent_stack[-1].tag == 'expression')
        first, last = endpoints(root, locator.capture([formula]).cfi('/6/2'))
        self.assertEqual(first[0], 'x')
        self.assertEqual(first[1], '')
        self.assertEqual(last[1], '1')

    def test_source_tracking_is_opt_in(self):
        root = fromstring('<p>A <b>B</b> C</p>')
        self.assertTrue(all(not segment.origins for segment in search_text_segments(root)))

    def test_invalid_offset_is_rejected(self):
        root = fromstring('<p>A</p>')
        with self.assertRaises(ValueError):
            ContentCFILocator(root).point(root, TextPosition.TEXT, 2)
