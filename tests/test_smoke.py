import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

from pdf_craft.document import DocumentPackage
from pdf_craft.extractor import PDFExtractor
from pdf_craft.common import save_xml
from pdf_craft.markdown.render import render_markdown_file
from pdf_craft.metering import OCRTokensMetering
from pdf_craft.ocr_config import OCRConfig
from pdf_craft.pdf import OCREvent, OCREventKind
from pdf_craft_tool.smoke.assets import discover_assets
from pdf_craft_tool.smoke.checks import check_epub, check_markdown, check_pdf_patch_geometry
from pdf_craft_tool.smoke.runner import (
    SmokeAsset,
    SmokeRun,
    _redact,
    _epub_contains_marker,
    _run_pdf,
    expand_matrix,
    run_smoke,
)
from pdf_craft.sequence.chapter import AssetLayout, BlockLayout, Chapter, ParagraphLayout, encode

encode_chapter = encode


class _CaptureTransform:
    def __init__(self):
        self.kwargs = None

    def extract_package(self, *, analysing_path, **kwargs):
        self.kwargs = kwargs
        (analysing_path / "chapters").mkdir(parents=True)
        (analysing_path / "assets").mkdir()
        (analysing_path / "toc.xml").write_text("<toc/>")
        DocumentPackage.from_path(analysing_path).write_metadata(page_pixel_sizes={1: (1, 1)})
        return None, None, None, None, None


