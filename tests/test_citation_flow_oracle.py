"""A real note continuation must not be forced into a fifth definition."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZipFile

from pdf_craft_tool.smoke.citation_footnotes import check_reading_flows


class CitationFlowOracleTests(TestCase):
    def check_fixture(self, *, missing=False, wrong_mark=False, detached=False,
                      paragraphs=((0,), (1,), (2,)), leaked=False):
        root = Element('chapter')
        flow = SubElement(root, 'flow')
        for orders in paragraphs:
            text = SubElement(flow, 'text')
            for order in orders:
                SubElement(text, 'fragment', page_index='1', source_order=str(order)).text = 'Main prose.'
        text = list(flow)[-1]
        if leaked:
            SubElement(text, 'fragment', page_index='1', source_order='6').text = '试着泛化'
        for page in (2, 3):
            SubElement(text, 'fragment', page_index=str(page), source_order='0').text = 'Continued prose.'
        refs = SubElement(root, 'references')
        for identity, mark, content in (
                ('1-1', '①', '我不会在本书'), ('1-2', '②', '对拉康来说'),
                ('1-3', '③', '我们可以说试着泛化米勒'), ('3-1', '①', '主人话语')):
            ref = SubElement(refs, 'ref', id=identity)
            SubElement(ref, 'mark').text = '②' if wrong_mark and identity == '1-1' else mark
            note = SubElement(SubElement(ref, 'flow'), 'text')
            SubElement(note, 'fragment', page_index=identity[0], source_order='4').text = content
            if identity == '1-3' and not detached:
                SubElement(note, 'fragment', page_index='2', source_order='4').text = 'Note continuation.'
            if missing and identity == '3-1':
                refs.remove(ref)
        with TemporaryDirectory() as directory:
            output = Path(directory)
            with ZipFile(output/'book.pcex', 'w') as archive:
                archive.writestr('chapters/chapter_head.xml', tostring(root))
                archive.writestr('furnitures.xml', '<furnitures/>')
            return check_reading_flows(output, True)

    def test_four_printed_definitions_and_real_continuation_pass(self):
        result = self.check_fixture()
        self.assertEqual(result['definitions'], 4)
        self.assertTrue(result['passed'])

    def test_missing_mark_content_or_cross_page_note_does_not_pass(self):
        for failure in ('missing', 'wrong_mark', 'detached'):
            with self.subTest(failure=failure):
                self.assertFalse(self.check_fixture(**{failure: True})['passed'])

    def test_concluding_sentence_can_join_preceding_argument(self):
        self.assertTrue(self.check_fixture(paragraphs=((0,), (1, 2)))['passed'])

    def test_alternative_does_not_allow_note_leaks_or_arbitrary_merges(self):
        self.assertFalse(self.check_fixture(paragraphs=((0,), (1, 2)), leaked=True)['passed'])
        self.assertFalse(self.check_fixture(paragraphs=((0, 1, 2),))['passed'])
