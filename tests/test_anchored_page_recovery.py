"""The real Figure 4 failure shape, through projection, repair and recovery."""
# pylint: disable=protected-access,unused-argument
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import parse, fromstring
import json

from PIL import Image

from pdf_craft import AsyncPDFCraft, ExtractionOptions, RetryPolicy, ErrorPolicyInput
from pdf_craft.common import XMLReader, save_xml, AssetHub
from pdf_craft.document.access import PageGeometry
from pdf_craft.error import NoUsableOCRPagesError, PDFError
from pdf_craft.extractor.chapter.chapter import (
    TextFlowItem, SourceAsset, SourceTextFragment, StandaloneAsset, decode as decode_chapter, encode,
)
from pdf_craft.extractor.chapter.generation import _resolve_pages, _generate_chapters
from pdf_craft.extractor.chapter.page_analysis import analyse_pages, restore_streams, PageStructureError
from pdf_craft.extractor.chapter.page_repair import AllPageLlmRepairProcessor, _compile_page_connections
from pdf_craft.extractor.recovery import affected_pages
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.pdf import decode, encode as encode_page
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.transform import PDFExtractionEngine
from tests.test_page_repair import _response_from_message

FIXTURE = Path(__file__).parent / 'assets/analysis/anchored_cross_page'


def source_pages():
    return list(XMLReader('page', FIXTURE, decode).read())


def project(pages):
    paragraphs, notes = _resolve_pages(pages)
    return analyse_pages([page.index for page in pages], paragraphs, notes)


