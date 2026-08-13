import unittest
from unittest.mock import patch

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

    def test_transform_epub_defaults_assume_toc_pages(self):
        with patch.object(functions, "Transform") as transform_cls:
            functions.transform_epub(
                pdf_path="input.pdf",
                epub_path="output.epub",
            )

        transform_cls.return_value.transform_epub.assert_called_once()
        _, kwargs = transform_cls.return_value.transform_epub.call_args
        self.assertTrue(kwargs["toc_assumed"])


if __name__ == "__main__":
    unittest.main()
