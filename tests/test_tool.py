import unittest
from datetime import datetime
from pathlib import Path
import tempfile
from unittest.mock import patch

from pdf_craft_tool.cli import _page_indexes, _work_dir
from pdf_craft_tool.paths import create_run_directory
from pdf_craft_tool.runtime import create_llm_from_env, ocr_mode_from_env


class TestPDFCraftTool(unittest.TestCase):
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