class AnchoredPageTests(IsolatedAsyncioTestCase):
    async def test_real_shape_survives_identity_refinement_and_codec(self):
        pages = source_pages()
        analysed = project(pages)
        async def request(messages, _index, _maximum):
            return json.dumps(_response_from_message(messages[1]))
        repaired = await AllPageLlmRepairProcessor(request)(pages, analysed, {})
        chapters = list(_generate_chapters(Path('/unused'), TocInfo([], []), repaired))
        reopened = decode_chapter(encode(chapters[0]))
        paragraph = reopened.flow_items[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual([type(c) for c in paragraph.children],
                         [SourceTextFragment, SourceAsset, SourceTextFragment])
        self.assertEqual([c.page_index for c in paragraph.children], [1, 2, 2])
        self.assertEqual([c.bbox for c in paragraph.children],
                         [(10, 150, 190, 195), (20, 10, 180, 70), (10, 90, 190, 130)])
        self.assertEqual([c.source_order for c in paragraph.children if isinstance(c, SourceTextFragment)], [0, 1])
        self.assertEqual(repaired, analysed)

    async def test_failed_refinement_uses_valid_original_without_recompiling(self):
        pages = source_pages()
        analysed = project(pages)
        original = deepcopy(analysed)
        async def request(*args):
            return '{}'
        result = await AllPageLlmRepairProcessor(
            request, retry_policy=RetryPolicy(analysis=0), error_policy=True)(pages, analysed, {})
        self.assertEqual(analysed, original)
        self.assertEqual(result, original)
        paragraph = restore_streams(result).paragraphs[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual(len(paragraph.children), 3)

    async def test_anchors_are_transparent_but_formula_is_a_boundary(self):
        for asset_types in [('image',), ('table',), ('image', 'table', 'image')]:
            for cross_page in (False, True):
                with self.subTest(asset_types=asset_types, cross_page=cross_page):
                    pages = source_pages()
                    first, last = pages[0].body_layouts[0], pages[1].body_layouts[-1]
                    assets = [deepcopy(pages[1].body_layouts[0]) for _ in asset_types]
                    for order, (asset, kind) in enumerate(zip(assets, asset_types)):
                        asset.ref = kind
                        asset.det = (20, 20 + order * 10, 180, 25 + order * 10)
                    if cross_page:
                        pages[1].body_layouts = [*assets, last]
                    else:
                        first.det = (10, 1, 190, 15)
                        pages[0].body_layouts = [first, *assets, last]
                        pages = pages[:1]
                    for page in pages:
                        for order, layout in enumerate(page.body_layouts):
                            layout.order = order
                    analysed = project(pages)
                    _compile_page_connections(analysed)
                    result = restore_streams(analysed)
                    self.assertEqual(len(result.paragraphs), 1)
                    paragraph = result.paragraphs[0]
                    assert isinstance(paragraph, TextFlowItem)
                    self.assertEqual([c.ref for c in paragraph.children if isinstance(c, SourceAsset)], list(asset_types))
        pages = source_pages()
        pages[1].body_layouts[0].ref = 'equation'
        result = restore_streams(project(pages))
        self.assertEqual(len(result.paragraphs), 3)
        assert isinstance(result.paragraphs[1], SourceAsset)
        self.assertEqual(result.paragraphs[1].ref, 'formula')

    async def test_footnote_uses_same_anchor_rules(self):
        pages = source_pages()
        for page in pages:
            page.footnotes_layouts, page.body_layouts = page.body_layouts, []
        pages[0].footnotes_layouts[0].text = '① ' + pages[0].footnotes_layouts[0].text
        analysed = project(pages)
        _compile_page_connections(analysed)
        notes = restore_streams(analysed).citations
        self.assertEqual(len(notes), 1)
        paragraph = notes[0].flow_items[0]
        assert isinstance(paragraph, TextFlowItem)
        self.assertEqual([type(c) for c in paragraph.children],
                         [SourceTextFragment, SourceAsset, SourceTextFragment])

    async def test_explicit_refinement_break_is_not_rejoined_by_finalization(self):
        analysed = project(source_pages())
        texts = [layout for page in analysed for layout in page.layouts if layout.source_order is not None]
        texts[0].annotations.continues_to_next = False
        texts[1].annotations.continues_from_previous = False
        chapters = list(_generate_chapters(Path('/unused'), TocInfo([], []), analysed))
        self.assertEqual([type(item) for item in chapters[0].flow_items],
                         [TextFlowItem, StandaloneAsset, TextFlowItem])

    async def test_page_scope_closes_over_cross_page_paragraph_and_reference(self):
        analysed = project(source_pages())
        self.assertEqual(affected_pages({2}, analysed), {1, 2})


class RecoveryOCR:
    def metadata(self, *_args):
        return None

    is_vendor = False
    last_page_pixel_sizes = {1: (200, 200), 2: (200, 200), 3: (200, 200)}

    def page_geometry(self, index, dpi):
        return PageGeometry(index, 200, 200, dpi)

    def recognize(self, **kwargs):
        folder = kwargs['ocr_path']
        folder.mkdir(parents=True, exist_ok=True)
        for index in (1, 2, 3):
            yield OCREvent(OCREventKind.START, index, 3)
            page = decode(fromstring(f'<page index="{index}"><body><layout ref="text" det="1,1,190,40">Page {index}.</layout></body></page>'))
            save_xml(encode_page(page), folder / f'page_{index}.xml')
            yield OCREvent(OCREventKind.COMPLETE, index, 3)

    def recover_page_image(self, pdf_path, asset_path, index, dpi):
        image = Image.new('RGB', (200, 200), 'white')
        try:
            asset_hash = AssetHub(asset_path).clip(image, (0, 0, 200, 200))
        finally:
            image.close()
        return decode(fromstring(f'<page index="{index}"><body><layout ref="image" det="0,0,200,200" hash="{asset_hash}"/></body></page>'))

    def _create_fallback_page(self, hub, index, image):
        return decode(fromstring(f'<page index="{index}"><body><layout ref="text" det="0,0,100,100">[[Page {index} extraction failed due to PDF rendering error]]</layout></body></page>'))


class ExtractionRecoveryTests(IsolatedAsyncioTestCase):
    async def extract(self, root, *, error_policy: ErrorPolicyInput=True, failure=None, callback=None, stage="finish"):
        engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
        engine.__dict__["_ocr"] = RecoveryOCR()
        error = failure or PageStructureError('damaged page graph', 2)
        options = ExtractionOptions(includes_furniture=False, on_body=callback)
        if stage == "prepare":
            from dataclasses import replace
            from pdf_craft import JEV, LLM, FootnoteOptions, FootnoteRefinement, ConcurrentExecutor, FixedCapacity
            options = replace(options, footnotes=FootnoteOptions(FootnoteRefinement(
                JEV(key='unused'), LLM(key='unused', model='unused', url='https://unused.invalid', token_encoding='o200k_base'),
                executor=ConcurrentExecutor(FixedCapacity(1)))))
        target = 'pdf_craft.extractor.refinement._project_window' if stage == "prepare" else 'pdf_craft.transform.generate_chapter_files'
        with patch(target) as mocked:
            mocked.side_effect = error
            return await AsyncPDFCraft.from_engine(engine).extract_pdf(
                root / 'source.pdf', root / 'book.pcex',
                options,
                analysing_path=root / 'analysis',
                error_policy=error_policy,
            )

    async def test_unenhanced_finalization_falls_back_locally_and_keeps_cache(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seen, previews = [], []
            result = await self.extract(root, error_policy=lambda ctx: seen.append(ctx) or True,
                                        callback=previews.append)
            reopened = await AsyncPDFCraft().open_extraction(root / 'book.pcex')
            self.assertIsNotNone(reopened)
            self.assertIsNotNone(result)
            from zipfile import ZipFile
            with ZipFile(root / 'book.pcex') as archive:
                chapter = decode_chapter(fromstring(archive.read('chapters/chapter_head.xml')))
            self.assertEqual([type(item) for item in chapter.flow_items], [TextFlowItem, StandaloneAsset, TextFlowItem])
            self.assertEqual(len(previews), 3)  # No second/replacement notification.
            self.assertEqual(len(seen), 1)
            self.assertEqual((seen[0].category.value, seen[0].fallback), ('analysis', 'page_image'))
            self.assertEqual(seen[0].targets[0].page_indexes, (2,))
            self.assertEqual(seen[0].affected[0].page_indexes, (2,))
            cached = parse(root / 'analysis/ocr/page_2.xml').getroot().find('./body/layout')
            assert cached is not None
            self.assertEqual(cached.get('ref'), 'text')
            # A replay is still diagnosed from the original source cache.
            (root / "book.pcex").unlink()
            await self.extract(root)

    async def test_rejected_downgrade_and_callback_storage_errors_propagate(self):
        for error, policy in [(PageStructureError('bad', 2), False),
                              (OSError('disk full'), True)]:
            with TemporaryDirectory() as directory:
                with self.assertRaises(type(error)):
                    await self.extract(Path(directory), error_policy=policy, failure=error)
        with TemporaryDirectory() as directory:
            def callback(node):
                raise ValueError('user callback')
            decisions = []
            with self.assertRaisesRegex(ValueError, 'user callback'):
                await self.extract(Path(directory), error_policy=lambda ctx: decisions.append(ctx) or True, callback=callback)
            # Primary delivery aborts before finalization/recovery is attempted.
            self.assertEqual(decisions, [])

    async def test_all_late_downgrades_fail_instead_of_claiming_ocr_success(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(NoUsableOCRPagesError):
                await self.extract(Path(directory), failure=ValueError('unlocalizable broken graph'))

    async def test_render_failure_has_separate_decision_and_preserves_known_geometry(self):
        with TemporaryDirectory() as directory:
            seen = []
            with patch.object(RecoveryOCR, 'recover_page_image', side_effect=PDFError('render failed', 2)):
                await self.extract(Path(directory), error_policy=lambda ctx: seen.append(ctx) or True)
            self.assertEqual([ctx.fallback for ctx in seen], ['page_image', 'page_placeholder'])
            self.assertEqual(seen[1].targets[0].page.width, 200)

    async def test_live_producer_failure_still_collects_later_ocr_pages(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('pdf_craft.extractor.chapter.live.resolve_to_disk',
                       side_effect=PageStructureError('live analysis failed', 2)):
                await self.extract(root)
            self.assertTrue((root / 'analysis/ocr/page_3.xml').exists())
            self.assertTrue((root / 'book.pcex').exists())

    async def test_error_policy_cancellation_and_refused_image_render_propagate(self):
        from pdf_craft import ErrorPolicy
        import asyncio
        with TemporaryDirectory() as directory:
            async def cancelled(context):
                raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await self.extract(Path(directory), error_policy=cancelled)
        with TemporaryDirectory() as directory:
            with patch.object(RecoveryOCR, 'recover_page_image', side_effect=PDFError('render failed', 2)):
                with self.assertRaises(PDFError):
                    await self.extract(Path(directory), error_policy=ErrorPolicy(ignore_analysis_errors=True))

    async def test_final_storage_failure_is_not_another_recovery_attempt(self):
        with TemporaryDirectory() as directory:
            decisions = []
            def save(source, destination):
                if Path(destination).parent == Path(directory) / 'analysis/extraction/chapters':
                    raise OSError('cannot publish recovered chapters')
                return save_xml(source, destination)
            with patch('pdf_craft.extractor.commit.save_xml', side_effect=save):
                with self.assertRaisesRegex(OSError, 'cannot publish'):
                    await self.extract(Path(directory), error_policy=lambda ctx: decisions.append(ctx) or True)
            self.assertEqual(len(decisions), 1)

    async def test_cross_page_recovery_closes_graph_and_ocr_failures_count_at_eof(self):
        from types import SimpleNamespace
        from pdf_craft._policy import policy_operation
        from pdf_craft.document import ExtractionPaths
        from pdf_craft.extractor.recovery import recover_chapters
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pages_path = root / 'ocr'
            pages_path.mkdir()
            pages = source_pages()
            pages.append(decode(fromstring('<page index="3"><body><layout ref="text" det="1,1,190,40">Unaffected page.</layout></body></page>')))
            for page in pages:
                save_xml(encode_page(page), pages_path / f'page_{page.index}.xml')
            analyses = project(pages)
            draft = SimpleNamespace(pdf_path=root / 'source.pdf', pages_path=pages_path,
                                    extraction_paths=ExtractionPaths.at(root / 'extraction'),
                                    toc=TocInfo([], []), dpi=300)
            decisions = []
            @policy_operation
            async def recover_once(destination, *, error_policy):
                return await recover_chapters(draft, RecoveryOCR(), PageStructureError('invalid connection', 2),
                                              destination, analyses=analyses, aborted=lambda: False)
            output = await recover_once(root / 'recovered', error_policy=lambda ctx: decisions.append(ctx) or True)
            from pdf_craft.document.content import chapter_paths
            items = [item for path in chapter_paths(output.chapters)
                     for item in decode_chapter(parse(path).getroot()).flow_items]
            self.assertEqual([type(item) for item in items], [StandaloneAsset, StandaloneAsset, TextFlowItem])
            self.assertEqual({value.page_indexes[0] for value in decisions[0].affected}, {1, 2})
            self.assertEqual(decisions[0].targets[0].page_indexes, (2,))
            (pages_path / 'page_3.failed').touch()
            with self.assertRaises(NoUsableOCRPagesError):
                await recover_once(root / 'recovered-again', error_policy=True)

    async def test_first_translation_item_needs_no_furniture_pretranslation_barrier(self):
        from pdf_craft.document.content import ContentItem
        from pdf_craft.transformer.content import translate_content
        class Transformer:
            async def transform(self, chapter, **kwargs):
                return chapter
        async def items():
            yield ContentItem('chapter_head.xml', None, -1, 0,
                              TextFlowItem('body', -1, [SourceTextFragment(1, 0, (1, 1, 100, 20), ['Body.'])]))
        results = [item async for item in translate_content(items(), Transformer(), 2, [], [])]
        self.assertEqual(len(results), 1)

    async def test_prepare_analysis_failure_uses_the_same_public_page_recovery(self):
        with TemporaryDirectory() as directory:
            decisions = []
            await self.extract(Path(directory), stage='prepare', error_policy=lambda ctx: decisions.append(ctx) or True)
            self.assertEqual(decisions[0].fallback, 'page_image')
            self.assertEqual(decisions[0].affected[0].page_indexes, (2,))

    async def test_pdf_image_recovery_uses_real_dimensions_and_does_not_hide_disk_errors(self):
        from typing import cast
        from pdf_craft.pdf.handler import PDFHandler
        from pdf_craft.pdf.ocr import OCR
        from pdf_craft import DeepSeekOCRVendorConfig
        class Document:
            def render_page(self, index, dpi):
                return Image.new('RGB', (320, 480), 'red')
            def close(self):
                pass
        class Handler:
            def open(self, path):
                return Document()
        ocr = OCR(DeepSeekOCRVendorConfig(base_url='https://unused.invalid', api_key='unused', model='unused'), cast(PDFHandler, Handler()))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = ocr.recover_page_image(root / 'source.pdf', root / 'assets', 2, 200)
            self.assertEqual(page.body_layouts[0].det, (0, 0, 320, 480))
            self.assertEqual(ocr.last_page_pixel_sizes[2], (320, 480))
            with Image.open(root / 'assets' / f'{page.body_layouts[0].hash}.png') as image:
                self.assertEqual(image.getpixel((10, 10)), (255, 0, 0))
            ocr._last_page_pixel_sizes[2] = (160, 240)
            resized = ocr.recover_page_image(root / 'source.pdf', root / 'assets', 2, 200)
            self.assertEqual(resized.body_layouts[0].det, (0, 0, 160, 240))
            self.assertEqual(ocr.last_page_pixel_sizes[2], (160, 240))
            with patch.object(AssetHub, 'clip', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    ocr.recover_page_image(root / 'source.pdf', root / 'assets', 2, 200)

    async def test_raw_dependency_probe_includes_anchors_without_requiring_analysis(self):
        from pdf_craft.extractor.recovery import source_dependencies
        self.assertEqual(source_dependencies(FIXTURE, [1, 2], {2}), {1, 2})

    async def test_scope_probe_failure_uses_finite_conservative_degradation(self):
        with TemporaryDirectory() as directory:
            decisions = []
            with patch('pdf_craft.extractor.recovery.source_dependencies', side_effect=ValueError('unusable scope graph')):
                with self.assertRaises(NoUsableOCRPagesError):
                    await self.extract(Path(directory), error_policy=lambda ctx: decisions.append(ctx) or True)
            self.assertEqual(len(decisions), 1)
            self.assertEqual({value.page_indexes[0] for value in decisions[0].affected}, {1, 2, 3})

    async def test_review_context_skips_anchor_assets_when_finding_text_predecessors(self):
        from pdf_craft.extractor.chapter.page_repair import _page_packet
        from pdf_craft.extractor.chapter.page_review import _boundary_context
        pages = source_pages()
        pages[0].body_layouts[0].det = (10, 1, 190, 15)
        pages[0].body_layouts.extend(pages[1].body_layouts)
        for order, layout in enumerate(pages[0].body_layouts):
            layout.order = order
        analysed = project(pages[:1])[0]
        packet = _page_packet(analysed)
        self.assertIsNone(packet['layouts'][1]['initial_stream_boundary_from_previous'])
        self.assertIsNotNone(packet['layouts'][2]['initial_stream_boundary_from_previous'])
        packet.update(ocr_body_layout_count=3, ocr_footnote_layout_count=0)
        boundary = _boundary_context(packet, 'next')
        assert boundary is not None
        self.assertEqual([layout['source_type'] for layout in boundary['paragraph_boundary']], ['text', 'text'])
