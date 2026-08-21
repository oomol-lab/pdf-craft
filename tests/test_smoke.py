import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pdf_craft.document import DocumentPackage
from pdf_craft.extractor import PDFExtractor
from pdf_craft.smoke.assets import discover_assets
from pdf_craft.smoke.checks import check_epub
from pdf_craft.smoke.runner import SmokeRun, expand_matrix, run_smoke


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
