# pylint: disable=no-member,protected-access,c-extension-no-member

import tempfile
import unittest
from shutil import which
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

import pypdf
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from PIL import Image
from reportlab.pdfgen import canvas

from pdf_craft.pdf.handler import PDFHandler
from pdf_craft.pipeline.pdf import (
    FillWindowPlan, FittedParagraph, GhostscriptVisualBaseCompiler, PDFPatcher, PDFReplacement,
    PDFReplacementRegion, PatchTextOptions, PatchTextStyle,
    QTextParagraphFiller,
)
from pdf_craft.pipeline.pdf.text_layout import _CJK_FONT_CANDIDATES, _ensure_qt_application


class BlankVisualBaseCompiler:
    """A deterministic compiler double; production always uses Ghostscript."""

    def compile(self, source_path: Path, target_path: Path) -> None:
        reader = pypdf.PdfReader(str(source_path))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_blank_page(float(page.mediabox.width), float(page.mediabox.height))
        with target_path.open("wb") as output:
            writer.write(output)


class TestPDFPatcher(unittest.TestCase):
    def setUp(self):
        self._compiler = BlankVisualBaseCompiler()

    def patcher(self, *args, **kwargs) -> PDFPatcher:
        return PDFPatcher(*args, visual_base_compiler=self._compiler, **kwargs)

    def test_legacy_font_size_remains_an_explicit_maximum(self):
        patcher = self.patcher(font_size=12)

        self.assertEqual(patcher.options.max_font_size, 12)
        self.assertEqual(patcher.options.style_for("text", 0).max_font_size, 12)
        self.assertEqual(patcher.options.style_for("sub_title", 0).max_font_size, 12)

        fitted = QTextParagraphFiller(patcher.options).fit(
            PDFReplacement(
                1, (0, 0, 1_000, 500),
                "A spacious headline must keep the legacy font-size ceiling.",
                (1_000, 500), layout_ref="sub_title",
            ),
            {1: (1_000, 500)},
        )

        self.assertEqual(fitted.font_size, 12)

    def test_automatic_font_scans_all_replacements_before_layout_in_either_input_order(self):
        """A later CJK title must influence the run-wide automatic family."""
        from PySide6 import QtGui

        _ensure_qt_application(QtGui)
        installed = {family.casefold(): family for family in QtGui.QFontDatabase.families()}
        cjk_font = next(
            (installed[candidate.casefold()] for candidate in _CJK_FONT_CANDIDATES
             if candidate.casefold() in installed),
            None,
        )
        if cjk_font is None:
            self.skipTest("this Qt installation has no preferred CJK candidate")

        body = PDFReplacement(1, (5, 5, 195, 75), "English body", (200, 200))
        title = PDFReplacement(
            1, (5, 80, 195, 195), "中文标题", (200, 200), layout_ref="sub_title",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(5, 5, "source")
            doc.save()

            for index, replacements in enumerate(((body, title), (title, body))):
                patcher = self.patcher(options=PatchTextOptions(max_font_size=8, min_font_size=8))
                patcher.patch(source, root / f"target-{index}.pdf", replacements)
                resolutions = patcher.font_resolutions
                self.assertEqual(len(resolutions), 1)
                self.assertEqual(resolutions[0].source, "automatic")
                self.assertEqual(resolutions[0].resolved_font_name, cjk_font)

    def test_samples_colored_background_from_the_source_page_handler(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(100, 100))
            doc.drawString(1, 1, "source")
            doc.save()
            document: Any = Mock()
            document.render_page.return_value = Image.new("RGB", (100, 100), (234, 220, 183))
            handler: Any = Mock()
            handler.open.return_value = document

            self.patcher(font_size=8, pdf_handler=handler).patch(
                source,
                target,
                [PDFReplacement(1, (20, 20, 80, 80), "translated", (100, 100))],
            )

            document.render_page.assert_called_once_with(1, 300)
            document.close.assert_called_once_with()
            page_contents = pypdf.PdfReader(str(target)).pages[0].get_contents()
            assert page_contents is not None
            contents = page_contents.get_data()
            self.assertIn(b"0.917647 0.862745 0.717647 rg", contents)

    def test_replaces_region_and_preserves_page_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "nested" / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.setFont("Helvetica", 12)
            doc.drawString(20, 160, "Original")
            doc.save()

            self.patcher(font_size=12).patch(
                source,
                target,
                [PDFReplacement(1, (50, 25, 450, 100), "Translated", (600, 600))],
            )

            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 1)
            page = list(reader.pages)[0]
            self.assertIn("Translated", page.extract_text())
            self.assertNotIn("Original", page.extract_text())
            self.assertEqual(len(list(page.images)), 0)

    def test_writes_a_narrow_headline_as_a_natural_right_overflow_line(self):
        """A headline lower bound never prevents a patched PDF from being written."""
        options = PatchTextOptions(
            styles={
                "text": PatchTextStyle(max_font_size=10, min_font_size=10),
                "sub_title": PatchTextStyle(max_font_size=11, min_font_size=4),
            },
            headline_min_body_ratio=1.2,
        )
        body = PDFReplacement(1, (0, 50, 200, 100), "Body", (200, 100))
        title = PDFReplacement(
            1, (0, 10, 36, 40), "A deliberately long heading", (200, 100),
            layout_ref="sub_title",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 100))
            doc.drawString(0, 90, "Original title")
            doc.save()

            self.patcher(options=options).patch(source, target, [body, title])

            extracted = " ".join(
                pypdf.PdfReader(str(target)).pages[0].extract_text().split()
            )
            self.assertIn("A deliberately long heading", extracted)

    def test_rejects_invalid_bbox(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (4, 4, 2, 3), "text", (100, 100)))

    def test_rejects_bbox_outside_page_pixels(self):
        with self.assertRaises(ValueError):
            PDFPatcher().validate(PDFReplacement(1, (1, 1, 101, 20), "text", (100, 100)))

    def test_replaces_multi_region_paragraph_with_one_qt_text_layer(self):
        first = PDFReplacementRegion(1, (1, 1, 20, 20), (100, 100), reading_order=1)
        second = PDFReplacementRegion(1, (1, 22, 99, 98), (100, 100), reading_order=2)
        replacement = PDFReplacement(
            1, first.bbox, "translated paragraph", first.page_pixel_size,
            regions=(first, second),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(100, 100))
            doc.drawString(1, 90, "Original source page")
            doc.save()

            self.patcher(options=PatchTextOptions(max_font_size=8, min_font_size=8)).patch(
                source, target, [replacement]
            )

            page: Any = pypdf.PdfReader(str(target)).pages[0]
            self.assertIn("translated", page.extract_text().replace("\n", "").lower())
            self.assertEqual(len(list(page.images)), 0)

    def test_rejects_single_region_that_disagrees_with_patch_geometry(self):
        region = PDFReplacementRegion(1, (1, 1, 20, 20), (100, 100), reading_order=1)
        cases = (
            PDFReplacement(1, (1, 1, 0, 0), "text", (100, 100), reading_order=1, regions=(region,)),
            PDFReplacement(2, region.bbox, "text", region.page_pixel_size, reading_order=1, regions=(region,)),
        )

        for replacement in cases:
            with self.subTest(replacement=replacement), self.assertRaisesRegex(
                ValueError, "must match its only source region",
            ):
                PDFPatcher().validate(replacement)

    def test_rejects_missing_source_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.save()

            with self.assertRaises(ValueError):
                self.patcher().patch(
                    source,
                    root / "target.pdf",
                    [PDFReplacement(2, (1, 1, 10, 10), "text", (100, 100))],
                )

    def test_cjk_multiline_replacement_has_text_layer_and_fits_source_bbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(1, 1, "source")
            doc.save()
            replacement = PDFReplacement(
                1, (60, 60, 360, 360), "\u8fd9\u662f\u4e00\u6bb5\u6ca1\u6709\u7a7a\u683c\u7684\u4e2d\u6587\u8bd1\u6587\uff0c\u5b83\u5e94\u8be5\u5728\u65b9\u6846\u5185\u81ea\u52a8\u6362\u884c\u3002" * 3, (600, 600)
            )
            patcher = self.patcher(options=PatchTextOptions(max_font_size=12, min_font_size=4))
            fitted = QTextParagraphFiller(patcher.options).fit(replacement, {1: (200, 200)})

            patcher.patch(source, target, [replacement])

            source_page: Any = pypdf.PdfReader(str(source)).pages[0]
            reader = pypdf.PdfReader(str(target))
            self.assertEqual(len(reader.pages), 1)
            page: Any = reader.pages[0]
            # An absent CJK font is allowed to fall back (even to a missing-glyph
            # font), so Unicode extraction is platform dependent. The English
            # patch tests cover extraction; here prove that CJK layout fits and
            # produces a genuine PDF font/content layer instead of an image.
            for placement in fitted.placements:
                self.assertTrue(all(
                    placement.rectangle.top <= top
                    and top + height <= placement.rectangle.bottom
                    for top, height in zip(placement.line_tops, placement.line_heights)
                ))
            source_fonts = set(source_page["/Resources"]["/Font"].get_object())
            result_fonts = set(page["/Resources"]["/Font"].get_object())
            self.assertGreater(len(result_fonts - source_fonts), 0)
            self.assertGreater(
                len(page.get_contents().get_data()), len(source_page.get_contents().get_data()),
            )
            self.assertEqual(len(list(page.images)), 0)

    def test_preflight_failure_leaves_no_partial_target_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(1, 1, "source")
            doc.save()
            patcher = self.patcher(options=PatchTextOptions(max_font_size=8, min_font_size=8))

            with self.assertRaisesRegex(ValueError, "page 1, bbox"):
                patcher.patch(
                    source,
                    target,
                    [PDFReplacement(1, (10, 10, 30, 30), "too much text " * 100, (200, 200))],
                )
            self.assertFalse(target.exists())

    def test_explicit_skip_records_overflow_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(1, 1, "source")
            doc.save()
            patcher = self.patcher(options=PatchTextOptions(max_font_size=8, min_font_size=8, overflow="skip"))

            patcher.patch(
                source,
                target,
                [PDFReplacement(1, (10, 10, 30, 30), "too much text " * 100, (200, 200))],
            )

            self.assertEqual(len(patcher.skipped_replacements), 1)
            self.assertIn("cannot fit paragraph source regions", patcher.skipped_replacements[0].reason)

    def test_long_cross_page_window_releases_each_source_image_before_the_next(self):
        """Composition consumes serialized page work without materializing a whole window."""
        class TrackingImage:
            def __init__(self, page_index):
                self.page_index = page_index
                self.closed = False

            def close(self):
                self.closed = True

        class TrackingDocument:
            def __init__(self):
                self.images = []
                self.closed = False

            def render_page(self, page_index, _dpi):
                if any(not image.closed for image in self.images):
                    raise AssertionError("previous source image was retained")
                image = TrackingImage(page_index)
                self.images.append(image)
                return image

            def close(self):
                self.closed = True

        class TrackingHandler:
            def __init__(self):
                self.document = TrackingDocument()

            def open(self, _source):
                return self.document

        class LightweightFiller:
            def __init__(self, options):
                self.options = options

            @staticmethod
            def fit(replacement, _page_sizes, _minimum_font_size=None):
                return FittedParagraph(replacement.text, 10, ())

        class RecordingEraser:
            def __init__(self):
                self.image_map_sizes = []

            def plan(self, regions, _page_sizes, page_images):
                self.image_map_sizes.append(len(page_images))
                if len(tuple(regions)) != 1:
                    raise AssertionError("page image must only serve its own regions")
                return ()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(100, 100))
            for _ in range(8):
                doc.showPage()
            doc.save()
            handler = TrackingHandler()
            patcher = self.patcher(
                options=PatchTextOptions(max_font_size=10, min_font_size=10),
                pdf_handler=cast(PDFHandler, handler),
            )
            patcher._filler = LightweightFiller(patcher.options)  # type: ignore[assignment]
            eraser = RecordingEraser()
            patcher._eraser = eraser  # type: ignore[assignment]
            regions = tuple(
                PDFReplacementRegion(page_index, (1, 1, 99, 99), (100, 100))
                for page_index in range(1, 9)
            )
            replacement = PDFReplacement(
                1, regions[0].bbox, "long paragraph", (100, 100), regions=regions,
            )

            def fail_if_materialized(_plan):
                raise AssertionError("patcher must stream page contributions")

            with patch.object(FillWindowPlan, "paragraphs", new=property(fail_if_materialized)):
                patcher.patch(source, target, [replacement])

            self.assertEqual([image.page_index for image in handler.document.images], list(range(1, 9)))
            self.assertTrue(all(image.closed for image in handler.document.images))
            self.assertEqual(eraser.image_map_sizes, [1] * 8)
            self.assertTrue(handler.document.closed)

    def test_lifts_all_page_annotations_above_the_translation_layer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "Original")
            doc.linkURL("https://example.invalid/original", (20, 150, 100, 170), relative=0)
            doc.save()

            self.patcher(font_size=12).patch(
                source, target, [PDFReplacement(1, (50, 25, 450, 100), "Translated", (600, 600))]
            )

            page: Any = pypdf.PdfReader(str(target)).pages[0]
            annotations = page["/Annots"]
            self.assertEqual(len(annotations), 1)
            annotation = annotations[0].get_object()
            self.assertEqual(annotation["/Subtype"], "/Link")
            self.assertEqual(annotation["/A"]["/URI"], "https://example.invalid/original")
            self.assertEqual(annotation.raw_get("/P").idnum, page.indirect_reference.idnum)
            self.assertNotIn("Original", page.extract_text())
            self.assertIn("Translated", page.extract_text())

    def test_preserves_popup_parent_and_reply_annotation_relationships(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "Original")
            doc.save()

            source_reader = pypdf.PdfReader(str(source))
            source_writer = pypdf.PdfWriter(clone_from=source_reader)
            text = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Text"),
                NameObject("/Rect"): ArrayObject([NumberObject(20), NumberObject(120), NumberObject(40), NumberObject(140)]),
                NameObject("/Contents"): TextStringObject("parent note"),
            })
            text_reference = source_writer._add_object(text)  # pylint: disable=protected-access
            popup = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Popup"),
                NameObject("/Rect"): ArrayObject([NumberObject(40), NumberObject(100), NumberObject(160), NumberObject(140)]),
                NameObject("/Parent"): text_reference,
            })
            popup_reference = source_writer._add_object(popup)  # pylint: disable=protected-access
            text[NameObject("/Popup")] = popup_reference
            reply = DictionaryObject({
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Text"),
                NameObject("/Rect"): ArrayObject([NumberObject(20), NumberObject(80), NumberObject(40), NumberObject(100)]),
                NameObject("/Contents"): TextStringObject("reply note"),
                NameObject("/IRT"): text_reference,
            })
            reply_reference = source_writer._add_object(reply)  # pylint: disable=protected-access
            source_page: Any = source_writer.pages[0]
            source_page[NameObject("/Annots")] = ArrayObject([text_reference, popup_reference, reply_reference])  # pylint: disable=unsupported-assignment-operation
            with source.open("wb") as output:
                source_writer.write(output)

            self.patcher().patch(source, target, [])

            page: Any = pypdf.PdfReader(str(target)).pages[0]
            annotations = [reference.get_object() for reference in page["/Annots"]]
            parent = annotations[0]
            popup_result = annotations[1]
            reply_result = annotations[2]
            self.assertEqual(parent.raw_get("/Popup").idnum, popup_result.indirect_reference.idnum)
            self.assertEqual(popup_result.raw_get("/Parent").idnum, parent.indirect_reference.idnum)
            self.assertEqual(reply_result.raw_get("/IRT").idnum, parent.indirect_reference.idnum)
            self.assertIsNotNone(page.indirect_reference)
            for annotation in annotations:
                self.assertEqual(annotation.raw_get("/P").idnum, page.indirect_reference.idnum)

    def test_remaps_annotation_page_destinations_to_visual_base_pages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "first source page")
            doc.linkAbsolute("next", "second-page", (20, 150, 100, 170))
            doc.showPage()
            doc.bookmarkPage("second-page")
            doc.drawString(20, 160, "second source page")
            doc.save()

            self.patcher().patch(source, target, [])

            reader = pypdf.PdfReader(str(target))
            first_page: Any = reader.pages[0]
            second_page: Any = reader.pages[1]
            annotation = first_page["/Annots"][0].get_object()
            destination = annotation.raw_get("/Dest")
            self.assertIsNotNone(second_page.indirect_reference)
            self.assertEqual(destination[0].idnum, second_page.indirect_reference.idnum)
            self.assertNotIn("second source page", second_page.extract_text())

    def test_resolves_named_annotation_destination_to_a_visual_base_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "first source page")
            doc.showPage()
            doc.drawString(20, 160, "second source page")
            doc.save()

            source_reader = pypdf.PdfReader(str(source))
            source_writer = pypdf.PdfWriter(clone_from=source_reader)
            source_writer.add_named_destination("destination-name", 1)
            annotation_reference = source_writer._add_object(DictionaryObject({  # pylint: disable=protected-access
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Link"),
                NameObject("/Rect"): ArrayObject([NumberObject(20), NumberObject(140), NumberObject(100), NumberObject(170)]),
                NameObject("/Dest"): TextStringObject("destination-name"),
            }))
            source_page: Any = source_writer.pages[0]
            source_page[NameObject("/Annots")] = ArrayObject([annotation_reference])  # pylint: disable=unsupported-assignment-operation
            with source.open("wb") as output:
                source_writer.write(output)

            self.patcher().patch(source, target, [])

            result = pypdf.PdfReader(str(target))
            first_page: Any = result.pages[0]
            second_page: Any = result.pages[1]
            catalog: Any = result.trailer["/Root"]
            annotation = first_page["/Annots"][0].get_object()
            destination = annotation.raw_get("/Dest")
            self.assertIsInstance(destination, ArrayObject)
            self.assertIsNotNone(second_page.indirect_reference)
            self.assertEqual(destination[0].idnum, second_page.indirect_reference.idnum)
            self.assertNotIn("/Names", catalog)

    def test_lifts_widget_with_its_acroform_field_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "Original")
            doc.acroForm.textfield(
                name="customer_name", value="Ada", x=20, y=100, width=120, height=20,
            )
            doc.save()

            source_reader = pypdf.PdfReader(str(source))
            source_writer = pypdf.PdfWriter(clone_from=source_reader)
            orphan_field = source_writer._add_object(DictionaryObject({  # pylint: disable=protected-access
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject("orphan_field"),
            }))
            source_acroform: Any = source_writer._root_object[NameObject("/AcroForm")]  # pylint: disable=protected-access
            source_acroform["/Fields"].append(orphan_field)
            source_acroform[NameObject("/XFA")] = TextStringObject("unrelated-xfa-payload")
            with source.open("wb") as output:
                source_writer.write(output)

            self.patcher().patch(source, target, [])

            reader = pypdf.PdfReader(str(target))
            fields: Any = reader.get_fields()
            catalog: Any = reader.trailer["/Root"]
            page: Any = reader.pages[0]
            self.assertEqual(set(fields or {}), {"customer_name"})
            self.assertIn("/AcroForm", catalog)
            self.assertNotIn("/XFA", catalog["/AcroForm"])
            widget = page["/Annots"][0].get_object()
            self.assertEqual(widget["/Subtype"], "/Widget")
            self.assertIsNotNone(page.indirect_reference)
            self.assertEqual(widget.raw_get("/P").idnum, page.indirect_reference.idnum)

    def test_omits_orphan_acroform_when_no_widget_annotation_is_lifted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "Original")
            doc.linkURL("https://example.invalid/original", (20, 150, 100, 170), relative=0)
            doc.save()

            source_reader = pypdf.PdfReader(str(source))
            source_writer = pypdf.PdfWriter(clone_from=source_reader)
            orphan_field = source_writer._add_object(DictionaryObject({  # pylint: disable=protected-access
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject("orphan_field"),
            }))
            acroform = source_writer._add_object(DictionaryObject({  # pylint: disable=protected-access
                NameObject("/Fields"): ArrayObject([orphan_field]),
                NameObject("/XFA"): TextStringObject("unrelated-xfa-payload"),
            }))
            source_writer._root_object[NameObject("/AcroForm")] = acroform  # pylint: disable=protected-access
            with source.open("wb") as output:
                source_writer.write(output)

            self.assertEqual(set(pypdf.PdfReader(str(source)).get_fields() or {}), {"orphan_field"})
            self.patcher().patch(source, target, [])

            result = pypdf.PdfReader(str(target))
            catalog: Any = result.trailer["/Root"]
            page: Any = result.pages[0]
            self.assertNotIn("/AcroForm", catalog)
            self.assertEqual(page["/Annots"][0].get_object()["/Subtype"], "/Link")

    def test_lifts_parent_field_additional_actions_with_widget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.drawString(20, 160, "Original")
            doc.acroForm.textfield(name="customer_name", x=20, y=100, width=120, height=20)
            doc.save()

            source_reader = pypdf.PdfReader(str(source))
            source_writer = pypdf.PdfWriter(clone_from=source_reader)
            page: Any = source_writer.pages[0]
            widget_reference = page.raw_get("/Annots")[0]
            widget = widget_reference.get_object()
            action = DictionaryObject({
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("event.change = event.change.toUpperCase();"),
            })
            parent = DictionaryObject({
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/T"): TextStringObject("customer_name"),
                NameObject("/Kids"): ArrayObject([widget_reference]),
                NameObject("/AA"): DictionaryObject({NameObject("/K"): action}),
            })
            parent_reference = source_writer._add_object(parent)  # pylint: disable=protected-access
            widget[NameObject("/Parent")] = parent_reference
            acroform: Any = source_writer._root_object[NameObject("/AcroForm")]  # pylint: disable=protected-access
            acroform[NameObject("/Fields")] = ArrayObject([parent_reference])
            with source.open("wb") as output:
                source_writer.write(output)

            self.patcher().patch(source, target, [])

            result = pypdf.PdfReader(str(target))
            result_catalog: Any = result.trailer["/Root"]
            result_acroform: Any = result_catalog["/AcroForm"]
            parent_result = result_acroform["/Fields"][0].get_object()
            self.assertEqual(parent_result["/AA"]["/K"]["/S"], "/JavaScript")
            self.assertIn("toUpperCase", parent_result["/AA"]["/K"]["/JS"])


class TestGhostscriptVisualBaseCompiler(unittest.TestCase):
    def test_reports_a_missing_explicit_executable(self):
        compiler = GhostscriptVisualBaseCompiler("definitely-not-a-ghostscript-command")
        with self.assertRaisesRegex(RuntimeError, "Ghostscript executable is not available"):
            compiler.compile(Path("source.pdf"), Path("target.pdf"))

    @unittest.skipUnless(which("gs"), "requires local Ghostscript")
    def test_removes_visible_and_invisible_source_text_without_rasterizing_page(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.pdf"
            target = root / "target.pdf"
            doc = canvas.Canvas(str(source), pagesize=(200, 200))
            doc.rect(10, 10, 80, 80, fill=1)
            doc.drawString(20, 160, "Visible source text")
            hidden = doc.beginText(20, 140)
            hidden.setTextRenderMode(3)
            hidden.textOut("Invisible source text")
            doc.drawText(hidden)
            doc.save()

            PDFPatcher().patch(source, target, [])

            page: Any = pypdf.PdfReader(str(target)).pages[0]
            self.assertNotIn("Visible source text", page.extract_text())
            self.assertNotIn("Invisible source text", page.extract_text())
            self.assertEqual(len(list(page.images)), 0)
