"""Deterministic EPUB metadata updates around XML translation."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

from pdf_craft import ConcurrentExecutor, FixedCapacity, LLM
from pdf_craft.pipeline.epub.translation.translator import translate
from pdf_craft.transformer import SubmitKind


class TestEpubTranslationMetadata(unittest.TestCase):
    def test_translation_updates_language_ncx_title_and_xhtml_title(self):
        async def translate_tasks(_translator, *, tasks, **_kwargs):
            results = []
            for task in tasks:
                for element in task.element.iter():
                    if element.text and element.text.strip():
                        element.text = f"译:{element.text}"
                results.append((task.element, task.payload))
            return results

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "translated.epub"
            llm = LLM("unused", "https://example.invalid/v1", "unused", "o200k_base")
            executor = ConcurrentExecutor(FixedCapacity(2))
            with patch(
                "pdf_craft.pipeline.epub.translation.translator.XMLTranslator.translate_elements",
                new=translate_tasks,
            ):
                asyncio.run(translate(
                    "tests/assets/epub/Cambridge.epub",
                    target,
                    "zh",
                    SubmitKind.REPLACE,
                    llm=llm,
                    executor=executor,
                    window=2,
                ))

            with ZipFile(target) as archive:
                opf = fromstring(archive.read("OEBPS/content.opf"))
                ncx = fromstring(archive.read("OEBPS/toc.ncx"))
                chapter = fromstring(archive.read("OEBPS/Text/chapter_05.xhtml"))

            self.assertEqual(opf.findtext(".//{*}language"), "zh")
            translated_title = opf.findtext(".//{*}title")
            self.assertIsNotNone(translated_title)
            assert translated_title is not None
            self.assertTrue(translated_title.startswith("译:"))
            self.assertEqual(ncx.findtext(".//{*}docTitle/{*}text"), translated_title)
            self.assertTrue((chapter.findtext(".//{*}head/{*}title") or "").startswith("译:"))


if __name__ == "__main__":
    unittest.main()
