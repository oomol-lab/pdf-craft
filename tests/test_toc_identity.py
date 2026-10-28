"""Deterministic TOC/chapter identity gates; no provider or model requests."""
# pylint: disable=protected-access,unused-argument,super-init-not-called
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft import (
    AsyncPDFCraft, ConcurrentExecutor, ExtractionOptions, FixedCapacity,
    FootnoteOptions, FootnoteRefinement, JEV, LLM, ErrorPolicyInput,
)
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import decode as decode_chapter, SourceTextFragment, TextFlowItem
from pdf_craft.extractor.chapter.page_repair import AllPageLlmRepairProcessor
from pdf_craft.extractor.toc import TocInfo, Toc, iter_toc
from pdf_craft.extractor.toc.analysing import analyse_toc
from pdf_craft.pdf import decode, encode
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.transform import PDFExtractionEngine
from tests.test_anchored_page_recovery import RecoveryOCR
from tests.test_page_repair import _response_from_message


class IdentityOCR(RecoveryOCR):
    extra_title_page = 0
    last_page_pixel_sizes = {index: (200, 200) for index in range(1, 7)}

    def recognize(self, **kwargs):
        folder = kwargs['ocr_path']
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'page_pixel_sizes.json').write_text(json.dumps(self.last_page_pixel_sizes))
        for index in range(1, 7):
            yield OCREvent(OCREventKind.START, index, 6)
            titles = ('<layout ref="title" det="1,1,190,20">First chapter</layout>'
                      '<layout ref="title" det="1,21,190,40">Second chapter</layout>') if index == 1 else ''
            if index == self.extra_title_page:
                titles = '<layout ref="title" det="1,1,190,20">Third chapter</layout>'
            page = decode(fromstring(f'<page index="{index}"><body>{titles}'
                                    f'<layout ref="text" det="1,60,190,90">Page {index} body.</layout>'
                                    '</body><footnotes><layout ref="text" det="1,150,190,180">① Note.</layout>'
                                    '</footnotes></page>'))
            if not kwargs['includes_footnotes']:
                page.footnotes_layouts = []
            save_xml(encode(page), folder / f'page_{index}.xml')
            yield OCREvent(OCREventKind.COMPLETE, index, 6)


class RefinedEngine(PDFExtractionEngine):
    def __init__(self, mode):
        self._ocr = IdentityOCR()
        self.mode = mode

    async def _refine_pages(self, chapter_analysis, footnote_refinement, draft):
        async def request(messages, _index, _maximum):
            result = _response_from_message(messages[1])
            if result['page_index'] == 1:
                first, second = result['layouts'][:2]
                if self.mode == 'body':
                    second['paragraph_role'] = 'body'
                elif self.mode == 'ignored':
                    second.update(ownership='ignored', paragraph_role=None, paragraph_level=None)
                elif self.mode == 'merged':
                    first['continues_to_next'] = True
                    second['continues_from_previous'] = True
            return json.dumps(result)
        return await AllPageLlmRepairProcessor(request)(
            chapter_analysis.source_pages, chapter_analysis.pages, chapter_analysis.page_pixel_sizes)


def refinement():
    return FootnoteOptions(FootnoteRefinement(
        JEV('unused'), LLM('unused', 'https://example.invalid', 'unused', 'o200k_base'),
        executor=ConcurrentExecutor(FixedCapacity(1))))


