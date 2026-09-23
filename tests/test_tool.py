import json
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from pdf_craft import OCRTokensMetering
from pdf_craft_tool.cli import (
    _ExtractionResult,
    _page_indexes,
    _parser,
    _record_pdf_cache_owner,
    _render_package,
    _resolve_ocr_size,
    _run_matrix,
    _smoke_exit_code,
    _patch_package_pdf,
    _translate_package,
    _translate_pdf,
    _validate_ocr_size,
    _work_dir,
)
from pdf_craft_tool.paths import create_run_directory
from pdf_craft_tool.runtime import create_llm_from_env, create_ocr_config_from_env, ocr_mode_from_env


class TestPDFCraftTool(unittest.TestCase):
    def test_pdf_parser_exposes_opt_in_book_metadata_llm(self):
        args = _parser().parse_args([
            "pdf", "convert", "source.pdf", "--format", "epub",
            "--book-metadata", "--metadata-llm", "metadata",
        ])
        self.assertTrue(args.book_metadata)
        self.assertEqual(args.metadata_llm, "metadata")

    def test_furniture_is_only_an_opt_in_pdf_translation_flag(self):
        translated = _parser().parse_args([
            "pdf", "translate", "source.pdf", "zh", "--with-furniture",
        ])
        self.assertTrue(translated.with_furniture)
        self.assertFalse(hasattr(translated, "furniture"))

        package = _parser().parse_args([
            "package", "translate", "source.pcex", "zh", "--with-furniture",
        ])
        self.assertTrue(package.with_furniture)

        converted = _parser().parse_args([
            "pdf", "convert", "source.pdf", "--format", "epub",
        ])
        self.assertFalse(hasattr(converted, "with_furniture"))

    def test_pdf_translation_rejects_furniture_for_non_pdf_output(self):
        args = _parser().parse_args([
            "pdf", "translate", "source.pdf", "zh", "--format", "epub", "--with-furniture",
        ])
        with self.assertRaisesRegex(SystemExit, "only with --format pdf"):
            _translate_pdf(args)

    def test_pdf_translation_forwards_furniture_to_extraction_and_patching(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = _parser().parse_args([
                "pdf", "translate", str(root / "source.pdf"), "zh",
                "--work-dir", str(root / "work"), "--with-furniture",
            ])
            craft = Mock()
            result = _ExtractionResult(
                craft=craft,
                extraction=Mock(),
                path=root / "work" / "book.pcex",
                metering=OCRTokensMetering(0, 0),
            )
            transformer = Mock()
            with patch("pdf_craft_tool.cli._extract", return_value=result) as extract, patch(
                "pdf_craft_tool.cli._xml_transformer", return_value=transformer,
            ):
                _translate_pdf(args)

            self.assertEqual(extract.call_args.kwargs["includes_furniture"], True)
            self.assertEqual(craft.translate_pdf.call_args.kwargs["with_furniture"], True)

    def test_package_commands_open_extractions_through_craft_facade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "book.pcex"
            source = root / "source.pdf"
            handle = object()

            translate_args = _parser().parse_args([
                "package", "translate", str(package), "zh",
                "--work-dir", str(root / "translate"),
            ])
            translate_craft = Mock()
            translate_craft.open_extraction.return_value = handle
            with patch("pdf_craft_tool.cli.load_project_env"), \
                    patch("pdf_craft_tool.cli.PDFCraft", return_value=translate_craft), \
                    patch("pdf_craft_tool.cli._xml_transformer", return_value=Mock()):
                _translate_package(translate_args)
            translate_craft.open_extraction.assert_called_once_with(package)
            self.assertIs(translate_craft.translate_extraction.call_args.args[0], handle)

            render_args = _parser().parse_args([
                "package", "render", str(package), "--format", "markdown",
                "--work-dir", str(root / "render"),
            ])
            render_craft = Mock()
            render_craft.open_extraction.return_value = handle
            with patch("pdf_craft_tool.cli.PDFCraft", return_value=render_craft), \
                    patch("pdf_craft_tool.cli._render") as render:
                _render_package(render_args)
            render_craft.open_extraction.assert_called_once_with(package)
            self.assertIs(render.call_args.args[1], handle)

            patch_args = _parser().parse_args([
                "package", "patch-pdf", str(source), str(package),
                "--work-dir", str(root / "patch"),
            ])
            patch_craft = Mock()
            patch_craft.open_extraction.return_value = handle
            with patch("pdf_craft_tool.cli.PDFCraft", return_value=patch_craft):
                _patch_package_pdf(patch_args)
            patch_craft.open_extraction.assert_called_once_with(package)
            self.assertIs(patch_craft.patch_pdf_with_extraction.call_args.args[1], handle)

    def test_smoke_exit_code_rejects_failed_and_skipped_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for status, expected in (("passed", 0), ("planned", 0), ("failed", 1), ("skipped", 1)):
                with self.subTest(status=status):
                    path = root / status
                    path.mkdir()
                    (path / "checks.json").write_text(json.dumps({"status": status}), encoding="utf-8")
                    self.assertEqual(_smoke_exit_code(path), expected)

    def test_smoke_exit_code_rejects_missing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(_smoke_exit_code(Path(directory)), 1)

    def test_smoke_parser_exposes_package_render_and_translation_profiles(self):
        args = _parser().parse_args([
            "smoke", "run", "--asset", "pdf/citation.pdf", "--route", "package-markdown",
            "--translation-llm-profile", "translation", "--fill-llm-profile", "fill",
        ])
        self.assertEqual(args.route, "package-markdown")
        self.assertEqual(args.translation_llm_profile, "translation")
        self.assertEqual(args.fill_llm_profile, "fill")

    def test_smoke_dry_run_does_not_require_project_env(self):
        args = _parser().parse_args([
            "smoke", "run", "--asset", "pdf/citation.pdf", "--route", "markdown",
            "--dry-run",
        ])
        with patch("pdf_craft_tool.cli.load_project_env") as load_env:
            from pdf_craft_tool.cli import _run_smoke
            _run_smoke(args)
        load_env.assert_not_called()

    def test_run_directories_use_a_date_and_shared_daily_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 8, 22, 9, 30)
            first = create_run_directory(root, "citation-convert", now=now)
            second = create_run_directory(root, "citation-translate", now=now)
            self.assertEqual(first.name, "citation-convert-20260822-001")
            self.assertEqual(second.name, "citation-translate-20260822-002")

    def test_explicit_work_directory_is_created_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chosen-output"
            self.assertEqual(_work_dir(Path("citation.pdf"), path, "convert"), path)
            self.assertEqual(_work_dir(Path("citation.pdf"), path, "convert"), path)

    def test_explicit_work_directory_rejects_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chosen-output"
            path.write_text("not a directory", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "not a directory"):
                _work_dir(Path("citation.pdf"), path, "convert")

    def test_page_indexes_are_explicitly_one_based(self):
        self.assertEqual(_page_indexes("1, 2,3"), (1, 2, 3))
        self.assertIsNone(_page_indexes(None))
        with self.assertRaisesRegex(SystemExit, "1-based"):
            _page_indexes("0,1")

    def test_openai_llm_profile_requires_its_own_credentials(self):
        with patch.dict("os.environ", {"PDF_CRAFT_LLM_CUSTOM_PROVIDER": "openai"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "PDF_CRAFT_LLM_CUSTOM_API_KEY"):
                create_llm_from_env("custom", cache_path=Path("cache"), log_dir_path=Path("logs"))

    def test_ocr_mode_uses_the_prefixed_runtime_variable(self):
        with patch.dict("os.environ", {"PDF_CRAFT_OCR_MODE": "unlimited-ocr-vendor"}, clear=True):
            self.assertEqual(ocr_mode_from_env(), "unlimited-ocr-vendor")

    def test_each_local_backend_reads_its_own_environment_namespace(self):
        cases = (
            (
                "deepseek-ocr-local",
                "PDF_CRAFT_DEEPSEEK_OCR",
                "DeepSeekOCRLocalConfig",
            ),
            (
                "deepseek-ocr2-local",
                "PDF_CRAFT_DEEPSEEK_OCR2",
                "DeepSeekOCR2LocalConfig",
            ),
            (
                "unlimited-ocr-local",
                "PDF_CRAFT_UNLIMITED_OCR",
                "UnlimitedOCRLocalConfig",
            ),
        )
        for mode, prefix, expected_type in cases:
            with self.subTest(mode=mode), patch.dict("os.environ", {
                "PDF_CRAFT_OCR_MODE": mode,
                f"{prefix}_LOCAL_MODELS_CACHE_PATH": f"/{mode}",
                f"{prefix}_LOCAL_ONLY": "false",
                f"{prefix}_LOCAL_ENABLE_DEVICES_NUMBERS": "1, 3",
            }, clear=True):
                config = create_ocr_config_from_env()
                self.assertEqual(type(config).__name__, expected_type)
                self.assertEqual(getattr(config, "models_cache_path"), Path(f"/{mode}").resolve())
                self.assertFalse(getattr(config, "local_only"))
                self.assertEqual(getattr(config, "enable_devices_numbers"), (1, 3))

    def test_local_backend_selection_keeps_legacy_shared_settings_as_fallback(self):
        with patch.dict("os.environ", {
            "PDF_CRAFT_OCR_MODE": "deepseek-ocr2-local",
            "PDF_CRAFT_DEEPSEEK_MODELS_CACHE_PATH": "/legacy-cache",
            "PDF_CRAFT_DEEPSEEK_LOCAL_ONLY": "false",
        }, clear=True):
            config = create_ocr_config_from_env()
        self.assertEqual(getattr(config, "models_cache_path"), Path("/legacy-cache").resolve())
        self.assertFalse(getattr(config, "local_only"))

    def test_deepseek_ocr2_local_defaults_to_base_when_ocr_size_is_omitted(self):
        self.assertEqual(_resolve_ocr_size(None, "deepseek-ocr2-local", "tiny"), "base")
        self.assertEqual(_resolve_ocr_size(None, "deepseek-ocr-local", "tiny"), "tiny")
        self.assertEqual(_resolve_ocr_size("tiny", "deepseek-ocr2-local", "base"), "tiny")

    def test_deepseek_ocr2_local_rejects_tiny_with_clear_error(self):
        with self.assertRaisesRegex(SystemExit, "deepseek-ocr2-local.*tiny.*base"):
            _validate_ocr_size("deepseek-ocr2-local", "tiny")
        _validate_ocr_size("deepseek-ocr2-local", "base")

    def test_pdf_work_directory_records_and_validates_cache_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF source")
            other = root / "other.pdf"
            other.write_bytes(b"%PDF other")
            work_dir = root / "work"
            work_dir.mkdir()
            args = Namespace(
                source=source,
                dpi=None,
                max_page_image_file_size=None,
                footnotes=False,
                max_ocr_tokens=None,
                max_ocr_output_tokens=None,
            )

            _record_pdf_cache_owner(work_dir, args, "deepseek-ocr-local", "tiny")
            _record_pdf_cache_owner(work_dir, args, "deepseek-ocr-local", "tiny")

            changed_source = Namespace(**(args.__dict__ | {"source": other}))
            with self.assertRaisesRegex(SystemExit, "different PDF/OCR settings.*source"):
                _record_pdf_cache_owner(work_dir, changed_source, "deepseek-ocr-local", "tiny")
            with self.assertRaisesRegex(SystemExit, "different PDF/OCR settings.*ocr_mode"):
                _record_pdf_cache_owner(work_dir, args, "unlimited-ocr-local", "tiny")

    def test_pdf_work_directory_rejects_legacy_ocr_cache_without_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF source")
            work_dir = root / "work"
            (work_dir / "analysis" / "ocr").mkdir(parents=True)
            args = Namespace(
                source=source,
                dpi=None,
                max_page_image_file_size=None,
                footnotes=False,
                max_ocr_tokens=None,
                max_ocr_output_tokens=None,
            )

            with self.assertRaisesRegex(SystemExit, "legacy OCR cache"):
                _record_pdf_cache_owner(work_dir, args, "deepseek-ocr-local", "tiny")

    def test_each_vendor_backend_requires_only_its_own_environment_namespace(self):
        cases = (
            (
                "deepseek-ocr-vendor",
                {
                    "PDF_CRAFT_DEEPSEEK_OCR_BASE_URL": "https://ocr.example/v1",
                    "PDF_CRAFT_DEEPSEEK_OCR_API_KEY": "key",
                    "PDF_CRAFT_DEEPSEEK_OCR_MODEL": "ocr",
                },
                "DeepSeekOCRVendorConfig",
            ),
            (
                "deepseek-ocr2-vendor",
                {
                    "PDF_CRAFT_DEEPSEEK_OCR2_BASE_URL": "https://ocr2.example/v1",
                    "PDF_CRAFT_DEEPSEEK_OCR2_API_KEY": "key",
                    "PDF_CRAFT_DEEPSEEK_OCR2_MODEL": "ocr2",
                },
                "DeepSeekOCR2VendorConfig",
            ),
            (
                "unlimited-ocr-vendor",
                {
                    "PDF_CRAFT_UNLIMITED_OCR_ACCESS_KEY": "ak",
                    "PDF_CRAFT_UNLIMITED_OCR_SECRET_KEY": "sk",
                },
                "UnlimitedOCRVendorConfig",
            ),
        )
        for mode, values, expected_type in cases:
            with self.subTest(mode=mode), patch.dict("os.environ", {
                "PDF_CRAFT_OCR_MODE": mode,
            } | values, clear=True):
                self.assertEqual(type(create_ocr_config_from_env()).__name__, expected_type)

    def test_matrix_records_missing_profile_as_skipped_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "matrix.json"
            config.write_text(json.dumps({"runs": [{
                "asset": "epub/Cambridge.epub",
                "route": "epub-translate",
                "translation": {"llm_profile": "custom"},
            }]}), encoding="utf-8")

            with patch("pdf_craft_tool.cli.load_project_env"), \
                    patch("pdf_craft_tool.cli.llm_values_from_env",
                          side_effect=SystemExit("missing LLM profile")):
                exit_code = _run_matrix(Namespace(
                    config=config,
                    assets_root=Path("tests/assets"),
                    output_root=root / "output",
                    dry_run=True,
                ))

            run_path = next((root / "output").iterdir())
            checks = json.loads((run_path / "checks.json").read_text(encoding="utf-8"))
            self.assertEqual(checks["status"], "skipped")
            self.assertEqual(checks["errors"], ["missing LLM profile"])
            self.assertEqual(exit_code, 1)
