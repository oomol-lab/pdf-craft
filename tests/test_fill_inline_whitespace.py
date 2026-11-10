"""Whitespace is sentence content between inline formula/formatting slots."""
# pylint: disable=protected-access
from unittest import TestCase
from xml.etree.ElementTree import fromstring

from pdf_craft.transformer.xml_translator.segment import search_text_segments


class InlineWhitespaceTests(TestCase):


    def test_inline_gap_survives_segmentation_but_block_indentation_does_not(self):
        element = fromstring('<root>\n <p><em>one</em> <strong>two</strong></p>\n <p>end</p>\n</root>')
        self.assertEqual(''.join(s.text for s in search_text_segments(element)), 'one twoend')
