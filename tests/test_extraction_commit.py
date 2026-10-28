"""Final semantic adoption, not intermediate OCR, owns public dependencies."""
# pylint: disable=protected-access
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, tostring
from zipfile import ZipFile

from pdf_craft import AsyncPDFCraft, ExtractionOptions
from pdf_craft.common import save_xml
from pdf_craft.extractor.toc.analysing import analyse_toc
from pdf_craft.pdf.furniture import _read_ocr_boxes
from tests.test_toc_identity import RefinedEngine, refinement


class CommitBoundaryTests(IsolatedAsyncioTestCase):
    async def test_refined_removed_title_is_not_a_final_directory_anchor(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            await AsyncPDFCraft.from_engine(RefinedEngine('ignored')).extract_pdf(
                root/'source.pdf', root/'book.pcex',
                ExtractionOptions(includes_furniture=False, footnotes=refinement()),
                analysing_path=root/'analysis', error_policy=False)
            with ZipFile(root/'book.pcex') as archive:
                toc = fromstring(archive.read('toc.xml'))
                self.assertEqual(len(list(toc.iter('item'))), 1)
                text = ''.join(archive.read(n).decode() for n in archive.namelist() if n.startswith('chapters/'))
                self.assertNotIn('Second chapter', text)

    async def test_callbacks_only_deliver_adopted_chapter_content(self):
        received = []
        class Engine(RefinedEngine):
            async def _refine_pages(self, chapter_analysis, footnote_refinement, draft):
                if received:
                    raise AssertionError('candidate content escaped before adoption')
                return await super()._refine_pages(chapter_analysis, footnote_refinement, draft)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            await AsyncPDFCraft.from_engine(Engine('ignored')).extract_pdf(
                root/'source.pdf',root/'book.pcex',
                ExtractionOptions(includes_furniture=False,footnotes=refinement(),
                                  on_body=lambda node: received.append(tostring(node))),
                analysing_path=root/'analysis',error_policy=False)
            self.assertTrue(received)
            self.assertNotIn(b'Second chapter', b''.join(received))
            with ZipFile(root/'book.pcex') as archive:
                expected = [tostring(node) for name in sorted(archive.namelist()) if name.startswith('chapters/') and name.endswith('.xml')
                            for node in fromstring(archive.read(name)).findall('./flow/*')]
            self.assertEqual([value.rstrip() for value in received], [value.rstrip() for value in expected])

    async def test_furniture_coverage_uses_adopted_not_ignored_layouts(self):
        observed = []
        def furniture(_pdf, pages, destination, **_kwargs):
            observed.extend(_read_ocr_boxes(pages/'page_1.xml'))
            save_xml(fromstring('<furnitures><patterns/><pages/></furnitures>'),destination)
        with TemporaryDirectory() as directory:
            root=Path(directory)
            with patch('pdf_craft.transform.write_furnitures',furniture):
                await AsyncPDFCraft.from_engine(RefinedEngine('ignored')).extract_pdf(
                    root/'source.pdf',root/'book.pcex',
                    ExtractionOptions(footnotes=refinement()),analysing_path=root/'analysis',error_policy=True)
            self.assertNotIn((1,21,190,40), observed)

    async def test_unversioned_toc_cache_is_not_authoritative(self):
        with TemporaryDirectory() as directory:
            root=Path(directory); pages=root/'ocr';pages.mkdir()
            save_xml(fromstring('<page index="1"><body><layout ref="title" det="0,0,100,20">New title</layout></body></page>'),pages/'page_1.xml')
            toc=root/'toc.xml'
            save_xml(fromstring('<toc page_indexes=""><item id="9" page_index="99" order="0" level="0"/></toc>'),toc)
            result=await analyse_toc(pages,toc,False)
            self.assertEqual([(t.page_index,t.order) for t in result.content],[(1,0)])

    async def test_projection_uses_final_heading_source_and_fallback_coverage(self):
        from pdf_craft.extractor.commit import project_adopted
        with TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, chapters = root/'ocr', root/'chapters'
            evidence.mkdir(); chapters.mkdir()
            save_xml(fromstring('<page index="1"><body><layout ref="text" det="0,0,100,20">Body</layout></body>'
                                '<footnotes><layout ref="text" det="0,25,100,40">Heading</layout></footnotes></page>'), evidence/'page_1.xml')
            save_xml(fromstring('<chapter><flow><text role="heading"><fragment page_index="1" source_order="1" bbox="0,25,100,40">Heading</fragment></text>'
                                '<standalone-asset><asset ref="image" page_index="1" bbox="0,0,100,100"/></standalone-asset></flow></chapter>'), chapters/'chapter_head.xml')
            project_adopted(chapters, evidence, root/'projection', [])
            toc = await analyse_toc(root/'projection', root/'toc.xml', False)
            self.assertEqual([(item.page_index,item.order) for item in toc.content], [(1,1)])
            self.assertIn((0,0,100,100), list(_read_ocr_boxes(root/'projection/page_1.xml')))
            self.assertNotIn((0,0,100,20), list(_read_ocr_boxes(root/'projection/page_1.xml')))

    async def test_final_footnotes_are_delivered_once_in_source_order(self):
        from pdf_craft.extractor.commit import notify_adopted
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index, ids in ((1,['2-1','1-2']), (2,['2-1','1-1'])):
                refs = ''.join(f'<ref id="{ident}"><mark>*</mark><flow/></ref>' for ident in ids)
                save_xml(fromstring(f'<chapter><flow/><references>{refs}</references></chapter>'), root/f'chapter_{index}.xml')
            seen = []
            await notify_adopted(root, None, lambda node: seen.append(node.get('id')))
            self.assertEqual(seen, ['1-1','1-2','2-1'])
