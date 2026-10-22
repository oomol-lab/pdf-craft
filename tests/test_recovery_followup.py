"""Task completion and furniture recovery through real format adapters."""
# pylint: disable=protected-access,unused-argument,cell-var-from-loop

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch
from zipfile import ZipFile

from pdf_craft import AllFallbackError, AsyncPDFCraft, ErrorCallbackError, FailureCategory, PageGeometry, XMLTranslator
from pdf_craft._failure_sources import segment_locations
from pdf_craft._policy import recover
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import Chapter, encode
from pdf_craft.transformer.furniture import FurnitureSection
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.package import FurnitureExtractionTransformer
from tests.extraction_helpers import make_extraction
from tests.test_epub_optional_cfi import book, mappings, options
from tests.test_policies import translator


async def partial_translation(runtime, inline_segments, **kwargs):
    result = []
    for segment in inline_segments:
        if "Fail" in "".join(part.text for part in segment):
            await recover(ValueError("mock translation failure"), FailureCategory.TRANSLATION,
                          "text_translation", "omit_translation", targets=segment_locations([segment]))
            result.append(None)
        else:
            result.extend(await mappings(runtime, [segment], **kwargs))
    return result


class EpubCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_body_heading_success_cannot_hide_all_failed_paragraphs(self):
        for heading in (
            "<h1>Heading</h1>", "<h2>Heading</h2>", "<h3>Heading</h3>",
            "<h4>Heading</h4>", "<h5>Heading</h5>", "<h6>Heading</h6>",
            "<h1><span>Heading</span></h1>", '<div role="heading"><span>Heading</span></div>',
        ):
            with self.subTest(heading=heading), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, output = root / "source.epub", root / "output.epub"
                book(source, [("c.xhtml", "application/xhtml+xml",
                               f'<html><head><title>Title</title></head><body>{heading}'
                               '<p>Fail one.</p><p>Fail two.</p></body></html>')])
                output.write_bytes(b"previous output")
                with patch.object(XMLTranslator, "_translate_inline_segments_async", new=partial_translation):
                    with self.assertRaises(AllFallbackError):
                        await AsyncPDFCraft().translate_epub(source, output, **options(), error_policy=True)
                self.assertEqual(output.read_bytes(), b"previous output")

    async def test_partial_success_and_heading_only_or_empty_bodies_are_not_all_fallback(self):
        for body in (
            "<h1>Heading</h1><p>Fail one.</p><p>Good body.</p>",
            "<h1>Fail heading.</h1><p>Good body.</p>",
            "<h1>Heading only</h1>", "",
        ):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, output = root / "source.epub", root / "output.epub"
                book(source, [("c.html", "text/html", f'<html><body>{body}</body></html>')])
                with patch.object(XMLTranslator, "_translate_inline_segments_async", new=partial_translation):
                    await AsyncPDFCraft().translate_epub(source, output, **options(), error_policy=True)
                with ZipFile(output) as archive:
                    rendered = archive.read("EPUB/c.html").decode()
                if "Good body" in body:
                    self.assertIn("译:Good body.", rendered)
                if "Fail one" in body:
                    self.assertIn("Fail one.", rendered)
                    self.assertNotIn("译:Fail one.", rendered)


def furniture_source(root: Path, *, template=False):
    source = make_extraction(root, page_pixel_sizes={7: (600, 900), 8: (600, 900)}, render_dpi=144)
    save_xml(encode(Chapter(None, -1, [])), root / "chapters/chapter_head.xml")
    if template:
        content = (
            "<patterns><pattern id='1' kind='universal'>"
            "<position id='2' folio_style='D' folio_offset='0' folio_prefix='Page '/>"
            "</pattern></patterns><pages><page index='7'>"
            "<section det='10,20,300,50'><association pattern_id='1' position_id='2' kind='universal'/></section>"
            "</page><page index='8'>"
            "<section det='30,40,350,70'><association pattern_id='1' position_id='2' kind='universal'/></section>"
            "</page></pages>"
        )
    else:
        content = ("<patterns/><pages><page index='7'><section det='10,20,300,50'>Left heading</section>"
                   "<section det='30,60,350,90'>Right heading</section></page></pages>")
    (root / "furnitures.xml").write_text(f"<furnitures>{content}</furnitures>", encoding="utf-8")
    return source._export(root.with_suffix(".pcex"))


class FurnitureRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_batch_retains_geometry_through_core_and_outer_recovery(self):
        for in_core in (True, False):
            for asynchronous in (True, False):
                with self.subTest(in_core=in_core, asynchronous=asynchronous), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    source = furniture_source(root / "source")
                    seen = []
                    if in_core:
                        value = translator()
                        value._translate_text_async = AsyncMock(side_effect=RuntimeError("provider failure"))
                        adapter = FurnitureXMLTransformer(value)
                    else:
                        adapter = Mock()
                        adapter.transform_sections = AsyncMock(side_effect=RuntimeError("custom transformer failure"))

                    def decide(failure):
                        seen.append(failure)
                        return True

                    async def decide_async(failure):
                        await asyncio.sleep(0)
                        return decide(failure)

                    result = await FurnitureExtractionTransformer(cast(Any, adapter)).transform(
                        source, root / "output.pcex", error_policy=decide_async if asynchronous else decide,
                    )
                    result._validate()
                    self.assertEqual(len(seen), 1)
                    event = seen[0]
                    self.assertEqual(event.category, FailureCategory.TRANSLATION if in_core else FailureCategory.FILL)
                    self.assertEqual(event.affected, event.targets)
                    self.assertEqual(len(event.targets), 2)
                    for target, box in zip(event.targets, ((10, 20, 300, 50), (30, 60, 350, 90)), strict=True):
                        self.assertEqual(target.page_indexes, (7,))
                        self.assertEqual(target.page, PageGeometry(7, 600, 900, 144))
                        self.assertEqual(target.bbox, box)
                        self.assertEqual(target.member_path, "furnitures.xml")
                        self.assertTrue(target.source)
                        self.assertIn("section:7:", target.unit_id)
                    self.assertNotEqual(event.targets[0].unit_id, event.targets[1].unit_id)

    async def test_direct_page_batch_does_not_invent_missing_geometry(self):
        value = translator()
        value._translate_text_async = AsyncMock(side_effect=RuntimeError("provider failure"))
        seen = []
        result = await FurnitureXMLTransformer(value).transform_sections(
            7, [FurnitureSection(7, (10, 20, 300, 50), "Header")],
            error_policy=lambda failure: seen.append(failure) or True,
        )
        self.assertEqual(result, (None,))
        self.assertEqual(seen[0].targets[0].page_indexes, (7,))
        self.assertEqual(seen[0].targets[0].bbox, (10, 20, 300, 50))
        self.assertIsNone(seen[0].targets[0].page)

    async def test_invalid_folio_results_obey_policy_and_preserve_template_impact(self):
        for invalid in ("Lost marker", "", "  ", "__PDF_CRAFT_FOLIO__",
                        "Page __PDF_CRAFT_FOLIO____PDF_CRAFT_FOLIO__"):
            for allow in (True, False):
                with self.subTest(invalid=invalid, allow=allow), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    source = furniture_source(root / "source", template=True)
                    adapter = Mock()
                    adapter.transform_position = AsyncMock(return_value=invalid)
                    seen = []

                    async def decide(failure):
                        seen.append(failure)
                        return allow

                    pending = FurnitureExtractionTransformer(adapter).transform(
                        source, root / "output.pcex", error_policy=decide,
                    )
                    if allow:
                        await pending
                    else:
                        with self.assertRaisesRegex(ValueError, "folio"):
                            await pending
                        self.assertFalse((root / "output.pcex").exists())
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(seen[0].category, FailureCategory.FILL)
                    self.assertEqual(seen[0].targets[0].scope, "template")
                    self.assertEqual(seen[0].targets[0].unit_id, "1:2")
                    self.assertEqual(seen[0].targets[0].page_indexes, (7, 8))
                    self.assertEqual([part.page_indexes for part in seen[0].affected], [(7,), (8,)])
                    self.assertTrue(all(part.page is not None and part.bbox is not None for part in seen[0].affected))

    async def test_strict_folio_failure_and_legal_none_or_prior_recovery(self):
        for result in ("Lost marker", None):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = furniture_source(root / "source", template=True)
                adapter = Mock()
                adapter.transform_position = AsyncMock(return_value=result)
                pending = FurnitureExtractionTransformer(adapter).transform(
                    source, root / "output.pcex", error_policy=False,
                )
                if result is None:
                    await pending
                else:
                    with self.assertRaisesRegex(ValueError, "folio"):
                        await pending
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = furniture_source(root / "source", template=True)
            value = translator()
            value._translate_text_async = AsyncMock(side_effect=RuntimeError("provider failure"))
            seen = []
            await FurnitureExtractionTransformer(FurnitureXMLTransformer(value)).transform(
                source, root / "output.pcex", error_policy=lambda failure: seen.append(failure) or True,
            )
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0].category, FailureCategory.TRANSLATION)

    async def test_callback_failure_is_not_recovered(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = furniture_source(root / "source", template=True)
            adapter = Mock()
            adapter.transform_position = AsyncMock(return_value="Lost marker")

            def reject(_failure):
                raise RuntimeError("callback failed")

            with self.assertRaises(ErrorCallbackError):
                await FurnitureExtractionTransformer(adapter).transform(
                    source, root / "output.pcex", error_policy=reject,
                )

    async def test_invalid_section_counts_and_empty_text_obey_policy(self):
        for invalid in ([], ["Only one"], ["", "Valid"]):
            for allow in (False, True):
                with self.subTest(invalid=invalid, allow=allow), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    source = furniture_source(root / "source")
                    adapter = Mock()
                    adapter.transform_sections = AsyncMock(return_value=invalid)
                    seen = []
                    pending = FurnitureExtractionTransformer(adapter).transform(
                        source, root / "output.pcex", error_policy=lambda f: seen.append(f) or allow,
                    )
                    if allow:
                        await pending
                    else:
                        with self.assertRaisesRegex(ValueError, "section"):
                            await pending
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(seen[0].category, FailureCategory.FILL)
                    self.assertTrue(all(t.page_indexes == (7,) and t.bbox is not None for t in seen[0].targets))

    async def test_xml_furniture_validation_failures_are_decided_once(self):
        for invalid in ("count", "identity", "empty", "position"):
            for allow in (False, True):
                with self.subTest(invalid=invalid, allow=allow), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    source = furniture_source(root / "source", template=invalid == "position")

                    async def malformed(task, **_kwargs):
                        if invalid == "count":
                            task.element.remove(task.element[0])
                        elif invalid == "identity":
                            task.element[0].set("id", "wrong")
                        elif invalid == "empty":
                            task.element[0].text = None
                        else:
                            task.element.text = None
                        return task.element, task.payload

                    runtime = Mock()
                    runtime.translate_element = AsyncMock(side_effect=malformed)
                    seen = []
                    pending = FurnitureExtractionTransformer(FurnitureXMLTransformer(runtime)).transform(
                        source, root / "output.pcex", error_policy=lambda f: seen.append(f) or allow,
                    )
                    if allow:
                        await pending
                    else:
                        with self.assertRaisesRegex(ValueError, "empty" if invalid in {"empty", "position"} else "identities"):
                            await pending
                    self.assertEqual(len(seen), 1)
                    self.assertEqual(len(seen[0].targets), 1 if invalid in {"empty", "position"} else 2)
