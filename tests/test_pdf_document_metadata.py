# pylint: disable=protected-access

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from typing import cast

import pypdf
from reportlab.pdfgen import canvas

from pdf_craft import PDFCraft
from pdf_craft.document import DocumentAuthor, DocumentMetadata, PDFCraftExtraction
from pdf_craft.document.package import write_manifest
from pdf_craft.pipeline.pdf import PDFPatcher

from tests.extraction_helpers import make_extraction


class TestPDFDocumentMetadata(unittest.TestCase):
    def test_patch_pdf_with_extraction_writes_every_populated_pcex_field(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            target = root / "target.pdf"
            self._write_source(source, title="Incorrect export title")
            extraction = self._extraction_with_metadata(root / "extraction")

            PDFCraft().patch_pdf_with_extraction(source, extraction, target)

            metadata = pypdf.PdfReader(str(target)).metadata
            assert metadata is not None
            self.assertEqual(metadata.title, "Printed Title")
            self.assertEqual(metadata.author, "Translated Author")
            self.assertEqual(metadata.subject, "Printed description")
            self.assertEqual(metadata.get("/Keywords"), "history; mathematics")
            self.assertEqual(
                json.loads(cast(str, metadata["/PDFCraftDocumentMetadata"])), extraction.document_metadata(),
            )

    def test_translate_pdf_carries_translated_pcex_metadata_into_output(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            target = root / "target.pdf"
            self._write_source(source, title="Incorrect export title")
            extraction = self._extraction_with_metadata(root / "extraction")
            craft = PDFCraft()

            with patch.object(craft, "translate_extraction", return_value=extraction):
                craft.translate_pdf(source, extraction, target, _IdentityTransformer())

            metadata = pypdf.PdfReader(str(target)).metadata
            assert metadata is not None
            self.assertEqual(metadata.title, "Printed Title")
            self.assertEqual(
                json.loads(cast(str, metadata["/PDFCraftDocumentMetadata"])), extraction.document_metadata(),
            )

    def test_overlay_patch_keeps_pcex_metadata(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            target = root / "target.pdf"
            self._write_source(source, title="Incorrect export title")

            PDFPatcher(visual_base_compiler=_BlankVisualBaseCompiler()).patch(
                source,
                target,
                [_replacement()],
                document_metadata={
                    "/Title": "Printed Title",
                    "/PDFCraftDocumentMetadata": '{"title":"Printed Title"}',
                },
            )

            metadata = pypdf.PdfReader(str(target)).metadata
            assert metadata is not None
            self.assertEqual(metadata.title, "Printed Title")
            self.assertEqual(metadata["/PDFCraftDocumentMetadata"], '{"title":"Printed Title"}')

    def test_empty_pcex_metadata_keeps_identity_copy_without_replacements(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            target = root / "target.pdf"
            self._write_source(source, title="Original title")

            PDFPatcher().patch(source, target, [], document_metadata={})

            self.assertEqual(target.read_bytes(), source.read_bytes())

    @staticmethod
    def _write_source(path: Path, *, title: str) -> None:
        document = canvas.Canvas(str(path), pagesize=(100, 100))
        document.setTitle(title)
        document.drawString(5, 50, "Original")
        document.save()

    @staticmethod
    def _extraction_with_metadata(root: Path) -> PDFCraftExtraction:
        make_extraction(root, page_pixel_sizes={1: (100, 100)})
        write_manifest(root, document_metadata=DocumentMetadata(
            title="Printed Title",
            original_title="Original Title",
            description="Printed description",
            publisher="Printed Press",
            isbn="978-1-4028-9462-6",
            authors=(DocumentAuthor(
                "Translated Author", original_name="Original Author", nationality="British",
            ),),
            editors=("Editor",),
            translators=("Translator",),
            publication_date="2024-01-01",
            edition="Second edition",
            subjects=("history", "mathematics"),
            rights="All rights reserved",
            language="en",
        ))
        return PDFCraftExtraction._from_workspace(root).validate()


class _IdentityTransformer:
    def transform(self, chapter):
        return chapter


class _BlankVisualBaseCompiler:
    def compile(self, source_path: Path, target_path: Path) -> None:
        reader = pypdf.PdfReader(str(source_path))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_blank_page(float(page.mediabox.width), float(page.mediabox.height))
        with target_path.open("wb") as output:
            writer.write(output)


def _replacement():
    from pdf_craft.pipeline.pdf import PDFReplacement
    return PDFReplacement(1, (5, 5, 95, 40), "Translated", (100, 100))
