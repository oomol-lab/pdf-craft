"""Static guards for the independent erasure and paragraph-fill layers."""

import ast
from pathlib import Path
import unittest


_PDF_PIPELINE = Path(__file__).parents[1] / "pdf_craft" / "pipeline" / "pdf"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestPDFLayerBoundaries(unittest.TestCase):
    def test_text_filler_has_no_image_or_eraser_dependency(self):
        imports = _imports(_PDF_PIPELINE / "text_layout.py")

        self.assertFalse(any(name.startswith("PIL") for name in imports))
        self.assertNotIn("eraser", imports)

    def test_eraser_has_no_paragraph_or_translation_dependency(self):
        path = _PDF_PIPELINE / "eraser.py"
        imports = _imports(path)
        source = path.read_text()

        self.assertFalse(any(name.startswith("pdf_craft.extractor") for name in imports))
        self.assertFalse(any(name.startswith("pdf_craft.transformer") for name in imports))
        self.assertNotIn("ParagraphLayout", source)
        self.assertNotIn("Translation", source)
