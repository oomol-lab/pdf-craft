import unittest
from unittest.mock import patch

from pdf_craft import LocalDeepSeekOCRConfig
from pdf_craft import functions


class TestConvenienceFunctions(unittest.TestCase):
    def test_transform_markdown_defaults_do_not_assume_toc_pages(self):
        with patch.object(functions, "Transform") as transform_cls:
            functions.transform_markdown(
                pdf_path="input.pdf",
                markdown_path="output.md",
            )

        transform_cls.return_value.transform_markdown.assert_called_once()
        _, kwargs = transform_cls.return_value.transform_markdown.call_args
        self.assertFalse(kwargs["toc_assumed"])

    def test_transform_markdown_preserves_legacy_positional_parameters(self):
        with patch.object(functions, "Transform") as transform_cls:
            functions.transform_markdown(
                "input.pdf",
                "output.md",
                None,
                None,
                None,
                "gundam",
                None,
                False,
                300,
        )

        _, transform_kwargs = transform_cls.call_args
        self.assertIsNone(transform_kwargs["ocr"])
        _, method_kwargs = transform_cls.return_value.transform_markdown.call_args
        self.assertEqual(method_kwargs["dpi"], 300)

    def test_transform_markdown_accepts_ocr_as_last_keyword(self):
        config = LocalDeepSeekOCRConfig()
        with patch.object(functions, "Transform") as transform_cls:
            functions.transform_markdown(
                pdf_path="input.pdf",
                markdown_path="output.md",
                ocr=config,
            )

        _, transform_kwargs = transform_cls.call_args
        self.assertIs(transform_kwargs["ocr"], config)

    def test_transform_epub_defaults_assume_toc_pages(self):
        with patch.object(functions, "Transform") as transform_cls:
            functions.transform_epub(
                pdf_path="input.pdf",
                epub_path="output.epub",
            )

        transform_cls.return_value.transform_epub.assert_called_once()
        _, kwargs = transform_cls.return_value.transform_epub.call_args
        self.assertTrue(kwargs["toc_assumed"])

    def test_transform_epub_preserves_legacy_positional_parameters(self):
        with patch.object(functions, "Transform") as transform_cls:
            functions.transform_epub(
                "input.pdf",
                "output.epub",
                None,
                None,
                "gundam",
                None,
                False,
                300,
        )

        _, transform_kwargs = transform_cls.call_args
        self.assertIsNone(transform_kwargs["ocr"])
        _, method_kwargs = transform_cls.return_value.transform_epub.call_args
        self.assertEqual(method_kwargs["dpi"], 300)


if __name__ == "__main__":
    unittest.main()
