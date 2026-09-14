import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_craft.extractor.chapter.chapter import ParagraphLayout
from pdf_craft.extractor.chapter.generation import _extract_body_layouts
from pdf_craft.extractor.toc.analysing import analyse_toc
from pdf_craft.extractor.toc.toc_pages import MatchedTitle, PageRef, TitleReference


class TocExtractionTests(unittest.TestCase):
    def test_printed_toc_switch_only_controls_page_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "pages"
            pages.mkdir()
            (pages / "page_1.xml").write_text(
                "<page index='1'><body>"
                "<layout ref='text' det='1,1,90,20'>Chapter One .... 7</layout>"
                "</body></page>",
                encoding="utf-8",
            )
            (pages / "page_2.xml").write_text(
                "<page index='2'><body>"
                "<layout ref='title' det='1,1,90,30'>Chapter One</layout>"
                "</body></page>",
                encoding="utf-8",
            )
            ref = PageRef(
                page_index=1,
                score=1.0,
                matched_titles=[
                    MatchedTitle(
                        text="chapter one",
                        score=1.0,
                        references=[TitleReference(2, 0)],
                    )
                ],
            )

            with patch("pdf_craft.extractor.toc.analysing.find_toc_pages") as find:
                no_printed_toc = analyse_toc(
                    pages, root / "no-toc.xml", toc_assumed=False
                )
                find.assert_not_called()

            with patch(
                "pdf_craft.extractor.toc.analysing.find_toc_pages",
                return_value=[ref],
            ):
                printed_toc = analyse_toc(
                    pages, root / "printed-toc.xml", toc_assumed=True
                )

            self.assertEqual(no_printed_toc.page_indexes, [])
            self.assertEqual(printed_toc.page_indexes, [1])
            self.assertTrue(no_printed_toc.content)
            self.assertTrue(printed_toc.content)
            self.assertIn(1, _body_page_indexes(pages, no_printed_toc))
            self.assertNotIn(1, _body_page_indexes(pages, printed_toc))


def _body_page_indexes(pages: Path, toc) -> set[int]:
    result: set[int] = set()
    for layout in _extract_body_layouts(pages, toc):
        if isinstance(layout, ParagraphLayout):
            result.update(block.page_index for block in layout.blocks)
    return result