class TocIdentityTests(IsolatedAsyncioTestCase):
    async def extract(self, root, mode='identity', *, policy: ErrorPolicyInput=True, footnotes=True):
        engine = RefinedEngine(mode)
        return await AsyncPDFCraft.from_engine(engine).extract_pdf(
            root / 'source.pdf', root / 'book.pcex',
            ExtractionOptions(includes_furniture=False, footnotes=(FootnoteOptions() if mode == 'traditional'
                                                                        else refinement()) if footnotes else None),
            analysing_path=root / 'analysis', error_policy=policy,
        )

    def inspect(self, root):
        with ZipFile(root / 'book.pcex') as archive:
            toc = fromstring(archive.read('toc.xml'))
            chapters = [decode_chapter(fromstring(archive.read(name))) for name in archive.namelist()
                        if name.startswith('chapters/') and name.endswith('.xml')]
        ids = {int(item.attrib['id']) for item in toc.iter('item')}
        self.assertEqual({chapter.id for chapter in chapters if chapter.id is not None}, ids)
        return chapters, ids

    async def test_refined_title_role_change_does_not_destroy_six_successful_ocr_pages(self):
        # Before the fix: final validation -> whole-book image fallback -> NoUsableOCRPagesError.
        for mode in ('body', 'ignored'):
            with self.subTest(mode=mode), TemporaryDirectory() as folder:
                root = Path(folder)
                decisions = []
                with patch.object(IdentityOCR, 'recover_page_image', side_effect=AssertionError('unnecessary page loss')):
                    await self.extract(root, mode, policy=lambda event, decisions=decisions: decisions.append(event) or True)
                chapters, ids = self.inspect(root)
                self.assertEqual(ids, {1})
                self.assertFalse(decisions, 'accepted heading changes are not failures')
                for index in range(1, 7):
                    self.assertTrue(any(f'Page {index} body.' in str(item)
                                        for chapter in chapters for item in chapter.flow_items))
                self.assertEqual(len([path for path in (root / 'analysis/ocr').glob('page_*.xml')]), 6)

    async def test_merged_heading_gets_one_final_anchor_without_losing_sources(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(IdentityOCR, 'recover_page_image', side_effect=AssertionError('unnecessary page loss')):
                await self.extract(root, 'merged')
            chapters, ids = self.inspect(root)
            self.assertEqual(ids, {1})
            headings = [item for chapter in chapters for item in chapter.flow_items
                        if isinstance(item, TextFlowItem) and item.role == 'heading']
            self.assertEqual([fragment.source_order for item in headings for fragment in item.children
                              if isinstance(fragment, SourceTextFragment)], [0, 1])

    async def test_sparse_pages_keep_real_identity_in_detected_directory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pages = root / 'pages'
            pages.mkdir()
            # First of 20 selected pages is printed TOC; page numbers are deliberately sparse.
            for position in range(20):
                index = 10 + position * 3
                text = 'Chapter Alpha. Chapter Bravo. Chapter Charlie.' if position == 0 else 'Plain body.'
                ref = 'text'
                if position in (4, 8, 12):
                    text = ['Chapter Alpha', 'Chapter Bravo', 'Chapter Charlie'][(position // 4) - 1]
                    ref = 'title'
                page = decode(fromstring(f'<page index="{index}"><body><layout ref="{ref}" '
                                         f'det="1,1,190,25">{text}</layout></body></page>'))
                save_xml(encode(page), pages / f'page_{index}.xml')
            result = await analyse_toc(pages, root / 'toc.xml', True)
            self.assertEqual(result.page_indexes, [10])
            self.assertEqual({item.page_index for item in iter_toc(result.content)}, {22, 34, 46})

    async def test_partial_page_recovery_keeps_surviving_toc_and_text(self):
        from pdf_craft.extractor.chapter.page_analysis import PageStructureError
        import pdf_craft.transform as transform
        original = transform.generate_chapter_files
        failed = False
        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise PageStructureError('damaged first page', 1)
            return original(*args, **kwargs)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('pdf_craft.transform.generate_chapter_files', side_effect=fail_once):
                await self.extract(root, footnotes=False)
            chapters, ids = self.inspect(root)
            self.assertEqual(ids, set())
            self.assertTrue(any('Page 6 body.' in str(item) for chapter in chapters for item in chapter.flow_items))

    async def test_identity_and_traditional_paths_preserve_page_local_citation_keys(self):
        for mode, footnotes in (('identity', True), ('identity', False), ('traditional', True)):
            with self.subTest(mode=mode, footnotes=footnotes), TemporaryDirectory() as folder:
                root = Path(folder)
                await self.extract(root, mode, footnotes=footnotes)
                chapters, ids = self.inspect(root)
                self.assertEqual(ids, {1, 2})
                notes = [note for chapter in chapters for note in chapter.definitions]
                if footnotes:
                    self.assertEqual({note.id for note in notes}, {(page, 1) for page in range(1, 7)})

    async def test_final_validation_reports_missing_ids_and_pages(self):
        from pdf_craft.document.package import PDFCraftExtraction
        with TemporaryDirectory() as folder:
            root = Path(folder)
            await self.extract(root)
            workspace = root / 'analysis/extraction'
            (workspace / 'chapters/chapter_2.xml').unlink()
            with self.assertRaises(ValueError) as raised:
                PDFCraftExtraction._from_workspace(workspace)._validate()
            self.assertEqual(getattr(raised.exception, 'missing_ids', None), (2,))
            self.assertEqual(getattr(raised.exception, 'page_indexes', None), (1,))

    async def test_simulated_incident_adopts_refinement_without_page_loss(self):
        # Image fallback is usable, but a heading change must never need it.
        with TemporaryDirectory() as folder:
            root = Path(folder)
            await self.extract(root, 'body')
            chapters, ids = self.inspect(root)
            self.assertEqual(ids, {1})
            self.assertEqual(sum('Page ' in str(item) for chapter in chapters for item in chapter.flow_items), 6)

    async def test_strict_policy_accepts_consistent_refined_headings(self):
        for policy in (False, lambda _event: False):
            with self.subTest(policy=policy), TemporaryDirectory() as folder:
                root = Path(folder)
                with patch.object(IdentityOCR, 'recover_page_image', side_effect=AssertionError('page loss')):
                    await self.extract(root, 'body', policy=policy)
                self.assertEqual(self.inspect(root)[1], {1})

    async def test_stale_directory_recomputed_from_adopted_headings(self):
        from pdf_craft.extractor.toc import encode as encode_toc
        with TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'analysis/extraction/toc.xml'
            path.parent.mkdir(parents=True)
            # A cached stale anchor not represented by the accepted OCR source.
            save_xml(encode_toc(TocInfo([Toc(77, 1, 99, 0, [])], [])), path)
            decisions = []
            with patch.object(IdentityOCR, 'recover_page_image', side_effect=AssertionError('page loss')):
                await self.extract(root, footnotes=False, policy=lambda event: decisions.append(event) or True)
            chapters, ids = self.inspect(root)
            self.assertEqual(ids, {1, 2})
            self.assertFalse(decisions)
            self.assertEqual(sum('Page ' in str(item) for chapter in chapters for item in chapter.flow_items), 6)

    async def test_partial_recovery_assigns_ids_only_to_surviving_headings(self):
        from pdf_craft.extractor.chapter.page_analysis import PageStructureError
        import pdf_craft.transform as transform
        original = transform.generate_chapter_files
        failed = False
        def fail_once(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                raise PageStructureError('first-page structure', 1)
            return original(*args, **kwargs)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(IdentityOCR, 'extra_title_page', 3), patch(
                'pdf_craft.transform.generate_chapter_files', side_effect=fail_once,
            ):
                await self.extract(root, footnotes=False)
            chapters, ids = self.inspect(root)
            self.assertEqual(ids, {1})
            self.assertTrue(any('Page 6 body.' in str(item) for chapter in chapters for item in chapter.flow_items))

    def test_toc_heading_split_keeps_assets_references_and_cross_page_source_identity(self):
        from pdf_craft.extractor.chapter.chapter import SourceAsset, Reference, Chapter, encode as encode_chapter
        from pdf_craft.extractor.chapter.generation import _partition_chapters
        reference = Reference(8, 1, '①', [TextFlowItem('body', -1, [
            SourceTextFragment(8, 5, (1, 150, 190, 180), ['Note.'])])])
        first = SourceTextFragment(7, 0, (1, 1, 190, 20), ['First', reference])
        second = SourceTextFragment(8, 0, (1, 1, 190, 20), ['Second'])
        asset = SourceAsset(7, 'table', (1, 30, 190, 50))
        toc = TocInfo([Toc(41, 7, 0, 0, []), Toc(92, 8, 0, 0, [])], [])
        parts = list(_partition_chapters([TextFlowItem('heading', 0, [first, asset, second])], toc, assembled=True))
        self.assertEqual([part.id for part in parts], [41, 92])
        first_title, second_title = parts[0].flow_items[0], parts[1].flow_items[0]
        assert isinstance(first_title, TextFlowItem) and isinstance(second_title, TextFlowItem)
        self.assertEqual(first_title.children, [first, asset])
        self.assertEqual(second_title.children, [second])
        reopened = [decode_chapter(encode_chapter(part)) for part in parts]
        paragraph = reopened[0].flow_items[0]
        assert isinstance(paragraph, TextFlowItem)
        fragment = paragraph.children[0]
        assert isinstance(fragment, SourceTextFragment)
        self.assertEqual([part.id for part in fragment.content if isinstance(part, Reference)], [(8, 1)])
        # A single chapter title spanning pages stays a single paragraph.
        one_anchor = TocInfo([toc.content[0]], [])
        parts = list(_partition_chapters([TextFlowItem('heading', 0, [first, asset, second])], one_anchor, assembled=True))
        self.assertEqual(len(parts), 1)
        title = parts[0].flow_items[0]
        assert isinstance(title, TextFlowItem)
        self.assertEqual(len(title.children), 3)
        self.assertIsInstance(parts[0], Chapter)

    async def test_printed_directory_pages_cannot_also_be_chapter_anchors(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            pages = root / 'pages'
            pages.mkdir()
            for position in range(20):
                index = position + 10
                text, ref = 'Ordinary body.', 'text'
                if position in (4, 8, 12):
                    text = ['Chapter Alpha', 'Chapter Bravo', 'Chapter Charlie'][position // 4 - 1]
                    ref = 'title'
                title = ''
                if position < 2:
                    title = '<layout ref="title" det="1,1,190,20">Chapter Alpha</layout>'
                    text = 'Chapter Alpha. Chapter Bravo. Chapter Charlie. ' * 3
                page = decode(fromstring(f'<page index="{index}"><body>{title}'
                                         f'<layout ref="{ref}" det="1,30,190,80">{text}</layout>'
                                         '</body></page>'))
                save_xml(encode(page), pages / f'page_{index}.xml')
            toc = await analyse_toc(pages, root / 'toc.xml', True)
            self.assertEqual(toc.page_indexes, [10, 11])
            self.assertEqual({item.page_index for item in iter_toc(toc.content)}, {14, 18, 22})

    async def test_unexpected_chapter_diagnostics_keep_ids_and_source_page(self):
        from pdf_craft.document.package import PDFCraftExtraction
        with TemporaryDirectory() as folder:
            root = Path(folder)
            await self.extract(root)
            workspace = root / 'analysis/extraction'
            source = workspace / 'chapters/chapter_2.xml'
            chapter = fromstring(source.read_text())
            chapter.set('id', '999')
            source.unlink()
            save_xml(chapter, workspace / 'chapters/chapter_999.xml')
            with self.assertRaises(ValueError) as raised:
                PDFCraftExtraction._from_workspace(workspace)._validate()
            self.assertEqual(getattr(raised.exception, 'missing_ids', None), (2,))
            self.assertEqual(getattr(raised.exception, 'unexpected_ids', None), (999,))
            self.assertIn(1, getattr(raised.exception, 'page_indexes', ()))
