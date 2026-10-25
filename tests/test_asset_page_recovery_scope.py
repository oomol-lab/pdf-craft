"""Recovery is atomic across transparent image/table-only intermediate pages."""
# Each subtest awaits its producers before advancing the scenario loop.
# pylint: disable=protected-access,unused-argument,cell-var-from-loop
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, parse
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, ExtractionOptions
from pdf_craft._policy import policy_operation
from pdf_craft.common import save_xml
from pdf_craft.document import ExtractionPaths
from pdf_craft.extractor.chapter.chapter import SourceAsset, SourceTextFragment, StandaloneAsset, TextFlowItem, decode as decode_chapter
from pdf_craft.extractor.chapter.page_analysis import PageStructureError, restore_streams
from pdf_craft.extractor.recovery import affected_pages, source_dependencies, recover_chapters
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.pdf import decode, encode
from pdf_craft.pdf.ocr import OCREvent, OCREventKind
from pdf_craft.transform import PDFExtractionEngine
from tests.test_anchored_page_recovery import RecoveryOCR, project

SCENARIOS = [('image',), ('table',), ('image', 'table')]


def bridge_pages(kinds):
    layouts = [('text', 'The paragraph continues')]
    layouts.extend((kind, '') for kind in kinds)
    layouts.extend([('text', 'on the following page.'), ('text', 'An independent paragraph.')])
    return [decode(fromstring(
        f'<page index="{index}"><body><layout ref="{kind}" det="10,20,190,100" '
        f'hash="{"a" * 64}">{text}</layout></body><footnotes/></page>'
    )) for index, (kind, text) in enumerate(layouts, 1)]


def write_pages(path, pages):
    path.mkdir(parents=True)
    for page in pages:
        save_xml(encode(page), path / f'page_{page.index}.xml')


class AssetPageScopeTests(IsolatedAsyncioTestCase):
    def test_analysed_scope_includes_every_child_page_for_each_failure_target(self):
        for kinds in SCENARIOS:
            pages = bridge_pages(kinds)
            analyses = project(pages)
            paragraph = restore_streams(analyses).paragraphs[0]
            assert isinstance(paragraph, TextFlowItem)
            self.assertEqual([type(child) for child in paragraph.children],
                             [SourceTextFragment, *[SourceAsset for _ in kinds], SourceTextFragment])
            component = set(range(1, len(pages)))
            for target in component:
                with self.subTest(kinds=kinds, target=target):
                    self.assertEqual(affected_pages({target}, analyses), component)
            self.assertEqual(affected_pages({len(pages)}, analyses), {len(pages)})

    def test_raw_scope_keeps_predecessor_across_asset_only_pages(self):
        for kinds in SCENARIOS:
            with self.subTest(kinds=kinds), TemporaryDirectory() as directory:
                pages = bridge_pages(kinds)
                root = Path(directory) / 'ocr'
                write_pages(root, pages)
                component = set(range(1, len(pages)))
                for target in component:
                    with self.subTest(target=target):
                        self.assertEqual(source_dependencies(root, list(range(1, len(pages) + 1)), {target}), component)
                self.assertEqual(source_dependencies(root, list(range(1, len(pages) + 1)), {len(pages)}), {len(pages)})

    async def test_recovered_chapters_replace_whole_paragraph_with_or_without_graph(self):
        for kinds in SCENARIOS:
            for use_graph in (False, True):
                for target in range(1, len(kinds) + 3):
                    with self.subTest(kinds=kinds, graph=use_graph, target=target), TemporaryDirectory() as directory:
                        root = Path(directory)
                        pages = bridge_pages(kinds)
                        write_pages(root / 'ocr', pages)
                        draft = SimpleNamespace(pdf_path=root / 'source.pdf', pages_path=root / 'ocr',
                                                extraction_paths=ExtractionPaths.at(root / 'extraction'),
                                                toc=TocInfo([], []), dpi=300)
                        events = []
                        @policy_operation
                        async def run(*, error_policy):
                            return await recover_chapters(
                                draft, RecoveryOCR(), PageStructureError('invalid paragraph', target), root / 'recovered',
                                analyses=project(pages) if use_graph else (), aborted=lambda: False)
                        output = await run(error_policy=lambda event: events.append(event) or True)
                        chapter = decode_chapter(parse(output / 'chapter_head.xml').getroot())
                        self.assert_whole_recovery(chapter.flow_items, len(pages))
                        self.assertEqual([event.fallback for event in events], ['page_image'])
                        self.assertEqual(events[0].targets[0].page_indexes, (target,))
                        self.assertEqual({value.page_indexes[0] for value in events[0].affected}, set(range(1, len(pages))))

    async def test_unenhanced_public_extraction_preserves_atomic_recovery(self):
        for kinds in SCENARIOS:
            for target in range(1, len(kinds) + 3):
                with self.subTest(kinds=kinds, target=target), TemporaryDirectory() as directory:
                    pages = bridge_pages(kinds)
                    root = Path(directory)
                    class OCR(RecoveryOCR):
                        last_page_pixel_sizes = {page.index: (200, 200) for page in pages}
                        def recognize(self, **kwargs):
                            folder = kwargs['ocr_path']
                            folder.mkdir(parents=True)
                            for page in pages:
                                yield OCREvent(OCREventKind.START, page.index, len(pages))
                                save_xml(encode(page), folder / f'page_{page.index}.xml')
                                yield OCREvent(OCREventKind.COMPLETE, page.index, len(pages))
                    engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
                    engine.__dict__['_ocr'] = OCR()
                    events = []
                    with patch('pdf_craft.transform.generate_chapter_files', side_effect=PageStructureError('broken chapter', target)):
                        await AsyncPDFCraft.from_engine(engine).extract_pdf(
                            root / 'source.pdf', root / 'book.pcex', ExtractionOptions(includes_furniture=False),
                            analysing_path=root / 'analysis', error_policy=lambda event: events.append(event) or True)
                    with ZipFile(root / 'book.pcex') as archive:
                        chapter = decode_chapter(fromstring(archive.read('chapters/chapter_head.xml')))
                    self.assert_whole_recovery(chapter.flow_items, len(pages))
                    self.assertEqual({value.page_indexes[0] for value in events[0].affected}, set(range(1, len(pages))))

    def assert_whole_recovery(self, items, count):
        self.assertEqual([type(item) for item in items], [*[StandaloneAsset for _ in range(count - 1)], TextFlowItem])
        self.assertEqual([item.asset.page_index for item in items[:-1]], list(range(1, count)))
        self.assertEqual(items[-1].children[0].content, ['An independent paragraph.'])

    def test_display_formula_page_does_not_create_transparent_dependencies(self):
        pages = bridge_pages(('equation',))
        analyses = project(pages)
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'ocr'
            write_pages(root, pages)
            for target in (1, 2, 3, 4):
                with self.subTest(target=target):
                    self.assertEqual(affected_pages({target}, analyses), {target})
                    self.assertEqual(source_dependencies(root, [1, 2, 3, 4], {target}), {target})