class TestSmokeMatrix(unittest.TestCase):
    def test_discovers_pdf_and_migrated_epub_assets(self):
        assets = discover_assets(Path("tests/assets"))
        self.assertIn("double_column.pdf", {asset.name for asset in assets})
        self.assertIn("epub/Cambridge.epub", {asset.name for asset in assets})

    def test_expands_config_without_fixed_profiles(self):
        runs = expand_matrix(
            {"defaults": {"page_indexes": [1], "ocr_size": "tiny"}, "runs": [
                {"asset": "double_column.pdf", "route": "markdown", "backend": "deepseek-ocr-local"},
                {"asset": "epub/Cambridge.epub", "route": "epub-check"},
            ]},
            Path("tests/assets"),
        )
        self.assertEqual(runs[0].page_indexes, (1,))
        self.assertEqual(runs[1].backend, None)

    def test_dry_run_writes_isolated_plan_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = run_smoke(
                SmokeRun("double_column.pdf", "package", "deepseek-ocr-vendor", ocr={"api_key": "secret"}),
                assets_root=Path("tests/assets"), output_root=root, dry_run=True,
            )
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "planned")
            self.assertEqual(manifest["run"]["ocr"]["api_key"], "[redacted]")
            self.assertTrue((run_path / "checks.json").exists())
            self.assertTrue((run_path / "logs").is_dir())
            self.assertRegex(run_path.name, r"^double_column-package-\d{8}-\d{3}$")

    def test_redact_covers_nested_vendor_credentials_without_hiding_limits(self):
        value = {
            "deepseek": {"api_key": "key", "max_tokens": 1200},
            "unlimited": [{"ak": "access", "sk": "secret", "max_ocr_tokens": 900}],
            "nested": {"access_key": "access", "secret_key": "secret", "password": "pw"},
        }
        redacted = _redact(value)
        self.assertEqual(redacted["deepseek"]["api_key"], "[redacted]")
        self.assertEqual(redacted["unlimited"][0]["ak"], "[redacted]")
        self.assertEqual(redacted["unlimited"][0]["sk"], "[redacted]")
        self.assertEqual(redacted["nested"]["access_key"], "[redacted]")
        self.assertEqual(redacted["nested"]["secret_key"], "[redacted]")
        self.assertEqual(redacted["nested"]["password"], "[redacted]")
        self.assertEqual(_redact({"cache_path": Path("models-cache")})["cache_path"], str(Path("models-cache")))
        self.assertEqual(redacted["deepseek"]["max_tokens"], 1200)
        self.assertEqual(redacted["unlimited"][0]["max_ocr_tokens"], 900)

    def test_markdown_assets_are_copied_and_resolved_from_book_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = _package_with_image(root / "package")
            markdown = root / "output" / "book.md"
            render_markdown_file(package.chapters_path, package.assets_path, markdown,
                                 Path("assets"), package.cover_path, lambda: False)
            self.assertTrue((root / "output" / "assets" / "image.png").is_file())
            self.assertTrue((root / "output" / "assets" / "cover.png").is_file())
            self.assertIn("![](assets/image.png)", markdown.read_text())
            self.assertEqual(check_markdown(markdown), [])

    def test_markdown_absolute_asset_destination_has_resolvable_relative_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = _package_with_image(root / "package")
            markdown = root / "output" / "book.md"
            destination = root / "external-assets"
            render_markdown_file(package.chapters_path, package.assets_path, markdown,
                                 destination, None, lambda: False)
            self.assertTrue((destination / "image.png").is_file())
            self.assertIn("![](../external-assets/image.png)", markdown.read_text())
            self.assertEqual(check_markdown(markdown), [])

    def test_markdown_without_resources_still_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = _package_without_assets(root / "package")
            markdown = root / "output" / "book.md"
            render_markdown_file(package.chapters_path, package.assets_path, markdown,
                                 Path("assets"), None, lambda: False)
            self.assertEqual(check_markdown(markdown), [])

    def test_markdown_check_skips_urls_and_protocol_relative_links(self):
        with tempfile.TemporaryDirectory() as directory:
            markdown = Path(directory) / "book.md"
            markdown.write_text(
                "![](mailto:reader@example.com)\n![](ftp://example.com/image.png)\n"
                "![](//cdn.example.com/image.png)\n![](assets/missing.png)\n",
                encoding="utf-8",
            )
            self.assertEqual(check_markdown(markdown), ["Markdown image reference is missing: assets/missing.png"])

    def test_pdf_run_records_ocr_events_and_stage_timeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = _package_without_assets(root / "fixture-package")
            metering = OCRTokensMetering(5, 7)
            with patch("pdf_craft_tool.smoke.runner.PDFCraft") as craft_class:
                craft = craft_class.return_value
                def extract(_source, _path, options):
                    options.on_ocr_event(OCREvent(OCREventKind.COMPLETE, 1, 1, 12, 5, 7))
                    return package, metering
                craft.extract_pdf_with_metering.side_effect = extract
                run_path = run_smoke(
                    SmokeRun("double_column.pdf", "package", "deepseek-ocr-local", ocr={}),
                    assets_root=Path("tests/assets"), output_root=root,
                )
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "passed")
            self.assertEqual([item["stage"] for item in manifest["timeline"]],
                             ["configure", "extract", "render", "check", "finish"])
            self.assertEqual(manifest["ocr_events"], [{"kind": "complete", "page_index": 1,
                "total_pages": 1, "cost_time_ms": 12, "input_tokens": 5, "output_tokens": 7}])

    def test_failed_configuration_records_stage_and_traceback_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("pdf_craft_tool.smoke.runner.create_ocr_config", side_effect=ValueError("bad OCR")):
                run_path = run_smoke(
                    SmokeRun("double_column.pdf", "package", "deepseek-ocr-vendor", ocr={"api_key": "secret"}),
                    assets_root=Path("tests/assets"), output_root=Path(directory),
                )
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(manifest["failure"]["stage"], "configure")
            self.assertEqual(manifest["failure"]["exception_type"], "ValueError")
            self.assertEqual(manifest["failure"]["traceback_path"], "logs/traceback.txt")
            self.assertTrue((run_path / "logs" / "traceback.txt").is_file())

    def test_persisted_errors_redact_vendor_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = {"api_key": "api-secret", "ak": "access-secret", "sk": "signing-secret"}
            with patch("pdf_craft_tool.smoke.runner.create_ocr_config"), \
                 patch("pdf_craft_tool.smoke.runner.PDFCraft") as craft_class:
                craft = craft_class.return_value

                def extract(_source, _path, options):
                    options.on_ocr_event(OCREvent(
                        OCREventKind.FAILED, 1, 1,
                        error=ValueError("OCR rejected api-secret and access-secret"),
                    ))
                    raise ValueError("request failed with signing-secret")

                craft.extract_pdf_with_metering.side_effect = extract
                run_path = run_smoke(
                    SmokeRun("double_column.pdf", "package", "deepseek-ocr-vendor", ocr=secrets),
                    assets_root=Path("tests/assets"), output_root=Path(directory),
                )
            persisted = "\n".join((run_path / name).read_text() for name in (
                "manifest.json", "checks.json", "logs/traceback.txt",
            ))
            self.assertNotIn("api-secret", persisted)
            self.assertNotIn("access-secret", persisted)
            self.assertNotIn("signing-secret", persisted)
            self.assertIn("[redacted]", persisted)

    def test_epub_check_copies_and_validates_real_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = run_smoke(
                SmokeRun("epub/Cambridge.epub", "epub-check"),
                assets_root=Path("tests/assets"), output_root=Path(directory),
            )
            checks = json.loads((run_path / "checks.json").read_text())
            self.assertEqual(checks["status"], "passed")

    def test_epub_check_validates_epub3_navigation_fixture(self):
        self.assertEqual(check_epub(Path("tests/assets/epub/DeepSeek OCR.epub")), [])

    def test_epub_marker_check_rejects_renderer_output_without_translation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_epub(Path(directory), {"chapter.xhtml": "<p>original</p>"})
            self.assertFalse(_epub_contains_marker(path, "[translated]"))

    def test_epub_marker_check_accepts_xhtml_and_html_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("chapter.xhtml", "appendix.html"):
                with self.subTest(name=name):
                    path = _write_epub(root, {name: "<p>[translated]</p>"})
                    self.assertTrue(_epub_contains_marker(path, "[translated]"))

    def test_epub_check_rejects_malformed_container(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_epub(Path(directory), {"META-INF/container.xml": "<container>"})
            self.assertTrue(any("container.xml" in error for error in check_epub(path)))

    def test_epub_check_rejects_broken_spine_and_toc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_epub(Path(directory), {
                "META-INF/container.xml": _container(),
                "OEBPS/content.opf": _opf('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>', '<itemref idref="missing"/>', 'toc="ncx"'),
                "OEBPS/toc.ncx": '<ncx><navMap><navPoint><content src="missing.xhtml"/></navPoint></navMap></ncx>',
            })
            errors = check_epub(path)
            self.assertTrue(any("spine references missing" in error for error in errors))
            self.assertTrue(any("TOC link references missing" in error for error in errors))

    def test_epub_check_rejects_malformed_spine_xhtml(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_epub(Path(directory), {
                "META-INF/container.xml": _container(),
                "OEBPS/content.opf": _opf('<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>', '<itemref idref="chapter"/>', 'toc="ncx"'),
                "OEBPS/chapter.xhtml": '<html><body>',
                "OEBPS/toc.ncx": '<ncx><navMap><navPoint><content src="chapter.xhtml"/></navPoint></navMap></ncx>',
            })
            self.assertTrue(any("spine XHTML" in error for error in check_epub(path)))

    def test_page_indexes_are_forwarded_to_public_extractor(self):
        with tempfile.TemporaryDirectory() as directory:
            transform = _CaptureTransform()
            PDFExtractor(transform).extract(Path("source.pdf"), Path(directory), page_indexes=(2, 4))
            kwargs = transform.kwargs
            assert kwargs is not None
            self.assertEqual(kwargs["page_indexes"], (2, 4))

    def test_pdf_patch_geometry_rejects_partial_page_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = DocumentPackage.from_path(root)
            package.chapters_path.mkdir()
            package.assets_path.mkdir()
            package.write_metadata(page_pixel_sizes={1: (100, 100)})
            chapter = Chapter(None, -1, [ParagraphLayout("text", 0, [
                BlockLayout(2, 1, (1, 1, 20, 20), ["will be replaced"])
            ])])
            with patch("pdf_craft_tool.smoke.checks.create_chapters_reader", return_value=lambda: iter([chapter])):
                errors = check_pdf_patch_geometry(package)
            self.assertEqual(errors, ["PDF patch geometry missing for replacement pages: [2]"])

    def test_pdf_route_skips_when_local_ocr_has_no_cuda_device(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package").mkdir()
            (root / "output").mkdir()

            class UnavailableCraft:
                def extract_pdf_with_metering(self, *_args, **_kwargs):
                    try:
                        raise RuntimeError("No CUDA devices available")
                    except RuntimeError as cause:
                        raise ValueError("OCR extraction failed") from cause

            run = SmokeRun("double_column.pdf", "markdown")
            asset = SmokeAsset("double_column.pdf", "pdf", Path("source.pdf"))
            with patch("pdf_craft_tool.smoke.runner.PDFCraft", return_value=UnavailableCraft()):
                status, errors, details = _run_pdf(run, asset, root, cast(OCRConfig, None))
            self.assertEqual(status, "skipped")
            self.assertEqual(
                errors,
                ["OCR backend unavailable: local OCR requires CUDA, but no CUDA device is available"],
            )
            self.assertEqual(details["package"], str(root / "package"))

    def test_markdown_route_inserts_deterministic_package_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "output").mkdir()
            package_path = root / "package"
            package_path.joinpath("chapters").mkdir(parents=True)
            package_path.joinpath("assets").mkdir()
            package_path.joinpath("toc.xml").write_text("<toc />")
            package = DocumentPackage.from_path(package_path)
            package.write_metadata(page_pixel_sizes={1: (10, 10)})
            chapter = Chapter(None, -1, [ParagraphLayout("text", 0, [
                BlockLayout(1, 1, (1, 1, 2, 2), ["original"])
            ])])
            save_xml(encode_chapter(chapter), package_path / "chapters" / "chapter_1.xml")

            run = SmokeRun(
                "double_column.pdf", "markdown",
                translation={"package_marker": "[translated]", "package_submit": "APPEND_BLOCK"},
            )
            asset = SmokeAsset("double_column.pdf", "pdf", Path("source.pdf"))
            from pdf_craft.craft import PDFCraft
            craft = PDFCraft()
            with patch("pdf_craft_tool.smoke.runner.PDFCraft", return_value=craft), \
                    patch.object(craft, "extract_pdf_with_metering", return_value=(
                        package, OCRTokensMetering(0, 0)
                    )):
                status, errors, details = _run_pdf(run, asset, root, cast(OCRConfig, None))
            self.assertEqual(status, "passed", errors)
            rendered = (root / "output" / "book.md").read_text()
            self.assertIn("original", rendered)
            self.assertIn("original[translated]", rendered)
            self.assertEqual(details["outputs"], [str(root / "output" / "book.md")])


def _write_epub(path: Path, files: dict[str, str]) -> Path:
    epub = path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        for name, content in files.items():
            archive.writestr(name, content)
    return epub


def _container() -> str:
    return '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'


def _opf(manifest: str, spine: str, spine_attrs: str) -> str:
    return f'<package version="2.0"><manifest>{manifest}</manifest><spine {spine_attrs}>{spine}</spine></package>'


def _package_with_image(root: Path) -> DocumentPackage:
    package = _package_without_assets(root)
    (package.assets_path / "image.png").write_bytes(b"image")
    cover = root / "cover.png"
    cover.write_bytes(b"cover")
    package = DocumentPackage(package.chapters_path, package.assets_path, None, cover, package.metadata_path)
    chapter = Chapter(None, -1, [AssetLayout(1, "image", (0, 0, 1, 1), [], [], [], "image")])
    save_xml(encode(chapter), package.chapters_path / "chapter_head.xml")
    return package


def _package_without_assets(root: Path) -> DocumentPackage:
    package = DocumentPackage.from_path(root)
    package.chapters_path.mkdir(parents=True)
    package.assets_path.mkdir()
    chapter = Chapter(None, -1, [ParagraphLayout("text", 0, [BlockLayout(1, 1, (0, 0, 1, 1), ["content"])])])
    save_xml(encode(chapter), package.chapters_path / "chapter_head.xml")
    return package
