import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.convert_pdf import _page_indexes
from scripts.runtime import create_translation_llm_from_env


class TestManualScripts(unittest.TestCase):
    def test_page_indexes_are_explicitly_one_based(self):
        self.assertEqual(_page_indexes("1, 2,3"), (1, 2, 3))
        self.assertIsNone(_page_indexes(None))
        with self.assertRaisesRegex(SystemExit, "1-based"):
            _page_indexes("0,1")

    def test_translation_config_requires_dedicated_text_llm(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "PDF_CRAFT_TRANSLATION_API_KEY"):
                create_translation_llm_from_env(cache_path=Path("cache"), log_dir_path=Path("logs"))
