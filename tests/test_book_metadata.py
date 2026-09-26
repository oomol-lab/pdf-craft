# pylint: disable=protected-access

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from pdf_craft.common import save_xml
from pdf_craft.document import DocumentAuthor, DocumentMetadata, PDFCraftExtraction
from pdf_craft.document.package import write_manifest, write_pages
from pdf_craft.extractor.metadata import (
    _looks_like_isbn,
    extract_book_metadata_from_ocr,
    merge_ocr_and_pdf_metadata,
)
from pdf_craft.pdf import PDFDocumentMetadata
from pdf_craft.pdf.types import Page, PageLayout, encode
from pdf_craft.transform import PDFExtractionEngine


class _ScriptedLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = []

    def request(self, *, input):
        self.calls.append(input)
        return self.responses.pop(0)


def _complete(title: str, page_index: int, evidence: str) -> str:
    return json.dumps({
        "action": "complete",
        "metadata": {
            "title": {"value": title, "page_index": page_index, "evidence": evidence},
            "publisher": {"value": "Example Press", "page_index": 4, "evidence": "Example Press"},
            "isbn": {"value": "978-1-4028-9462-6", "page_index": 5, "evidence": "ISBN 978-1-4028-9462-6"},
            "authors": [{
                "name": {"value": "Ada Author", "page_index": 1, "evidence": "Ada Author"},
                "original_name": None,
                "nationality": None,
            }],
        },
    })


def _isbn_complete(isbn: str) -> str:
    return json.dumps({
        "action": "complete",
        "metadata": {
            "isbn": {"value": isbn, "page_index": 1, "evidence": f"ISBN {isbn}"},
        },
    })


def _title_complete(title: str, evidence: str) -> str:
    return json.dumps({
        "action": "complete",
        "metadata": {
            "title": {"value": title, "page_index": 1, "evidence": evidence},
        },
    })


