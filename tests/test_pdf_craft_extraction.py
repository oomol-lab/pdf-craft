# pylint: disable=protected-access

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from epub_generator import BookMeta

from pdf_craft.craft import PDFCraft
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.extractor.chapter.chapter import Chapter
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import encode
from tests.extraction_helpers import make_extraction


class _Identity:
    def transform(self, chapter: Chapter) -> Chapter:
        return chapter


def _replace_archive_members(
    source_path: Path,
    target_path: Path,
    replacements: dict[str, bytes],
) -> None:
    with ZipFile(source_path) as source, ZipFile(target_path, "w") as target:
        for info in source.infolist():
            content = replacements.get(info.filename, source.read(info.filename))
            target.writestr(info, content)


class TestPDFCraftExtraction(unittest.TestCase):
    def test_archive_remains_usable_after_analysis_workspace_is_gone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "book.pcex"
            with tempfile.TemporaryDirectory() as analysis_directory:
                extraction_root = Path(analysis_directory) / "extraction"
                extraction = make_extraction(extraction_root)
                save_xml(
                    encode(Chapter(None, -1, [])),
                    extraction_root / "chapters/chapter_head.xml",
                )
                extraction.export(archive_path)

            opened = PDFCraftExtraction.open(archive_path)
            opened.validate()
            self.assertEqual(opened.page_pixel_sizes(), {1: (100, 100)})

    def test_archive_round_trip_has_only_standard_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modified = datetime(2025, 1, 2, tzinfo=timezone.utc)
            extraction = make_extraction(
                root / "workspace",
                page_pixel_sizes={1: (1200, 1800), 2: (900, 1400)},
                render_dpi=240,
                with_toc=True,
                book_meta=BookMeta(
                    title="A book", authors=["Author"], translators=["Translator"],
                    modified=modified,
                ),
                language="en",
            )
            save_xml(encode(Chapter(None, -1, [])), root / "workspace/chapters/chapter_head.xml")
            (root / "workspace/cover.png").write_bytes(b"cover")
            asset_name = "a" * 64 + ".png"
            (root / "workspace/assets" / asset_name).write_bytes(b"asset")
            archive_path = root / "book.pcex"

            opened = extraction.export(archive_path)

            self.assertEqual(opened.page_pixel_sizes(), {1: (1200, 1800), 2: (900, 1400)})
            self.assertEqual(PDFCraftExtraction.load(archive_path).render_dpi(), 240)
            self.assertEqual(opened.render_dpi(), 240)
            self.assertEqual(opened.language(), "en")
            metadata = opened.book_meta()
            assert metadata is not None
            self.assertEqual(metadata.title, "A book")
            self.assertEqual(metadata.authors, ["Author"])
            self.assertEqual(metadata.modified, modified)
            with ZipFile(archive_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "manifest.json", "pages.xml", "toc.xml", "cover.png",
                        "chapters/", "chapters/chapter_head.xml",
                        "assets/", f"assets/{asset_name}",
                    },
                )

    def test_folder_and_non_pcex_paths_are_not_public_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_extraction(root / "workspace")
            with self.assertRaisesRegex(ValueError, "\\.pcex"):
                PDFCraftExtraction.open(root / "workspace")
            (root / "book.zip").write_bytes(b"not a zip")
            with self.assertRaisesRegex(ValueError, "\\.pcex"):
                PDFCraftExtraction.open(root / "book.zip")

    def test_corrupt_and_unsafe_archives_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "corrupt.pcex"
            corrupt.write_bytes(b"not a zip")
            with self.assertRaisesRegex(ValueError, "invalid or corrupt"):
                PDFCraftExtraction.open(corrupt)

            unsafe = root / "unsafe.pcex"
            with ZipFile(unsafe, "w") as archive:
                archive.writestr("../manifest.json", "{}")
            with self.assertRaisesRegex(ValueError, "unsafe"):
                PDFCraftExtraction.open(unsafe)

    def test_missing_component_and_unsupported_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "workspace")
            extraction.export(root / "valid.pcex")

            missing = root / "missing.pcex"
            with ZipFile(root / "valid.pcex") as source, ZipFile(missing, "w") as target:
                for info in source.infolist():
                    if info.filename != "pages.xml":
                        target.writestr(info, source.read(info.filename))
            with self.assertRaisesRegex(ValueError, "pages.xml"):
                PDFCraftExtraction.open(missing)

            unsupported = root / "unsupported.pcex"
            with ZipFile(root / "valid.pcex") as source, ZipFile(unsupported, "w") as target:
                for info in source.infolist():
                    content = source.read(info.filename)
                    if info.filename == "manifest.json":
                        payload = json.loads(content)
                        payload["format_version"] = 999
                        content = json.dumps(payload).encode()
                    target.writestr(info, content)
            with self.assertRaisesRegex(ValueError, "format version"):
                PDFCraftExtraction.open(unsupported)

    def test_invalid_manifest_json_and_incomplete_document_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "workspace")
            valid = root / "valid.pcex"
            extraction.export(valid)

            invalid_json = root / "invalid-json.pcex"
            _replace_archive_members(
                valid,
                invalid_json,
                {"manifest.json": b"{"},
            )
            with self.assertRaisesRegex(ValueError, "invalid PDFCraftExtraction manifest.json"):
                PDFCraftExtraction.open(invalid_json)

            incomplete = root / "incomplete-document.pcex"
            with ZipFile(valid) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            manifest["document"] = {}
            _replace_archive_members(
                valid,
                incomplete,
                {"manifest.json": json.dumps(manifest).encode()},
            )
            with self.assertRaisesRegex(ValueError, "invalid document metadata"):
                PDFCraftExtraction.open(incomplete)

    def test_invalid_chapter_xml_and_schema_are_rejected_when_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            extraction = make_extraction(workspace)
            save_xml(encode(Chapter(None, -1, [])), workspace / "chapters/chapter_head.xml")
            valid = root / "valid.pcex"
            extraction.export(valid)

            cases = {
                "malformed": (b"<chapter>", "invalid PDFCraftExtraction XML"),
                "missing-body": (b"<chapter/>", "invalid chapter schema"),
            }
            for name, (chapter_xml, error_pattern) in cases.items():
                with self.subTest(name=name):
                    invalid = root / f"{name}.pcex"
                    _replace_archive_members(
                        valid,
                        invalid,
                        {"chapters/chapter_head.xml": chapter_xml},
                    )
                    with self.assertRaisesRegex(ValueError, error_pattern):
                        PDFCraftExtraction.open(invalid)

    def test_invalid_toc_xml_is_rejected_when_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extraction = make_extraction(root / "workspace", with_toc=True)
            valid = root / "valid.pcex"
            extraction.export(valid)
            invalid = root / "invalid-toc.pcex"
            _replace_archive_members(valid, invalid, {"toc.xml": b"<toc>"})

            with self.assertRaisesRegex(ValueError, "invalid PDFCraftExtraction XML: toc.xml"):
                PDFCraftExtraction.open(invalid)

    def test_noncanonical_asset_hash_cannot_escape_assets_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            extraction = make_extraction(workspace)
            save_xml(encode(Chapter(None, -1, [])), workspace / "chapters/chapter_head.xml")
            (workspace / "cover.png").write_bytes(b"cover")
            valid = root / "valid.pcex"
            extraction.export(valid)
            invalid = root / "invalid-hash.pcex"
            chapter_xml = (
                b'<chapter><body><asset ref="image" page_index="1" det="0,0,1,1" '
                b'hash="../cover"/></body></chapter>'
            )
            _replace_archive_members(
                valid,
                invalid,
                {"chapters/chapter_head.xml": chapter_xml},
            )

            with self.assertRaisesRegex(ValueError, "invalid asset hash"):
                PDFCraftExtraction.open(invalid)

    def test_translation_preserves_manifest_pages_toc_cover_and_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_workspace = root / "source"
            source = make_extraction(
                source_workspace,
                page_pixel_sizes={1: (20, 30)},
                with_toc=True,
                book_meta=BookMeta(title="Metadata"),
                language="zh",
            )
            save_xml(encode(Chapter(None, -1, [])), source_workspace / "chapters/chapter_head.xml")
            (source_workspace / "cover.png").write_bytes(b"cover")
            asset_name = "b" * 64 + ".png"
            (source_workspace / "assets" / asset_name).write_bytes(b"asset")

            translated = PDFCraft().translate_extraction(
                source, root / "translated.pcex", _Identity()
            )

            with source._materialize() as original, translated._materialize() as result:
                for name in ("manifest.json", "pages.xml", "toc.xml", "cover.png"):
                    self.assertEqual((original.root / name).read_bytes(),
                                     (result.root / name).read_bytes())
                self.assertEqual((original.assets / asset_name).read_bytes(),
                                 (result.assets / asset_name).read_bytes())
