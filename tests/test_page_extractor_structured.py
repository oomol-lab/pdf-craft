import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from PIL import Image

from pdf_craft.common import AssetHub
from pdf_craft.pdf.page_extractor import PageExtractorNode
from pdf_craft.ocr_config import LocalDeepSeekOCRConfig


class _Kind:
    def __init__(self, value: str) -> None:
        self.value = value


class _Block:
    def __init__(
        self,
        kind: str,
        det: tuple[int, int, int, int],
        text: str | None = None,
        html: str | None = None,
        children: list["_Block"] | None = None,
    ) -> None:
        self.kind = _Kind(kind)
        self.det = det
        self.text = text
        self.html = html
        self.children = children or []


class _Structured:
    def __init__(self, blocks: list[_Block]) -> None:
        self.blocks = blocks


class TestStructuredPageMapping(unittest.TestCase):
    def test_asset_block_includes_structured_caption(self):
        node = PageExtractorNode(LocalDeepSeekOCRConfig())
        image = Image.new("RGB", (100, 100), "white")
        structured = _Structured(
            blocks=[
                _Block(
                    kind="image",
                    det=(10, 10, 80, 80),
                    text="",
                    children=[
                        _Block(
                            kind="image_caption",
                            det=(10, 82, 80, 94),
                            text="Figure 1. Caption",
                        )
                    ],
                )
            ]
        )

        with TemporaryDirectory() as temp_dir:
            layouts = list(
                node._iter_page_layouts(  # pylint: disable=protected-access
                    image=image,
                    structured=structured,
                    asset_hub=AssetHub(Path(temp_dir)),
                    stage_index=1,
                    body_layouts=[],
                    footnotes_layouts=[],
                )
            )

        self.assertEqual(len(layouts), 1)
        self.assertEqual(layouts[0].ref, "image")
        self.assertEqual(layouts[0].text, "Figure 1. Caption")
        self.assertIsNotNone(layouts[0].hash)

    def test_stage_two_asset_does_not_create_asset_file(self):
        node = PageExtractorNode(LocalDeepSeekOCRConfig())
        image = Image.new("RGB", (100, 100), "white")
        structured = _Structured(
            blocks=[
                _Block(
                    kind="image",
                    det=(10, 10, 80, 80),
                    text="Discarded stage two image",
                )
            ]
        )

        with TemporaryDirectory() as temp_dir:
            asset_path = Path(temp_dir)
            layouts = list(
                node._iter_page_layouts(  # pylint: disable=protected-access
                    image=image,
                    structured=structured,
                    asset_hub=AssetHub(asset_path),
                    stage_index=2,
                    body_layouts=[],
                    footnotes_layouts=[],
                )
            )

            self.assertEqual(layouts, [])
            self.assertEqual(list(asset_path.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