class TestBookMetadata(unittest.TestCase):
    def test_disabled_metadata_does_not_read_native_pdf_metadata(self):
        class _OCR:
            def metadata(self, _pdf_path):
                raise AssertionError("native metadata must stay unread when disabled")

        engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
        engine._ocr = _OCR()  # type: ignore[assignment]

        result = engine._extract_book_metadata(  # pylint: disable=protected-access
            pdf_path=Path("book.pdf"), pages_path=Path("ocr"), enabled=False, metadata_llm=None,
        )

        self.assertIsNone(result)

    def test_enabled_metadata_requires_its_explicit_llm_before_ocr_starts(self):
        engine = PDFExtractionEngine.__new__(PDFExtractionEngine)

        with self.assertRaisesRegex(ValueError, "requires metadata_llm"):
            engine._extract_from_pdf(  # pylint: disable=protected-access
                pdf_path=Path("book.pdf"),
                analysing_path=Path("analysis"),
                ocr_size="base",
                dpi=None,
                max_page_image_file_size=None,
                includes_cover=False,
                includes_footnotes=False,
                ignore_pdf_errors=False,
                ignore_ocr_errors=False,
                generate_plot=False,
                toc_llm=None,
                toc_assumed=False,
                aborted=lambda: False,
                max_tokens=None,
                max_output_tokens=None,
                on_ocr_event=lambda _event: None,
                extract_book_metadata=True,
                metadata_llm=None,
            )

    def test_reads_three_pages_then_appends_requested_pages(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", [
                "A Book\nAda Author", "contents", "preface", "Example Press", "ISBN 978-1-4028-9462-6",
            ])
            llm = _ScriptedLLM([
                '{"action":"read_more","page_count":2}',
                _complete("A Book", 1, "A Book"),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.title, "A Book")
            self.assertEqual(metadata.publisher, "Example Press")
            self.assertEqual(metadata.isbn, "978-1-4028-9462-6")
            self.assertEqual(metadata.authors, (DocumentAuthor("Ada Author"),))
            self.assertEqual(len(llm.calls), 2)
            self.assertIn("preface", llm.calls[0][-1].message)
            self.assertNotIn("Example Press", llm.calls[0][-1].message)
            self.assertIn("Example Press", llm.calls[1][-1].message)

    def test_business_validation_reenters_guaranteed_repair_loop(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", ["A Book", "", ""])
            llm = _ScriptedLLM([
                _complete("A Book", 1, "missing from page"),
                json.dumps({
                    "action": "complete",
                    "metadata": {"title": {"value": "A Book", "page_index": 1, "evidence": "A Book"}},
                }),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.title, "A Book")
            self.assertEqual(len(llm.calls), 2)
            self.assertIn("business validation error", llm.calls[1][-1].message)
            self.assertEqual(llm.calls[1][-2].role.name, "ASSISTANT")

    def test_string_page_count_is_rejected_by_strict_schema_and_repaired(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", ["A Book", "", ""])
            llm = _ScriptedLLM([
                '{"action":"read_more","page_count":"2"}',
                _title_complete("A Book", "A Book"),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.title, "A Book")
            self.assertEqual(len(llm.calls), 2)
            self.assertIn("page_count", llm.calls[1][-1].message)
            self.assertEqual(llm.calls[1][-2].role.name, "ASSISTANT")

    def test_evidence_rejects_case_and_internal_whitespace_rewrites(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", ["PDF Craft", "", ""])
            llm = _ScriptedLLM([
                _title_complete("pdfcraft", "PDF Craft"),
                _title_complete("PDF  Craft", "PDF Craft"),
                _title_complete("PDF Craft", "PDF Craft"),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.title, "PDF Craft")
            self.assertEqual(len(llm.calls), 3)
            self.assertIn("not supported by its evidence", llm.calls[1][-1].message)
            self.assertIn("not supported by its evidence", llm.calls[2][-1].message)

    def test_invalid_isbn_checksum_reenters_repair_loop_and_accepts_fix(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", [
                "ISBN 978-1-4028-9462-0\nISBN 978-1-4028-9462-6", "", "",
            ])
            llm = _ScriptedLLM([
                _isbn_complete("978-1-4028-9462-0"),
                _isbn_complete("978-1-4028-9462-6"),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.isbn, "978-1-4028-9462-6")
            self.assertEqual(len(llm.calls), 2)
            self.assertIn("valid ISBN-10 or ISBN-13 checksum", llm.calls[1][-1].message)

    def test_isbn_validation_accepts_both_formats_and_rejects_bad_check_digits(self):
        self.assertTrue(_looks_like_isbn("0-8044-2957-X"))
        self.assertFalse(_looks_like_isbn("0-8044-2957-0"))
        self.assertTrue(_looks_like_isbn("978-1-4028-9462-6"))
        self.assertFalse(_looks_like_isbn("978-1-4028-9462-0"))

    def test_exhausted_invalid_isbn_uses_safe_pdf_metadata_fallback(self):
        class _OCR:
            @staticmethod
            def metadata(_pdf_path):
                return PDFDocumentMetadata(
                    title="Fallback Title", description=None, publisher=None,
                    isbn="978-1-4028-9462-6", authors=[], editors=[], translators=[],
                    modified=datetime.now(timezone.utc),
                )

        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", [
                "ISBN 978-1-4028-9462-0", "", "",
            ])
            engine = PDFExtractionEngine.__new__(PDFExtractionEngine)
            engine._ocr = _OCR()  # type: ignore[assignment]
            llm = _ScriptedLLM([_isbn_complete("978-1-4028-9462-0")] * 4)

            metadata = engine._extract_book_metadata(  # pylint: disable=protected-access
                pdf_path=Path("book.pdf"), pages_path=pages_path, enabled=True,
                metadata_llm=llm,  # type: ignore[arg-type]
            )

            assert metadata is not None
            self.assertEqual(metadata.title, "Fallback Title")
            self.assertEqual(metadata.isbn, "978-1-4028-9462-6")
            self.assertEqual(len(llm.calls), 4)

    def test_reaching_twelve_pages_forces_complete_through_repair_loop(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", [f"page {index}" for index in range(1, 15)])
            llm = _ScriptedLLM([
                '{"action":"read_more","page_count":4}',
                '{"action":"read_more","page_count":4}',
                '{"action":"read_more","page_count":4}',
                '{"action":"read_more","page_count":2}',
                json.dumps({
                    "action": "complete",
                    "metadata": {"title": {"value": "page 1", "page_index": 1, "evidence": "page 1"}},
                }),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.title, "page 1")
            self.assertEqual(len(llm.calls), 5)
            self.assertIn("all available OCR pages", llm.calls[-1][-1].message)

    def test_reaching_the_last_physical_page_forces_complete_through_repair_loop(self):
        with TemporaryDirectory() as directory:
            pages_path = self._write_pages(Path(directory) / "ocr", [
                "A Book", "contents", "preface", "copyright",
            ])
            llm = _ScriptedLLM([
                '{"action":"read_more","page_count":2}',
                '{"action":"read_more","page_count":2}',
                json.dumps({
                    "action": "complete",
                    "metadata": {"title": {"value": "A Book", "page_index": 1, "evidence": "A Book"}},
                }),
            ])

            metadata = extract_book_metadata_from_ocr(pages_path, llm)  # type: ignore[arg-type]

            self.assertEqual(metadata.title, "A Book")
            self.assertEqual(len(llm.calls), 3)
            self.assertIn("all available OCR pages", llm.calls[-1][-1].message)

    def test_ocr_values_win_and_native_values_only_fill_missing_fields(self):
        ocr = DocumentMetadata(title="Printed Title", authors=(DocumentAuthor("Printed Author"),))
        pdf = PDFDocumentMetadata(
            title="Wrong Export Title", description="PDF subject", publisher="PDF Press",
            isbn="9781402894626", authors=["Export User"], editors=["PDF Editor"],
            translators=["PDF Translator"], modified=datetime.now(timezone.utc),
        )

        metadata = merge_ocr_and_pdf_metadata(ocr, pdf)

        self.assertEqual(metadata.title, "Printed Title")
        self.assertEqual(metadata.authors, (DocumentAuthor("Printed Author"),))
        self.assertEqual(metadata.description, "PDF subject")
        self.assertEqual(metadata.publisher, "PDF Press")
        self.assertEqual(metadata.editors, ("PDF Editor",))
        self.assertIsNone(metadata.modified)

    def test_v1_pcex_remains_readable_while_new_metadata_preserves_author_details(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "chapters").mkdir(parents=True)
            (workspace / "assets").mkdir()
            write_pages(workspace, render_dpi=300, page_pixel_sizes={1: (100, 100)})
            write_manifest(workspace, document_metadata=DocumentMetadata(
                title="译本", original_title="Original", authors=(DocumentAuthor(
                    "译名", original_name="Original Name", nationality="英国",
                ),), subjects=("history",),
            ))
            extraction = PDFCraftExtraction._from_workspace(workspace)
            archive_path = root / "v2.pcex"
            extraction._export(archive_path)
            book_meta = PDFCraftExtraction._open(archive_path)._book_meta()
            assert book_meta is not None
            self.assertEqual(book_meta.authors, ["译名"])

            with ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format_version"], 4)
            self.assertEqual(manifest["document"]["authors"][0]["original_name"], "Original Name")

            manifest["format_version"] = 1
            manifest["document"] = {
                "title": "Legacy", "description": None, "publisher": None, "isbn": None,
                "authors": ["Legacy Author"], "editors": [], "translators": [],
                "modified": None, "language": None,
            }
            legacy_path = root / "v1.pcex"
            with ZipFile(archive_path) as source, ZipFile(legacy_path, "w") as target:
                for info in source.infolist():
                    content = json.dumps(manifest).encode() if info.filename == "manifest.json" else source.read(info.filename)
                    target.writestr(info, content)
            opened = PDFCraftExtraction._open(legacy_path)
            legacy_book_meta = opened._book_meta()
            assert legacy_book_meta is not None
            self.assertEqual(legacy_book_meta.authors, ["Legacy Author"])

    @staticmethod
    def _write_pages(root: Path, texts: list[str]) -> Path:
        root.mkdir()
        for index, text in enumerate(texts, start=1):
            page = Page(
                index=index,
                image=None,
                body_layouts=[PageLayout("text", (0, 0, 100, 100), text, 0, None)],
                footnotes_layouts=[],
                input_tokens=0,
                output_tokens=0,
            )
            save_xml(encode(page), root / f"page_{index}.xml")
        return root


if __name__ == "__main__":
    unittest.main()
