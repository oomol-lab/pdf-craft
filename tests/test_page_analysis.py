import tempfile
import unittest
from pathlib import Path
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft.extractor.chapter.chapter import (
    Chapter,
    DisplayFormula,
    Reference,
    SourceAsset,
    SourceTextFragment,
    StandaloneAsset,
    TextFlowItem,
    encode,
)
from pdf_craft.extractor.chapter.generation import (
    _assemble_flow_items,
    _extract_body_layouts,
    _resolve_pages,
)
from pdf_craft.extractor.chapter.mark import transform2mark
from pdf_craft.extractor.chapter.page_analysis import (
    UnindexedCitation,
    analyse_pages,
    restore_streams,
)
from pdf_craft.extractor.toc.types import TocInfo
from pdf_craft.pdf import decode


def _fragment(
    page_index: int,
    order: int,
    text: str,
    *references: Reference,
) -> SourceTextFragment:
    return SourceTextFragment(
        page_index=page_index,
        source_order=order,
        bbox=(order, page_index, order + 10, page_index + 10),
        content=[text, *references],
    )


class PageAnalysisTests(unittest.TestCase):
    def test_round_trip_preserves_empty_pages_and_cross_page_paragraph(self):
        paragraph = TextFlowItem(
            "body",
            -1,
            [_fragment(1, 0, "continues"), _fragment(3, 0, " here.")],
        )

        pages = analyse_pages([1, 2, 3], [paragraph], [])
        restored = restore_streams(pages)

        self.assertEqual([page.page_index for page in pages], [1, 2, 3])
        self.assertEqual(pages[1].layouts, [])
        self.assertFalse(pages[0].layouts[0].continues_from_previous)
        self.assertTrue(pages[0].layouts[0].continues_to_next)
        self.assertTrue(pages[2].layouts[0].continues_from_previous)
        self.assertFalse(pages[2].layouts[0].continues_to_next)
        self.assertEqual(restored.paragraphs, [paragraph])
        self.assertEqual(restored.citations, [])

    def test_round_trip_restores_multiple_citations_and_reference_locations(self):
        first_mark = transform2mark("①")
        second_mark = transform2mark("②")
        assert first_mark is not None
        assert second_mark is not None
        first = Reference(
            1,
            1,
            first_mark,
            [TextFlowItem("body", -1, [_fragment(1, 0, "First note.")])],
        )
        second = Reference(
            1,
            2,
            second_mark,
            [TextFlowItem("body", -1, [_fragment(1, 1, "Second note.")])],
        )
        paragraph = TextFlowItem(
            "body",
            -1,
            [_fragment(1, 4, "Body", first, second)],
        )

        pages = analyse_pages([1], [paragraph], [first, second])
        body_layout = next(
            layout
            for layout in pages[0].layouts
            if layout.ownership == "paragraph"
        )
        restored = restore_streams(pages)

        self.assertEqual(
            [
                (location.path, location.citation_page_index, location.citation_index)
                for location in body_layout.references
            ],
            [((1,), 1, 1), ((2,), 1, 2)],
        )
        self.assertEqual(
            [layout.citation_index for layout in pages[0].layouts if layout.ownership == "citation"],
            [1, 2],
        )
        restored_paragraph = restored.paragraphs[0]
        assert isinstance(restored_paragraph, TextFlowItem)
        restored_first = restored_paragraph.children[0].content[1]
        restored_second = restored_paragraph.children[0].content[2]
        self.assertIs(restored_first, restored.citations[0])
        self.assertIs(restored_second, restored.citations[1])
        self.assertEqual(restored.paragraphs, [paragraph])
        self.assertEqual(restored.citations, [first, second])

    def test_round_trip_preserves_cross_page_citation_and_empty_citation(self):
        mark = transform2mark("①")
        empty_mark = transform2mark("②")
        assert mark is not None
        assert empty_mark is not None
        continued = Reference(
            1,
            1,
            mark,
            [TextFlowItem(
                "body",
                -1,
                [_fragment(1, 0, "continued"), _fragment(2, 0, " note")],
            )],
        )
        empty = Reference(2, 1, empty_mark, [])

        pages = analyse_pages([1, 2], [], [continued, empty])
        continuation = next(
            layout
            for layout in pages[1].layouts
            if layout.ownership == "citation"
        )
        origin = next(
            layout
            for layout in pages[0].layouts
            if layout.ownership == "citation"
        )
        restored = restore_streams(pages)

        self.assertEqual(continuation.citation_id, origin.citation_id)
        self.assertIsNone(continuation.citation_index)
        self.assertIsNone(pages[1].citations[0].index)
        self.assertTrue(continuation.continues_from_previous)
        self.assertEqual(restored.citations, [continued, empty])

    def test_unindexed_citation_is_retained_but_legacy_restore_omits_it(self):
        orphaned = TextFlowItem(
            "body",
            -1,
            [_fragment(1, 0, "possible previous-page continuation")],
        )

        pages = analyse_pages(
            [1],
            [],
            [UnindexedCitation(1, (orphaned,))],
        )
        restored = restore_streams(pages)

        self.assertEqual(len(pages[0].layouts), 1)
        self.assertEqual(len(pages[0].citations), 1)
        self.assertIsNone(pages[0].citations[0].index)
        self.assertIsNone(pages[0].layouts[0].citation_index)
        self.assertEqual(restored.citations, [])

    def test_unindexed_citations_must_precede_page_local_indexes(self):
        mark = transform2mark("①")
        assert mark is not None
        indexed = Reference(
            1,
            1,
            mark,
            [TextFlowItem("body", -1, [_fragment(1, 1, "Indexed")])],
        )
        unindexed = UnindexedCitation(
            1,
            (TextFlowItem("body", -1, [_fragment(1, 0, "Unindexed")]),),
        )
        pages = analyse_pages([1], [], [unindexed, indexed])
        pages[0].citations.reverse()

        with self.assertRaisesRegex(
            ValueError,
            "Unindexed citations must precede indexed citations",
        ):
            restore_streams(pages)

    def test_page_local_citation_indexes_must_start_at_one_and_be_contiguous(self):
        first_mark = transform2mark("①")
        second_mark = transform2mark("②")
        assert first_mark is not None
        assert second_mark is not None
        first = Reference(1, 1, first_mark, [])
        second = Reference(1, 2, second_mark, [])
        pages = analyse_pages([1], [], [first, second])
        pages[0].citations[1].index = 3

        with self.assertRaisesRegex(
            ValueError,
            "citation indexes must be contiguous",
        ):
            restore_streams(pages)

    def test_restoration_is_driven_by_editable_gap_annotations(self):
        paragraph = TextFlowItem(
            "body",
            -1,
            [_fragment(1, 0, "first"), _fragment(2, 0, " second")],
        )
        pages = analyse_pages([1, 2], [paragraph], [])

        pages[0].layouts[0].annotations.continues_to_next = False
        pages[1].layouts[0].annotations.continues_from_previous = False
        restored = restore_streams(pages)

        self.assertEqual(len(restored.paragraphs), 2)
        self.assertEqual(paragraph.children[0].content, ["first"])
        self.assertEqual(paragraph.children[1].content, [" second"])

    def test_ownership_change_uses_one_collision_free_document_order(self):
        mark = transform2mark("①")
        assert mark is not None
        paragraphs = [
            TextFlowItem("body", -1, [_fragment(1, 0, "first")]),
            TextFlowItem("body", -1, [_fragment(1, 1, "second")]),
        ]
        citation = Reference(
            1,
            1,
            mark,
            [TextFlowItem("body", -1, [_fragment(1, 2, "moved")])],
        )
        pages = analyse_pages([1], paragraphs, [citation])
        moved = next(
            layout for layout in pages[0].layouts if layout.ownership == "citation"
        )
        moved.annotations.ownership = "paragraph"
        moved.annotations.citation_id = None
        moved.annotations.citation_index = None

        restored = restore_streams(pages)

        self.assertTrue(
            all(isinstance(item, TextFlowItem) for item in restored.paragraphs)
        )
        self.assertEqual(
            [
                item.children[0].content
                for item in restored.paragraphs
                if isinstance(item, TextFlowItem)
            ],
            [["first"], ["second"], ["moved"]],
        )

    def test_round_trip_preserves_assets_at_flow_item_boundary(self):
        first = TextFlowItem("body", -1, [_fragment(1, 0, "sentence continues")])
        image = SourceAsset(1, "image", (0, 20, 10, 30), asset_hash="a" * 64)
        table = SourceAsset(1, "table", (0, 31, 10, 39), content=["<table></table>"])
        second = TextFlowItem("body", -1, [_fragment(1, 2, " to its end.")])
        formula = SourceAsset(1, "formula", (0, 40, 10, 50), content=["x^2"])
        third = TextFlowItem("body", -1, [_fragment(1, 4, "After formula.")])
        legacy = [first, image, table, second, formula, third]

        restored = restore_streams(analyse_pages([1], legacy, [])).paragraphs
        flow = list(_assemble_flow_items(restored))

        self.assertEqual(
            [type(item) for item in flow],
            [TextFlowItem, DisplayFormula, TextFlowItem],
        )
        assert isinstance(flow[0], TextFlowItem)
        self.assertEqual(
            [type(child) for child in flow[0].children],
            [
                SourceTextFragment,
                SourceAsset,
                SourceAsset,
                SourceTextFragment,
            ],
        )

    def test_ocr_fixture_matches_legacy_canonical_chapter_xml(self):
        with tempfile.TemporaryDirectory() as directory:
            pages_path = Path(directory)
            page_sources = [
                """<page index='1'><body>
                <layout ref='text' det='1,1,90,20'>Body① continues</layout>
                <layout ref='image' det='1,22,90,60' hash='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'>figure</layout>
                <layout ref='text' det='1,62,90,82'> to its end.</layout>
                </body><footnotes>
                <layout ref='text' det='1,90,90,105'>orphaned preface.</layout>
                <layout ref='text' det='1,106,90,121'>① First note.</layout>
                <layout ref='text' det='1,122,90,137'>② Unmatched note.</layout>
                </footnotes></page>""",
                """<page index='2'><body>
                <layout ref='equation' det='1,1,90,20'>x^2</layout>
                <layout ref='text' det='1,22,90,42'>After formula.</layout>
                </body><footnotes>
                <layout ref='text' det='1,50,90,65'>① Cross-page note continues</layout>
                </footnotes></page>""",
                """<page index='3'><body>
                <layout ref='text' det='1,1,90,20'>Final page.</layout>
                </body><footnotes>
                <layout ref='text' det='1,50,90,65'>on this page.</layout>
                </footnotes></page>""",
            ]
            source_pages = []
            for page_index, source in enumerate(page_sources, 1):
                (pages_path / f"page_{page_index}.xml").write_text(
                    source, encoding="utf-8"
                )
                source_pages.append(decode(fromstring(source)))

            legacy_paragraphs, citation_resolutions = _resolve_pages(source_pages)
            legacy_citations = [
                citation
                for citation in citation_resolutions
                if isinstance(citation, Reference)
            ]
            self.assertIsInstance(citation_resolutions[0], UnindexedCitation)
            legacy = Chapter(
                None,
                -1,
                list(_assemble_flow_items(legacy_paragraphs)),
            )
            projected = Chapter(
                None,
                -1,
                list(_assemble_flow_items(
                    _extract_body_layouts(pages_path, TocInfo([], []))
                )),
            )

            self.assertEqual(tostring(encode(projected)), tostring(encode(legacy)))
            self.assertEqual(
                [type(item) for item in projected.flow_items],
                [TextFlowItem, DisplayFormula, TextFlowItem, TextFlowItem],
            )
            first_note = legacy_citations[0].flow_items[0]
            assert isinstance(first_note, TextFlowItem)
            self.assertEqual(first_note.children[0].content, ["First note."])
            references = [
                part
                for flow in projected.flow_items
                if isinstance(flow, TextFlowItem)
                for child in flow.children
                if isinstance(child, SourceTextFragment)
                for part in child.content
                if isinstance(part, Reference)
            ]
            self.assertEqual([(ref.page_index, ref.order) for ref in references], [(1, 1)])
            self.assertTrue(
                all(
                    not isinstance(item, StandaloneAsset)
                    for reference in references
                    for item in reference.flow_items
                )
            )


if __name__ == "__main__":
    unittest.main()
