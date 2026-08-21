import unittest
from pathlib import Path

from pdf_craft.pipeline.epub import translate_epub
from pdf_craft.transformer import XMLTranslator


class TestModuleBoundaries(unittest.TestCase):
    def test_epub_orchestration_is_owned_by_pipeline(self):
        self.assertTrue(translate_epub.__module__.startswith("pdf_craft.pipeline.epub"))

    def test_xml_translator_is_format_agnostic(self):
        self.assertTrue(XMLTranslator.__module__.startswith("pdf_craft.transformer.xml_translator"))
        root = Path(__file__).parents[1] / "pdf_craft" / "transformer" / "xml_translator"
        sources = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        self.assertNotIn("pipeline.epub", sources)
        self.assertNotIn("Zip(", sources)
        self.assertNotIn("search_spine_paths", sources)
