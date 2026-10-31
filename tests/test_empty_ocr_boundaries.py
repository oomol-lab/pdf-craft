"""Empty OCR text remains evidence without replacing a semantic predecessor."""
import unittest
import tempfile
from pathlib import Path
from xml.etree.ElementTree import fromstring

from pdf_craft.pdf import decode, encode
from pdf_craft.common import save_xml
from pdf_craft.extractor.toc import TocInfo
from pdf_craft.extractor.chapter.generation import prepare_chapter_analysis
from pdf_craft.extractor.refinement import _project_window
from pdf_craft.extractor.chapter.primary import PrimaryPages
from pdf_craft.extractor.chapter.page_analysis import restore_streams
from pdf_craft.extractor.chapter.chapter import TextFlowItem


def page(index, content):
    return decode(fromstring(f"<page index='{index}'>{content}</page>"))


class EmptyOCRBoundaryTests(unittest.TestCase):
    def test_trailing_empty_box_is_ignored_without_losing_body_continuation(self):
        left = page(1, """<body>
            <layout ref='text' det='10,10,90,60'>An unfinished</layout>
            <layout ref='text' det='10,70,90,80'>   </layout>
            </body>""")
        right = page(2, """<body>
            <layout ref='text' det='10,10,90,30'>sentence.</layout>
            </body>""")
        for enabled in (False, True):
            with self.subTest(footnotes=enabled):
                before = [layout.text for layout in left.body_layouts]
                result = _project_window([left, right, None], enabled)
                first, second = result[:2]
                assert first is not None and second is not None
                self.assertTrue(second.layouts[0].continues_from_previous)
                empty = next(item for item in first.layouts if item.source_order == 1)
                self.assertEqual(empty.ownership, 'ignored')
                self.assertEqual(empty.bbox, (10, 70, 90, 80))
                assert empty.source.origin is not None
                self.assertEqual(empty.source.origin.text, before[1])
                restored = restore_streams([first, second])
                self.assertEqual(len(restored.paragraphs), 1)
                paragraph = restored.paragraphs[0]
                assert isinstance(paragraph, TextFlowItem)
                self.assertEqual([fragment.page_index for fragment in paragraph.children], [1, 2])
                self.assertEqual([layout.text for layout in left.body_layouts], before)

    def test_leading_empty_box_does_not_hide_the_first_real_text(self):
        left = page(1, """<body><layout ref='text' det='10,10,90,30'>An unfinished</layout></body>""")
        right = page(2, """<body>
            <layout ref='text' det='10,1,90,5'/>
            <layout ref='text' det='10,10,90,30'>sentence.</layout>
            </body>""")
        target = _project_window([left, right, None], False)[1]
        assert target is not None
        real = next(item for item in target.layouts if item.source_order == 1)
        self.assertTrue(real.continues_from_previous)
        self.assertEqual(target.layouts[0].ownership, 'ignored')

    def test_blank_only_page_keeps_the_empty_page_boundary(self):
        pages = [
            page(1, "<body><layout ref='text' det='1,1,90,20'>An unfinished</layout></body>"),
            page(2, "<body><layout ref='text' det='1,1,90,20'>  </layout></body>"),
            page(3, "<body><layout ref='text' det='1,1,90,20'>A different paragraph.</layout></body>"),
        ]
        primary = PrimaryPages(False)
        results = [primary.append(source) for source in pages]
        self.assertFalse(results[2].layouts[0].continues_from_previous)
        self.assertEqual(results[1].layouts[0].ownership, 'ignored')

    def test_empty_note_region_is_retained_without_a_citation(self):
        source = page(1, """<body>
            <layout ref='text' det='1,1,90,20'>Body.</layout></body>
            <footnotes><layout ref='text' det='1,70,90,90'/></footnotes>""")
        for enabled in (False, True):
            with self.subTest(footnotes=enabled):
                target = _project_window([None, source, None], enabled)[1]
                assert target is not None
                empty = next(item for item in target.layouts if item.source_order == 1)
                self.assertEqual(empty.ownership, 'ignored')
                assert empty.source.origin is not None
                self.assertEqual(empty.source.origin.stream, 'footnote')
                self.assertEqual(empty.bbox, (1, 70, 90, 90))
                self.assertFalse(target.citations)

    def test_traditional_chapter_analysis_retains_empty_source_evidence(self):
        source = page(1, """<body>
            <layout ref='text' det='1,1,90,20'>Body.</layout>
            <layout ref='text' det='1,25,90,30'/></body>
            <footnotes><layout ref='text' det='1,70,90,90'/></footnotes>""")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_xml(encode(source), root / 'page_1.xml')
            (root / 'page_pixel_sizes.json').write_text('{"1": [100, 100]}', encoding='utf-8')
            target = prepare_chapter_analysis(root, TocInfo([], [])).pages[0]
        empty = [layout for layout in target.layouts if layout.ownership == 'ignored']
        self.assertEqual([layout.source_order for layout in empty], [1, 2])
        self.assertEqual([layout.bbox for layout in empty], [(1, 25, 90, 30), (1, 70, 90, 90)])
        self.assertTrue(all(layout.source.origin is not None for layout in empty))
