import tempfile
import unittest
from pathlib import Path

from pdf_craft.pdf.furniture import (
    FurnitureSection,
    _bind_pattern_positions,
    _discover_patterns,
    _discover_tracks,
    _is_covered,
    _similar,
    extract_furnitures,
)
from pdf_craft.extractor.toc.types import Toc, TocInfo


class FurnitureTests(unittest.TestCase):
    def test_native_text_is_kept_when_ocr_has_no_matching_box(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "source.pdf"
            from reportlab.pdfgen.canvas import Canvas
            canvas = Canvas(str(pdf), pagesize=(200, 200))
            canvas.setFont("Helvetica", 12)
            canvas.drawString(20, 170, "RUNNING HEAD")
            canvas.save()
            ocr = root / "ocr"
            ocr.mkdir()
            (ocr / "page_1.xml").write_text("<page><body><layout det='10,0,190,20'>body</layout></body></page>", encoding="utf-8")
            output = extract_furnitures(pdf, ocr)
            section = output.find("pages/page/section")
            assert section is not None
            self.assertTrue(section.text)

    def test_ocr_covered_native_text_is_filtered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "source.pdf"
            from reportlab.pdfgen.canvas import Canvas
            canvas = Canvas(str(pdf), pagesize=(200, 200))
            canvas.setFont("Helvetica", 12)
            canvas.drawString(20, 170, "BODY")
            canvas.save()
            ocr = root / "ocr"
            ocr.mkdir()
            (ocr / "page_1.xml").write_text("<page><body><layout det='0,0,1000,1000'>body</layout></body></page>", encoding="utf-8")
            output = extract_furnitures(pdf, ocr)
            self.assertIsNone(output.find("pages/page/section"))

    def test_native_text_needs_substantial_horizontal_coverage_to_be_filtered(self):
        native = (0, 100, 100, 120)
        # This only touches the lower 50% and right 70% of the native line.
        # It can be a body block adjacent to a running header, not evidence
        # that the header's native text is already represented by OCR flow.
        self.assertFalse(_is_covered(native, (30, 110, 200, 200)))
        # OCR and Poppler may disagree about a line's vertical bounds, but a
        # block owning its entire text span is still a real duplicate.
        self.assertTrue(_is_covered(native, (0, 110, 100, 200)))

    def test_three_page_track_creates_one_universal_position(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in range(1, 5)
        }
        patterns = _discover_patterns(pages)
        universal = next(pattern for pattern in patterns if pattern.kind == "universal")
        self.assertEqual(universal.positions[0].content, "Header")
        self.assertEqual([s.page_index for s in universal.positions[0].sections], [1, 2, 3, 4])

    def test_initial_track_tolerates_one_universal_gap(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in (1, 2, 4)
        }
        tracks = _discover_tracks(pages, 1)
        self.assertEqual([*tracks[0].sections], [1, 2, 4])
        universal = [p for p in _discover_patterns(pages) if p.kind == "universal"]
        self.assertEqual([[s.page_index for s in p.positions[0].sections] for p in universal], [[1, 2], [4]])

    def test_initial_track_tolerates_one_same_side_gap(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in (1, 3, 7)
        }
        tracks = _discover_tracks(pages, 2)
        self.assertEqual([*tracks[0].sections], [1, 3, 7])

    def test_two_consecutive_gaps_end_a_track_instead_of_reviving_it(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in (1, 2, 5, 6, 7)
        }
        tracks = _discover_tracks(pages, 1)
        # The first two instances never become a Position.  The later run is
        # a new track, rather than a revival across page 3 and page 4.
        self.assertEqual([[*track.sections] for track in tracks], [[5, 6, 7]])

    def test_sections_on_one_axis_are_matched_without_early_claiming(self):
        pages = {
            page_index: [
                FurnitureSection(page_index, (10, 10, 100, 30), "Left header"),
                FurnitureSection(page_index, (200, 10, 290, 30), "Right header"),
            ]
            for page_index in range(1, 4)
        }
        tracks = _discover_tracks(pages, 1)
        self.assertEqual(
            [
                [section.content for _, section in sorted(track.sections.items())]
                for track in tracks
            ],
            [["Left header"] * 3, ["Right header"] * 3],
        )

    def test_same_side_tracks_both_page_parities_independently(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in range(1, 7)
        }
        same_side = [pattern for pattern in _discover_patterns(pages) if pattern.kind == "same_side"]
        self.assertEqual(
            [[section.page_index for section in pattern.positions[0].sections] for pattern in same_side],
            [[2, 4, 6], [1, 3, 5]],
        )

    def test_pattern_is_derived_from_the_current_position_combination(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in range(1, 7)
        }
        for index in (1, 2, 3, 5, 6):
            pages[index].append(FurnitureSection(index, (200, 10, 280, 30), "Folio"))
        universal = [p for p in _discover_patterns(pages) if p.kind == "universal"]
        self.assertEqual(
            [[len(position.sections) for position in pattern.positions] for pattern in universal],
            [[3, 3], [1], [2, 2]],
        )

    def test_new_stable_position_starts_a_new_pattern_combination(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Header")]
            for index in range(1, 7)
        }
        for index in (4, 5, 6):
            pages[index].append(FurnitureSection(index, (200, 10, 280, 30), "New"))
        universal = [p for p in _discover_patterns(pages) if p.kind == "universal"]
        self.assertEqual([len(pattern.positions) for pattern in universal], [1, 2])

    def test_tag_fixture_keeps_header_and_folio_but_not_ocr_covered_body(self):
        fixture = Path(__file__).parent / "assets/pdf/tag.pdf"
        with tempfile.TemporaryDirectory() as directory:
            ocr = Path(directory) / "ocr"
            ocr.mkdir()
            (ocr / "page_pixel_sizes.json").write_text('{"4": [2480, 3509]}', encoding="utf-8")
            (ocr / "page_4.xml").write_text(
                "<page><body>"
                "<layout det='570,681,1897,779'>body</layout>"
                "<layout det='570,786,1897,1881'>body</layout>"
                "<layout det='570,1986,1488,2028'>title</layout>"
                "<layout det='570,2088,1897,2681'>body</layout>"
                "</body><footnotes><layout det='613,2772,1118,2807'>footnote</layout>"
                "</footnotes></page>",
                encoding="utf-8",
            )
            output = extract_furnitures(fixture, ocr)
            contents = ["".join(section.itertext()).strip() for section in output.findall("pages/page/section")]
            self.assertIn("CHRISTOPH BRÜLL", contents)
            self.assertIn("314", contents)
            self.assertFalse(any("geistiger Brandstifter" in content for content in contents))
            self.assertFalse(any("redete" in content for content in contents))
            self.assertFalse(any("Ende der Geschichte" in content for content in contents))
            self.assertFalse(any("Zu Fukuyama" in content for content in contents))

    def test_internal_fragment_topology_is_part_of_matching(self):
        left = FurnitureSection(1, (0, 0, 100, 40), "A B", fragments=(
            (0.0, 0.0, 0.45, 0.5, "A"), (0.55, 0.0, 0.45, 0.5, "B")))
        right = FurnitureSection(2, (2, 1, 102, 41), "A B", fragments=(
            (0.0, 0.0, 0.45, 0.5, "A"), (0.55, 0.0, 0.45, 0.5, "B")))
        different = FurnitureSection(2, (2, 1, 102, 41), "A B", fragments=(
            (0.0, 0.0, 0.45, 0.5, "B"), (0.0, 0.55, 0.45, 0.5, "A")))
        self.assertTrue(_similar(left, right))
        self.assertFalse(_similar(left, different))

    def test_changing_fragment_text_still_matches_position(self):
        left = FurnitureSection(1, (0, 0, 100, 40), "Page 1", fragments=(
            (0.0, 0.0, 0.45, 0.5, "Page"), (0.55, 0.0, 0.45, 0.5, "1")))
        right = FurnitureSection(2, (2, 1, 102, 41), "Page 2", fragments=(
            (0.0, 0.0, 0.45, 0.5, "Page"), (0.55, 0.0, 0.45, 0.5, "2")))
        self.assertTrue(_similar(left, right))

    def test_canonical_content_tie_uses_first_match(self):
        pages = {
            1: [FurnitureSection(1, (10, 10, 100, 30), "first", fragments=((0, 0, 1, 1, "x"),))],
            2: [FurnitureSection(2, (10, 10, 100, 30), "second", fragments=((0, 0, 1, 1, "x"),))],
            3: [FurnitureSection(3, (10, 10, 100, 30), "first", fragments=((0, 0, 1, 1, "x"),))],
        }
        pattern = next(p for p in _discover_patterns(pages) if p.kind == "universal")
        self.assertEqual(pattern.positions[0].content, "first")

    def test_only_stable_pattern_position_can_bind_a_toc_id(self):
        pages = {
            index: [FurnitureSection(index, (10, 10, 100, 30), "Chapter One")]
            for index in range(1, 4)
        }
        patterns = _discover_patterns(pages)
        _bind_pattern_positions(patterns, {7: "Chapter One"})
        position = next(
            pattern.positions[0]
            for pattern in patterns
            if pattern.kind == "universal"
        )
        self.assertEqual(position.toc_id, 7)

        fragment = FurnitureSection(1, (10, 10, 100, 30), "Chapter One")
        self.assertIsNone(fragment.toc_id)

    def test_printed_toc_page_is_represented_as_furniture_with_safe_toc_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "source.pdf"
            from reportlab.pdfgen.canvas import Canvas
            canvas = Canvas(str(pdf), pagesize=(200, 200))
            canvas.drawString(20, 170, "Contents")
            canvas.save()
            ocr = root / "ocr"
            ocr.mkdir()
            (ocr / "page_1.xml").write_text(
                "<page index='1'><body>"
                "<layout det='20,10,180,30'>Chapter One .... 7</layout>"
                "<layout det='20,40,180,60'>Unmatched .... 9</layout>"
                "<layout det='20,70,180,90'>Chapter One Overview .... 11</layout>"
                "</body></page>",
                encoding="utf-8",
            )
            (ocr / "page_2.xml").write_text(
                "<page index='2'><body>"
                "<layout ref='title' det='20,10,180,30'>Chapter One</layout>"
                "</body></page>",
                encoding="utf-8",
            )
            toc = TocInfo(
                content=[Toc(id=7, page_index=2, order=0, level=0, children=[])],
                page_indexes=[1],
            )

            output = extract_furnitures(pdf, ocr, toc=toc)
            sections = output.findall("pages/page/section")
            self.assertEqual(sections[0].get("toc_id"), "7")
            self.assertEqual(sections[0].text, "Chapter One .... 7")
            self.assertIsNone(sections[1].get("toc_id"))
            self.assertIsNone(sections[2].get("toc_id"))
