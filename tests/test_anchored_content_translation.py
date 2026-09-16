"""AnchoredContent translation stays separate from NarrativeFlow."""

# pylint: disable=protected-access

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from xml.etree.ElementTree import parse, tostring

from tiktoken import get_encoding

from pdf_craft.common import read_xml, save_xml
from pdf_craft.craft import PDFCraft
from pdf_craft.document import PDFCraftExtraction
from pdf_craft.llm import LLM
from pdf_craft.extractor.chapter.chapter import (
    Chapter,
    SourceAsset,
    SourceTextFragment,
    StandaloneAsset,
    TextFlowItem,
    decode,
    encode,
)
from pdf_craft.transformer import (
    AnchoredContent,
    AnchoredContentTranslation,
    AnchoredContentXMLTransformer,
)
from pdf_craft.transformer.chapter_xml import ChapterXMLTransformer
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import warp_callbacks
from pdf_craft.transformer.xml_translator.xml_translator.stream_mapper import XMLStreamMapper
from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLTranslator
from tests.extraction_helpers import make_extraction


class _TemplateTranslator:
    """A deterministic XMLTranslator stand-in with real template round trips."""

    def __init__(self) -> None:
        self.sources: list[str] = []

    def translate_element(self, task, **kwargs):
        callbacks = warp_callbacks(
            interrupt_source_text_segments=kwargs["interrupt_source_text_segments"],
            interrupt_translated_text_segments=kwargs["interrupt_translated_text_segments"],
            interrupt_block_element=kwargs["interrupt_block_element"],
            on_fill_failed=None,
        )
        mapper = XMLStreamMapper(get_encoding("cl100k_base"), max_group_score=10_000)

        def translate_group(inline_segments):
            self.sources.append(
                "\n\n".join("".join(segment.text for segment in inline) for inline in inline_segments)
            )
            mappings = []
            for inline in inline_segments:
                template = inline.create_element()
                for element in template.iter():
                    if element.tag != "anchor" and element.text:
                        element.text = f"T:{element.text}"
                    if element.tag != "anchor" and element.tail:
                        element.tail = f"T:{element.tail}"
                assigned = inline.assign_attributes(template)
                mappings.append((inline.parent, list(search_text_segments(assigned))))
            return mappings

        translated = task.element
        for element, mappings in mapper.map_stream(
            elements=iter((task.element,)), callbacks=callbacks,
            map=translate_group, concurrency=1,
        ):
            translated = submit(element, task.action, mappings)
        return translated, task.payload


class _BrokenAnchorTranslator:
    def translate_element(self, task, **_kwargs):
        parent = next(node for node in task.element.iter() if node.find("anchor") is not None)
        parent.remove(parent.find("anchor"))
        return task.element, task.payload


class _AssetTranslator:
    def __init__(self) -> None:
        self.calls: list[list[AnchoredContent]] = []

    def transform_assets(
        self, assets: Sequence[AnchoredContent],
    ) -> Sequence[AnchoredContentTranslation | None]:
        self.calls.append(list(assets))
        return [
            AnchoredContentTranslation(
                [f"T:{_text(asset.asset.title)}"],
                [f"T:{_text(asset.asset.content)}"],
                [f"T:{_text(asset.asset.caption)}"],
            )
            for asset in assets
        ]


class _XMLTaskTranslator:
    def __init__(self) -> None:
        self.sources: list[str] = []

    def translate_element(self, task, **kwargs):
        self.sources.extend(
            segment.text
            for segment in kwargs["interrupt_source_text_segments"](
                search_text_segments(task.element)
            )
        )
        for element in task.element.iter():
            if element.tag != "translation-context" and element.text:
                element.text = f"T:{element.text}"
        return task.element, task.payload


class _ResponseContext:
    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = iter(responses)
        self.calls = 0
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def request(self, *_args, **_kwargs):
        self.messages.append(_args)
        self.calls += 1
        return next(self._responses)


class _ResponseRuntime:
    def __init__(self, responses: Sequence[str]) -> None:
        self.context_value = _ResponseContext(responses)

    def context(self, **_kwargs):
        return self.context_value


