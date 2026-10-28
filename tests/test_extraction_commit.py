"""Final semantic adoption, not intermediate OCR, owns public dependencies."""
# pylint: disable=protected-access
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
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


class AdoptionScaleTests(IsolatedAsyncioTestCase):
    def test_adoption_partition_releases_chunks_before_source_eof(self):
        from pdf_craft.extractor.chapter.generation import _partition_chapters
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment
        from pdf_craft.extractor.toc import TocInfo
        consumed = 0
        def source():
            nonlocal consumed
            for index in range(10000):
                consumed += 1
                yield TextFlowItem('heading', 0, [SourceTextFragment(index+1, 0, (0,0,100,20), ['Title'])])
        chunks = _partition_chapters(source(), TocInfo([], []), assembled=True, chunk_size=128)
        first = next(chunks)
        self.assertLessEqual(consumed, 129)
        self.assertEqual(len(first.flow_items), 128)
        self.assertIsNone(first.id)
        self.assertEqual(sum(len(chunk.flow_items) for chunk in chunks) + 128, 10000)

    def test_projection_indexes_flow_only_once_per_chunk(self):
        from xml.etree.ElementTree import Element, SubElement
        from pdf_craft.extractor.commit import project_adopted
        class CountedRoot(Element):
            scans = 0
            def findall(self, path, namespaces=None):
                self.scans += 1
                return super().findall(path, namespaces)
        root = CountedRoot('chapter')
        flow = SubElement(root, 'flow')
        for index in range(4000):
            text = SubElement(flow, 'text', {'role': 'body'})
            SubElement(text, 'fragment', {'page_index':'1', 'source_order':str(index), 'bbox':'0,0,100,20'}).text = 'Body'
        with TemporaryDirectory() as folder:
            path = Path(folder)
            (path/'chapter_head.xml').touch()
            with patch('pdf_craft.extractor.commit.read_xml', return_value=root):
                project_adopted(path, path/'absent', path/'projection', [])
        self.assertLessEqual(root.scans, 1, 'whole flow must not be rescanned per text')

    def test_disk_adoption_and_final_partition_preserve_order_and_references(self):
        import sqlite3
        import pickle
        from contextlib import closing
        from pdf_craft.extractor.chapter.generation import generate_chapter_files
        from pdf_craft.extractor.chapter.chapter import TextFlowItem, SourceTextFragment, Reference, decode
        from pdf_craft.extractor.toc import TocInfo, Toc
        from pdf_craft.extractor.commit import partition_adopted
        from pdf_craft.common import read_xml
        from pdf_craft.document.content import chapter_paths
        with TemporaryDirectory() as folder:
            root = Path(folder)
            database = root/'content.sqlite'
            with closing(sqlite3.connect(database)) as db:
                db.executescript('CREATE TABLE bodies(page,position,value); CREATE TABLE refs(page,number,value,used);')
                for index in range(1025):
                    ref = Reference(index+1, 1, '*', [TextFlowItem('body', -1, [SourceTextFragment(index+1, 1, (0,30,100,40), ['Note'])])])
                    item = TextFlowItem('heading', 0, [SourceTextFragment(index+1, 0, (0,0,100,20), [str(index),ref])])
                    db.execute('INSERT INTO bodies VALUES (?,?,?)', (index+1,0,pickle.dumps(item)))
                    db.execute('INSERT INTO refs VALUES (?,?,?,0)', (index+1,1,pickle.dumps(ref)))
                db.commit()
            generate_chapter_files(root/'unused', root/'chunks', TocInfo([], []), resolved_database=database, chunk_size=128)
            chunks = list(chapter_paths(root/'chunks'))
            self.assertEqual(len(chunks), 9)
            for path in chunks:
                chapter = decode(read_xml(path))
                self.assertIsNone(chapter.id)
                self.assertLessEqual(len(chapter.flow_items), 128)
            toc = TocInfo([Toc(index+1,index+1,0,0,[]) for index in range(1025)], [])
            partition_adopted(root/'chunks',root/'final',toc)
            for index,path in enumerate(chapter_paths(root/'final')):
                chapter = decode(read_xml(path))
                self.assertEqual(chapter.id,index+1)
                self.assertEqual(len(chapter.flow_items),1)
                self.assertEqual(chapter.definitions[0].id,(index+1,1))
