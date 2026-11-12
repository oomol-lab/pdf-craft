"""OCR-only literal tail recovery; no model, PDF conversion or translation."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from typing import Any, cast
from unittest.mock import patch
from xml.etree.ElementTree import tostring

from PIL import Image

from pdf_craft.common import AssetHub
from pdf_craft.ocr_config import DeepSeekOCRLocalConfig
from pdf_craft.ocr_config import DeepSeekOCRVendorConfig
from pdf_craft.pdf.ocr import _decode_vendor_response, _PreparedVendorPage
from pdf_craft.pdf.vendor_ocr import VendorOCRResponse
from pdf_craft.extractor.chapter.source import capture_sources
from pdf_craft.pdf.repetition_tail import repeated_tail, clean_page
from pdf_craft.pdf.types import Page, PageLayout, decode, encode
from pdf_craft.pdf.page_extractor import PageExtractorNode
from tests.test_page_extractor_structured import _Structured, _Block


def layout(text, order=0, ref='text'):
    return PageLayout(ref, (0, 0, 100, 100), text, order, None)


class OCRRepetitionTailTests(unittest.TestCase):
    def test_real_source_derived_tails_match_visually_checked_prefixes(self):
        evidence = json.loads((Path(__file__).parent / 'fixtures/ocr_repeated_tails.json').read_text())
        for case in evidence['cases']:
            with self.subTest(case=case['label']):
                cut = repeated_tail(case['text'])
                assert cut is not None
                self.assertEqual(case['text'][:cut.start], case['expected'])
                self.assertIsNone(repeated_tail(case['expected']))
                # These are source-text fixtures, not manufactured vendor responses.
                self.assertIn('NOT raw OCR', case['origin'])


    def test_preserves_original_prefix_and_first_full_occurrence(self):
        unit = r'\(\frac{49x}{100} = y\)'
        prefix = '17. (4) 49% of \\(x = y\\) '
        original = prefix + (unit + '\n\u3000') * 659 + unit[:14]
        cut = repeated_tail(original)
        assert cut is not None
        self.assertEqual(original[:cut.start], prefix + unit)
        self.assertEqual(cut.end, len(original))
        self.assertIsNone(repeated_tail(original[:cut.start]))

    def test_rotated_suffix_does_not_cut_inside_tex_group(self):
        for unit in (r'\(\mathrm{Alter:}\)', r'$\frac{2}{3}=x$'):
            original = 'A valid prefix. ' + (unit + '\n') * 40 + unit[:7]
            cut = repeated_tail(original)
            assert cut is not None
            self.assertEqual(original[:cut.start], 'A valid prefix. ' + unit)

    def test_uncertain_unclosed_prefix_is_not_regenerated(self):
        original = 'prefix \\(' + ('Long repeating prose. ' * 40)
        self.assertIsNone(repeated_tail(original))

    def test_normal_repetition_and_non_suffix_remain_literal(self):
        for text in ('Echo. ' * 8, '0123456789 ' * 100, '-=' * 500,
                     'Echo. ' * 100 + 'Distinct final content.',
                     '<table>' + '<td>Same</td>' * 100 + '</table>',
                     '```python\n' + 'print(value)\n' * 100 + '```',
                     'Examples: x=x, x=x, x=x.', 'A' * 10000):
            with self.subTest(text=text[:50]):
                self.assertIsNone(repeated_tail(text))

    def test_cross_block_tail_keeps_ids_geometry_and_first_occurrence(self):
        original = Page(3, None, [layout('Important prefix.', 0)] +
                        [layout('Repeated words.', i+1) for i in range(40)], [], 12, 34)
        before = deepcopy(original)
        with self.assertLogs('pdf_craft.pdf.repetition_tail') as logs:
            result = clean_page(original)
        self.assertEqual(original, before)
        self.assertEqual([x.text for x in result.body_layouts[:2]], ['Important prefix.', 'Repeated words.'])
        self.assertTrue(all(not x.text for x in result.body_layouts[2:]))
        self.assertEqual([(x.det, x.order) for x in result.body_layouts],
                         [(x.det, x.order) for x in original.body_layouts])
        self.assertEqual((result.input_tokens, result.output_tokens), (12, 34))
        self.assertIn('sample', getattr(logs.records[0], 'ocr_tail_cut'))
        self.assertEqual(clean_page(result), result)

    def test_does_not_bridge_assets_or_streams(self):
        items = [layout('Repeated words.', i) for i in range(10)]
        items.append(layout('Repeated words.' * 100, 10, 'table'))
        items.extend(layout('Repeated words.', i) for i in range(11, 21))
        page = Page(1, None, items, [layout('Repeated words.', i) for i in range(10)], 0, 0)
        self.assertEqual(clean_page(page), page)

    def test_cross_block_code_fences_preserve_whole_page_before_any_cuts(self):
        for marker in ('```', '~~~'):
            for closing in (True, False):
                for split_streams in (True, False):
                    with self.subTest(marker=marker, closing=closing, split_streams=split_streams):
                        items = [layout(marker + 'python', 0), layout('print(value)\n' * 40, 1)]
                        if closing:
                            items.append(layout(marker, 2))
                        # A second repeated block must also remain untouched;
                        # its relationship to an unmatched fence is uncertain.
                        items.append(layout('Repeated words. ' * 40, len(items)))
                        page = Page(3, None, items[:1] if split_streams else items,
                                    items[1:] if split_streams else [], 12, 34)
                        before = deepcopy(page)
                        result = clean_page(page)
                        self.assertEqual(page, before)
                        self.assertEqual(result, before)
                        self.assertEqual(clean_page(result), before)
                        for stream in ('body_layouts', 'footnotes_layouts'):
                            self.assertEqual([(x.text, x.det, x.order) for x in getattr(result, stream)],
                                             [(x.text, x.det, x.order) for x in getattr(before, stream)])

    def test_inner_bad_block_tail_does_not_remove_following_valid_block(self):
        page = Page(1, None, [layout('Prefix ' + 'Repeated words. ' * 40),
                             layout('Next unique valid paragraph.', 1)], [], 0, 0)
        result = clean_page(page)
        self.assertEqual(result.body_layouts[0].text, 'Prefix Repeated words.')
        self.assertEqual(result.body_layouts[1].text, page.body_layouts[1].text)

    def test_old_cache_is_normalized_without_rewriting_xml_or_identity(self):
        page = Page(63, None, [layout('Valid prefix ' + 'Repeated words. ' * 40)], [], 5, 9)
        root = encode(page)
        before = tostring(root)
        normalized = decode(root)
        self.assertEqual(normalized.body_layouts[0].text, 'Valid prefix Repeated words.')
        self.assertEqual(tostring(root), before)
        self.assertEqual(decode(encode(normalized)), normalized)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'page_63.xml'
            path.write_bytes(before)
            from pdf_craft.extractor.metadata import _read_pages
            values = _read_pages([(63, path)])
            self.assertEqual(values[0].text, 'Valid prefix Repeated words.')
            self.assertEqual(path.read_bytes(), before)

    def test_vendor_and_local_result_mapping_use_same_cleanup(self):
        from doc_page_extractor.types import OCRPageResult
        raw = 'Important prefix. ' + 'Repeated words. ' * 40
        structured = _Structured([_Block('text', (0, 0, 100, 100), text=raw)])
        result = OCRPageResult(layouts=[], source='fixture', structured=cast(Any, structured), raw_text=raw)
        node = PageExtractorNode(DeepSeekOCRLocalConfig())
        with TemporaryDirectory() as directory, Image.new('RGB', (100, 100), 'white') as image:
            # results2page is the common local/vendor materialization boundary.
            with patch('pdf_craft.pdf.page_extractor.printed_line_metrics', return_value=None), \
                 patch('pdf_craft.pdf.page_extractor.first_line_inset', return_value=None), \
                 patch('pdf_craft.pdf.page_extractor.has_vertical_columns', return_value=False):
                page = node.results2page(results=[(image, result)], page_index=2,
                    asset_hub=AssetHub(Path(directory)), includes_footnotes=False, raw_image=None,
                    plot_path=None, input_tokens=3, output_tokens=4, aborted=lambda: False)
            self.assertEqual(page.body_layouts[0].text, 'Important prefix. Repeated words.')
            self.assertEqual(result.raw_text, raw)
            self.assertEqual(structured.blocks[0].text, raw)

    def test_large_periodic_and_nonperiodic_inputs_have_bounded_cost(self):
        start = time.perf_counter()
        periodic = 'Header ' + 'Repeated words. ' * 20000
        self.assertIsNotNone(repeated_tail(periodic))
        self.assertIsNone(repeated_tail(periodic + 'Different ending.'))
        self.assertLess(time.perf_counter() - start, 5)

    def test_constructed_deepseek_protocol_through_production_decoder_and_source_capture(self):
        # Constructed protocol example, NOT a recovered historical vendor reply.
        raw_text = 'Valid introduction. ' + 'Repeated words. ' * 40
        protocol = ('<|ref|>text<|/ref|><|det|>[[100,100,900,400]]<|/det|>' + raw_text +
                    '\n<|ref|>image<|/ref|><|det|>[[100,500,900,900]]<|/det|>')
        response = VendorOCRResponse({'constructed_fixture': True}, raw_text=protocol)
        original_response = deepcopy(response)
        config = DeepSeekOCRVendorConfig(base_url='https://example.invalid/v1', api_key='fixture', model='fixture')
        with TemporaryDirectory() as directory, Image.new('RGB', (100, 100), 'white') as image:
            path = Path(directory) / 'request.png'
            image.save(path)
            prepared = _PreparedVendorPage(2, path, path, 0, 1)
            result = _decode_vendor_response(config, prepared, response)
            original_layouts = deepcopy(result.layouts)
            self.assertEqual(len(result.layouts), 2)
            self.assertIsNotNone(result.structured)
            with patch('pdf_craft.pdf.page_extractor.printed_line_metrics', return_value=None), \
                 patch('pdf_craft.pdf.page_extractor.first_line_inset', return_value=None), \
                 patch('pdf_craft.pdf.page_extractor.has_vertical_columns', return_value=False):
                page = PageExtractorNode(config).results2page(
                    results=[(image, result)], page_index=2, asset_hub=AssetHub(Path(directory)/'assets'),
                    includes_footnotes=False, raw_image=None, plot_path=None,
                    input_tokens=3, output_tokens=4, aborted=lambda: False)
            text, asset = page.body_layouts
            self.assertEqual(text.text, 'Valid introduction. Repeated words.')
            self.assertEqual((text.det, text.order), ((10, 10, 90, 40), 0))
            self.assertEqual((asset.ref, asset.det, asset.order), ('image', (10, 50, 90, 90), 1))
            self.assertTrue(asset.hash)
            self.assertTrue(any((Path(directory)/'assets').rglob('*.png')))
            sources = capture_sources([page])
            identity, source = sources[(2, id(text))]
            self.assertEqual(identity, 0)
            self.assertEqual(source.text, text.text)
            self.assertNotIn('Repeated words. Repeated words.', source.text)
            self.assertEqual(source.reading_order, 0)
            self.assertEqual(sources[(2, id(asset))][0], 1)
            self.assertEqual(result.layouts, original_layouts)
            self.assertEqual(response, original_response)
            self.assertEqual(result.raw_text, protocol)
            self.assertEqual(clean_page(page), page)
