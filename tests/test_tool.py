import json
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
import tempfile
from unittest.mock import patch

from pdf_craft_tool.cli import _page_indexes, _parser, _run_matrix, _smoke_exit_code, _work_dir
from pdf_craft_tool.paths import create_run_directory
from pdf_craft_tool.runtime import create_llm_from_env, ocr_mode_from_env


class TestPDFCraftTool(unittest.TestCase):
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
            "smoke", "run", "--asset", "citation.pdf", "--route", "package-markdown",
            "--translation-llm-profile", "translation", "--fill-llm-profile", "fill",
        ])
        self.assertEqual(args.route, "package-markdown")
        self.assertEqual(args.translation_llm_profile, "translation")
        self.assertEqual(args.fill_llm_profile, "fill")

    def test_smoke_dry_run_does_not_require_project_env(self):
        args = _parser().parse_args([
            "smoke", "run", "--asset", "citation.pdf", "--route", "markdown",
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
            with self.assertRaises(FileExistsError):
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