def _repairing_translator(fill_responses: Sequence[str]) -> tuple[XMLTranslator, _ResponseRuntime]:
    config = LLM("test", "https://example.invalid/v1", "test", "cl100k_base")
    translator = XMLTranslator(
        config, config, "English", None, False, 2, 10_000, 10_000,
    )
    translator._translate_text = lambda text: text  # type: ignore[method-assign]
    runtime = _ResponseRuntime(fill_responses)
    translator._fill_runtime = runtime  # type: ignore[assignment]
    return translator, runtime


class AnchoredContentTranslationTests(unittest.TestCase):
    def test_narrative_translation_uses_anchor_and_restores_source_asset(self):
        image = SourceAsset(
            1, "image", (20, 20, 80, 80),
            title=["Secret title"], content=["Secret image text"], caption=["Secret caption"],
            asset_hash="a" * 64,
        )
        chapter = Chapter(None, -1, [TextFlowItem("body", 0, [
            SourceTextFragment(1, 1, (1, 1, 90, 15), ["Before."]),
            image,
            SourceTextFragment(1, 2, (1, 85, 90, 99), ["After."]),
        ])])
        translator = _TemplateTranslator()

        translated = ChapterXMLTransformer(translator).transform(chapter)

        source = "\n".join(translator.sources)
        self.assertIn("Before.", source)
        self.assertIn("After.", source)
        self.assertNotIn("[anchored content]", source)
        self.assertNotIn("Secret title", source)
        self.assertNotIn("Secret image text", source)
        self.assertNotIn("Secret caption", source)

        text = translated.flow_items[0]
        assert isinstance(text, TextFlowItem)
        self.assertEqual(text.children[0].content, ["T:Before."])
        self.assertEqual(text.children[2].content, ["T:After."])
        self.assertEqual(text.children[1], image)
        self.assertNotIn("anchor", tostring(encode(translated), encoding="unicode"))

    def test_narrative_translation_rejects_lost_anchor_from_non_protocol_translator(self):
        chapter = Chapter(None, -1, [TextFlowItem("body", 0, [
            SourceTextFragment(1, 1, (1, 1, 90, 15), ["Before."]),
            SourceAsset(1, "image", (20, 20, 80, 80), asset_hash="a" * 64),
            SourceTextFragment(1, 2, (1, 85, 90, 99), ["After."]),
        ])])

        with self.assertRaisesRegex(ValueError, "anchored-content structure"):
            ChapterXMLTransformer(_BrokenAnchorTranslator()).transform(chapter)

    def test_narrative_translation_restores_multiple_assets_in_source_order(self):
        first = SourceAsset(1, "image", (10, 20, 20, 30), asset_hash="a" * 64)
        second = SourceAsset(1, "table", (30, 20, 40, 30), asset_hash="b" * 64)
        chapter = Chapter(None, -1, [TextFlowItem("body", 0, [
            SourceTextFragment(1, 1, (1, 1, 90, 10), ["One."]),
            first,
            SourceTextFragment(1, 2, (1, 31, 90, 40), ["Two."]),
            second,
            SourceTextFragment(1, 3, (1, 41, 90, 50), ["Three."]),
        ])])
        translator = _TemplateTranslator()

        translated = ChapterXMLTransformer(translator).transform(chapter)

        item = translated.flow_items[0]
        assert isinstance(item, TextFlowItem)
        self.assertEqual(
            [type(child) for child in item.children],
            [SourceTextFragment, SourceAsset, SourceTextFragment, SourceAsset, SourceTextFragment],
        )
        self.assertEqual(item.children[1], first)
        self.assertEqual(item.children[3], second)
        self.assertNotIn("[anchored content]", "\n".join(translator.sources))

    def test_narrative_translation_hides_standalone_asset_text_without_an_anchor(self):
        table = SourceAsset(
            1, "table", (1, 30, 90, 80), title=["Table title"],
            content=["Table cells"], caption=["Table caption"], asset_hash="e" * 64,
        )
        chapter = Chapter(None, -1, [
            TextFlowItem("body", 0, [SourceTextFragment(1, 1, (1, 1, 90, 20), ["Narrative."])]),
            StandaloneAsset(table),
        ])
        translator = _TemplateTranslator()

        translated = ChapterXMLTransformer(translator).transform(chapter)

        source = "\n".join(translator.sources)
        self.assertIn("Narrative.", source)
        self.assertNotIn("[anchored content]", source)
        self.assertNotIn("Table title", source)
        self.assertNotIn("Table cells", source)
        self.assertNotIn("Table caption", source)
        self.assertEqual(translated.flow_items[1], StandaloneAsset(table))

    def test_anchor_protocol_retries_missing_duplicate_renamed_and_reordered_tokens(self):
        chapter = Chapter(None, -1, [TextFlowItem("body", 0, [
            SourceTextFragment(1, 1, (1, 1, 90, 15), ["Before."]),
            SourceAsset(1, "image", (20, 20, 40, 40), asset_hash="a" * 64),
            SourceTextFragment(1, 2, (1, 45, 90, 60), ["Middle."]),
            SourceAsset(1, "table", (20, 65, 40, 80), asset_hash="b" * 64),
            SourceTextFragment(1, 3, (1, 85, 90, 99), ["After."]),
        ])])
        valid = (
            '<xml><fragment id="1">Before.</fragment><anchor anchor_key="0"/>'
            '<fragment id="2">Middle.</fragment><anchor anchor_key="1"/>'
            '<fragment id="3">After.</fragment></xml>'
        )
        invalid_responses = (
            '<xml><fragment id="1">Before.</fragment><fragment id="2">Middle.</fragment>'
            '<anchor anchor_key="1"/><fragment id="3">After.</fragment></xml>',
            '<xml><fragment id="1">Before.</fragment><anchor anchor_key="0"/>'
            '<anchor anchor_key="0"/><fragment id="2">Middle.</fragment>'
            '<anchor anchor_key="1"/><fragment id="3">After.</fragment></xml>',
            '<xml><fragment id="1">Before.</fragment><marker anchor_key="0"/>'
            '<fragment id="2">Middle.</fragment><anchor anchor_key="1"/>'
            '<fragment id="3">After.</fragment></xml>',
            '<xml><fragment id="1">Before.</fragment><anchor anchor_key="1"/>'
            '<fragment id="2">Middle.</fragment><anchor anchor_key="0"/>'
            '<fragment id="3">After.</fragment></xml>',
            '<xml><fragment id="1">Before.</fragment><fragment id="2">Middle.</fragment>'
            '<fragment id="3">After.</fragment><anchor anchor_key="0"/>'
            '<anchor anchor_key="1"/></xml>',
        )

        for invalid in invalid_responses:
            with self.subTest(invalid=invalid):
                translator, runtime = _repairing_translator((invalid, valid))
                translated = ChapterXMLTransformer(cast(Any, translator)).transform(chapter)
                self.assertEqual(runtime.context_value.calls, 2)
                fill_request = runtime.context_value.messages[0][0][1].message
                self.assertIn('<anchor anchor_key="0"/>', fill_request)
                self.assertIn('<anchor anchor_key="1"/>', fill_request)
                self.assertNotIn("[anchored content]", fill_request)
                self.assertEqual(translated, chapter)

    def test_independent_stage_translates_asset_fields_and_records_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (100, 100)})
            image = SourceAsset(
                1, "image", (20, 20, 60, 60), title=["Image title"],
                content=["Image OCR"], caption=["Image caption"], asset_hash="a" * 64,
            )
            table = SourceAsset(
                1, "table", (1, 65, 90, 95), title=["Table title"],
                content=["Table cell"], caption=["Table caption"], asset_hash="b" * 64,
            )
            (source_root / "assets" / f"{image.asset_hash}.png").write_bytes(b"image")
            (source_root / "assets" / f"{table.asset_hash}.png").write_bytes(b"table")
            chapter = Chapter(7, 0, [
                TextFlowItem("heading", 0, [SourceTextFragment(1, 0, (1, 1, 90, 10), ["Chapter"])]),
                TextFlowItem("body", 0, [
                    SourceTextFragment(1, 1, (1, 12, 90, 19), ["Before image."]),
                    image,
                    SourceTextFragment(1, 2, (1, 61, 90, 64), ["After image."]),
                ]),
                StandaloneAsset(table),
            ])
            save_xml(encode(chapter), source_root / "chapters/chapter_7.xml")
            translator = _AssetTranslator()

            translated = PDFCraft().translate_anchored_contents(
                source, root / "translated.pcex", translator,
            )

            self.assertEqual(len(translator.calls), 1)
            payloads = translator.calls[0]
            self.assertEqual([payload.identity for payload in payloads], [("7", 1, 1), ("7", 2, -1)])
            self.assertIn("Before image.", payloads[0].context)
            self.assertIn("After image.", payloads[0].context)
            with translated._materialize() as paths:
                result = decode(read_xml(paths.chapters / "chapter_7.xml"))
                embedded = result.flow_items[1]
                assert isinstance(embedded, TextFlowItem)
                embedded_asset = embedded.children[1]
                assert isinstance(embedded_asset, SourceAsset)
                self.assertEqual(embedded_asset.title, ["T:Image title"])
                self.assertEqual(embedded_asset.caption, ["T:Image caption"])
                standalone = result.flow_items[2]
                assert isinstance(standalone, StandaloneAsset)
                self.assertEqual(standalone.asset.content, ["T:Table cell"])
                coverage = parse(paths.translation).getroot()
                self.assertEqual(
                    [
                        (entry.get("chapter_id"), entry.get("flow_index"), entry.get("child_index"), entry.get("state"))
                        for entry in coverage.findall("anchored/asset")
                    ],
                    [("7", "1", "1", "translated"), ("7", "2", "-1", "translated")],
                )
            translated.validate()

    def test_xml_adapter_keeps_context_transient_and_maps_all_asset_fields(self):
        source = SourceAsset(
            1, "image", (1, 1, 10, 10), title=["Title"], content=["Content"], caption=["Caption"],
        )
        translator = _XMLTaskTranslator()
        result = AnchoredContentXMLTransformer(translator).transform_assets((
            AnchoredContent("head", 0, 1, source, "Nearby narrative."),
        ))

        self.assertEqual(result[0], AnchoredContentTranslation(["T:Title"], ["T:Content"], ["T:Caption"]))
        self.assertIn("Nearby narrative.", "\n".join(translator.sources))

    def test_asset_without_extracted_text_is_preserved_without_a_translation_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            source = make_extraction(source_root, page_pixel_sizes={1: (100, 100)})
            image = SourceAsset(1, "image", (1, 1, 20, 20), asset_hash="c" * 64)
            (source_root / "assets" / f"{image.asset_hash}.png").write_bytes(b"image")
            save_xml(
                encode(Chapter(None, -1, [StandaloneAsset(image)])),
                source_root / "chapters/chapter_head.xml",
            )
            translator = _AssetTranslator()

            translated = PDFCraft().translate_anchored_contents(
                source, root / "translated.pcex", translator,
            )

            self.assertEqual(translator.calls, [])
            with translated._materialize() as paths:
                entry = parse(paths.translation).find("anchored/asset")
                assert entry is not None
                self.assertEqual(entry.get("state"), "preserved")

    def test_coverage_rejects_an_unknown_asset_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            make_extraction(source_root, page_pixel_sizes={1: (100, 100)})
            image = SourceAsset(1, "image", (1, 1, 20, 20), asset_hash="d" * 64)
            (source_root / "assets" / f"{image.asset_hash}.png").write_bytes(b"image")
            save_xml(
                encode(Chapter(None, -1, [StandaloneAsset(image)])),
                source_root / "chapters/chapter_head.xml",
            )
            (source_root / "translation.xml").write_text(
                "<translation><anchored><asset chapter_id='head' flow_index='9' "
                "child_index='-1' state='translated'/></anchored></translation>",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid anchored asset"):
                PDFCraftExtraction._from_workspace(source_root).validate()


def _text(content) -> str:
    return "".join(value for value in content if isinstance(value, str))


if __name__ == "__main__":
    unittest.main()
