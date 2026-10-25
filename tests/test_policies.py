"""Recovery decisions, provenance and task outcomes across the public boundary."""

# pylint: disable=protected-access,unused-argument,cell-var-from-loop
import asyncio
import pickle
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from pdf_craft import (
    AllFallbackError,
    AsyncPDFCraft,
    ErrorCallbackError,
    ErrorPolicy,
    FailureCategory,
    FailureLocation,
    PageGeometry,
    RetryPolicy,
    SourceRegion,
    LLM,
    XMLTranslator,
    ConcurrentExecutor,
    FixedCapacity,
)
from pdf_craft._policy import (
    OMITTED,
    Completion,
    current,
    failure,
    locations,
    policy_operation,
    recover,
)
from pdf_craft._failure_sources import page_sources
from pdf_craft.common import save_xml
from pdf_craft.extractor.chapter.chapter import (
    Chapter,
    SourceTextFragment,
    TextFlowItem,
    encode,
)
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from tests.extraction_helpers import make_extraction


@policy_operation
async def operation(*, retry_policy=None, error_policy=OMITTED):
    return current()


@policy_operation
async def handle(*, error_policy=OMITTED):
    await recover(
        ValueError("bad response"), FailureCategory.FILL, "xml_fill", "omit_translation"
    )


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_defaults_validation_and_immutability(self):
        self.assertEqual(RetryPolicy().analysis, 3)
        self.assertEqual(RetryPolicy(fill=0).fill, 0)
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RetryPolicy(analysis=cast(Any, value))
        with self.assertRaises(FrozenInstanceError):
            setattr(RetryPolicy(), "fill", 9)
        with self.assertRaises(TypeError):
            ErrorPolicy(ignore_fill_errors=cast(Any, None))

    async def test_strict_shorthands_and_category_decisions(self):
        for selected in (
            None,
            False,
            ErrorPolicy(),
            ErrorPolicy(ignore_analysis_errors=True),
        ):
            with (
                self.subTest(selected=selected),
                self.assertRaisesRegex(ValueError, "bad response"),
            ):
                await handle(error_policy=selected)
        for selected in (True, ErrorPolicy(ignore_fill_errors=True), lambda _: True):
            await handle(error_policy=selected)

    async def test_async_decision_is_awaited_once_with_complete_context(self):
        seen = []
        target = FailureLocation(
            "regions",
            member_path="chapters/chapter_1.xml",
            unit_id="body:2:4",
            regions=(
                SourceRegion(PageGeometry(2, 100, 200, 144), 4, (10, 20, 30, 40)),
            ),
        )

        async def accept(context):
            await asyncio.sleep(0)
            seen.append(context)
            return True

        with locations((target,)):
            await handle(error_policy=accept)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].targets, (target,))
        self.assertEqual(seen[0].affected, (target,))
        self.assertEqual(
            seen[0].targets[0].regions[0].map_bbox((200, 400)), (20, 40, 60, 80)
        )
        self.assertEqual(pickle.loads(pickle.dumps(seen[0])), seen[0])

    async def test_callback_error_is_not_recovered_or_called_again(self):
        callback = AsyncMock(side_effect=RuntimeError("consumer crashed"))
        with self.assertRaises(ErrorCallbackError) as caught:
            await handle(error_policy=callback)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        callback.assert_awaited_once()
        with self.assertRaises(ErrorCallbackError):
            await handle(error_policy=cast(Any, lambda _: None))

    async def test_cancellation_cannot_be_ignored(self):
        async def cancel(_):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await handle(error_policy=cancel)

    async def test_facade_inheritance_override_and_concurrent_isolation(self):
        results = []

        async def render(_self, *_args, **_kwargs):
            await asyncio.sleep(0)
            results.append((current().task_id, current().retry.fill, current().errors))

        craft = AsyncPDFCraft(retry_policy=RetryPolicy(fill=7), error_policy=True)
        with (
            patch(
                "pdf_craft.craft._ensure_extraction_async",
                AsyncMock(return_value=object()),
            ),
            patch("pdf_craft.craft.MarkdownRenderer.render", render),
        ):
            await asyncio.gather(
                craft.render_markdown("one", "one.md"),
                craft.render_markdown(
                    "two", "two.md", retry_policy=RetryPolicy(fill=0), error_policy=None
                ),
            )
        self.assertEqual(
            {(count, errors) for _, count, errors in results}, {(7, True), (0, None)}
        )
        self.assertEqual(len({identity for identity, _, _ in results}), 2)
        self.assertIsNone(
            __import__("pdf_craft._policy", fromlist=["_execution"])._execution.get()
        )

    async def test_no_fake_geometry_and_error_details_survive_unpickleable_exception(
        self,
    ):
        class LocalError(Exception):
            def __reduce__(self):
                raise TypeError("not serializable")

        target = FailureLocation("page", source="book.pdf", page_indexes=(7,))
        value = failure(
            LocalError("render failed"),
            FailureCategory.PDF_RENDER,
            "page_render",
            "page_placeholder",
            targets=(target,),
        )
        restored = pickle.loads(pickle.dumps(value))
        self.assertEqual(restored.targets[0].regions, ())
        self.assertEqual(restored.error.message, "render failed")
        self.assertIn("LocalError", restored.error.type)

    def test_task_all_fallback_is_not_text_equality_or_no_work(self):
        Completion().check("no targets")
        Completion(succeeded=1, degraded=10).check("partial")
        with self.assertRaises(AllFallbackError):
            Completion(degraded=2).check("translate")


def translator():
    config = LLM("unused", "https://example.invalid", "unused", "cl100k_base")
    value = XMLTranslator(
        config,
        config,
        "zh",
        None,
        10,
        10000,
        executor=ConcurrentExecutor(FixedCapacity(1)),
    )
    return value


def paragraph(page, order, content):
    return TextFlowItem(
        "body", 0, [SourceTextFragment(page, order, (1, 2, 80, 20), [content])]
    )


class TranslationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_failure_never_publishes_source_as_translation(self):
        value = translator()
        value._translate_text_async = AsyncMock(side_effect=RuntimeError("provider failed"))
        source = {"title": "Original title", "description": "Original description",
                  "subjects": ["First subject", "Second subject"]}
        notifications = []
        result = await NarrativeXMLTransformer(value).translate_metadata(
            source, error_policy=True, on_metadata=notifications.append,
        )
        self.assertEqual(result, {"language": "zh"})
        self.assertEqual(source["title"], "Original title")
        self.assertEqual(notifications[0].metadata.title, "Original title")
        self.assertEqual(notifications[0].original.title, "Original title")

    async def test_metadata_partial_failure_retains_only_accepted_overlay_fields(self):
        from tests.test_epub_optional_cfi import mappings

        async def partial(runtime, inline_segments, **kwargs):
            result = []
            for segment in inline_segments:
                if "Fail" in "".join(part.text for part in segment):
                    result.append(None)
                else:
                    result.extend(await mappings(runtime, [segment], **kwargs))
            return result

        with patch.object(XMLTranslator, "_translate_inline_segments_async", new=partial):
            result = await NarrativeXMLTransformer(translator()).translate_metadata(
                {"title": "Good title", "description": "Fail description",
                 "subjects": ["Good subject", "Fail subject"]}, error_policy=True,
            )
        self.assertIn("title", result)
        self.assertNotIn("description", result)
        self.assertNotIn("subjects", result)

    async def test_batch_translation_matches_single_source_and_missing_outcomes(self):
        geometries = {index: PageGeometry(index, 100, 200, 144) for index in (1, 2)}
        source = Chapter(7, 0, [TextFlowItem("body", 0, [
            SourceTextFragment(1, 2, (1, 2, 80, 20), ["Before "]),
            SourceTextFragment(2, 3, (1, 3, 80, 30), ["after."]),
        ])])
        for batch in (False, True):
            with self.subTest(batch=batch):
                value = translator()
                value._translate_text_async = AsyncMock(side_effect=RuntimeError("provider failed"))
                seen = []
                adapter = NarrativeXMLTransformer(value)
                with page_sources(geometries):
                    if batch:
                        results = await adapter.transform_many(
                            [(Chapter(None, -1, []), "empty"), (source, "seven")],
                            error_policy=lambda f: seen.append(f) or True,
                        )
                        result = results[1]
                        self.assertEqual(results[0].flow_items, [])
                    else:
                        result = await adapter.transform(source, error_policy=lambda f: seen.append(f) or True)
                self.assertTrue(getattr(result.flow_items[0], "_translation_missing"))
                self.assertEqual(seen[0].targets[0].page_indexes, (1, 2))
                self.assertEqual(len(seen[0].targets[0].regions), 2)
                self.assertIn("7", seen[0].targets[0].member_path)

    async def test_batch_partial_failure_is_bound_to_the_correct_chapter(self):
        from tests.test_epub_optional_cfi import mappings

        async def partial(runtime, inline_segments, **kwargs):
            result = []
            for segment in inline_segments:
                if "Fail" in "".join(part.text for part in segment):
                    result.append(None)
                else:
                    result.extend(await mappings(runtime, [segment], **kwargs))
            return result

        chapters = [(Chapter(1, 0, [paragraph(1, 0, "Fail body")]), "one"),
                    (Chapter(2, 0, [paragraph(2, 0, "Good body")]), "two")]
        with patch.object(XMLTranslator, "_translate_inline_segments_async", new=partial):
            result = await NarrativeXMLTransformer(translator()).transform_many(chapters, error_policy=True)
        self.assertTrue(getattr(result[0].flow_items[0], "_translation_missing"))
        self.assertFalse(getattr(result[1].flow_items[0], "_translation_missing"))

    async def test_cross_page_failure_has_all_regions_and_no_fake_translation(self):
        value = translator()
        value._translate_text_async = AsyncMock(
            side_effect=RuntimeError("provider failed")
        )
        source = Chapter(
            1,
            0,
            [
                TextFlowItem(
                    "body",
                    0,
                    [
                        SourceTextFragment(1, 2, (1, 2, 80, 20), ["Before "]),
                        SourceTextFragment(2, 3, (1, 3, 80, 30), ["after."]),
                    ],
                )
            ],
        )
        seen = []
        with page_sources(
            {1: PageGeometry(1, 100, 200, 144), 2: PageGeometry(2, 100, 200, 144)}
        ):
            result = await NarrativeXMLTransformer(value).transform(
                source, error_policy=lambda f: seen.append(f) or True
            )
        self.assertTrue(getattr(result.flow_items[0], "_translation_missing"))
        self.assertEqual(result, source)
        self.assertEqual(seen[0].targets[0].page_indexes, (1, 2))
        self.assertEqual(len(seen[0].targets[0].regions), 2)

    async def test_all_primary_translation_failures_reject_pcex_even_with_true_policy(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(
                encode(Chapter(None, -1, [paragraph(1, 1, "Source paragraph.")])),
                root / "source/chapters/chapter_head.xml",
            )
            value = translator()
            value._translate_text_async = AsyncMock(
                side_effect=RuntimeError("provider failed")
            )
            with self.assertRaises(AllFallbackError):
                await AsyncPDFCraft(error_policy=True).translate_extraction(
                    source, root / "output.pcex", value, with_anchored=False
                )
            self.assertFalse((root / "output.pcex").exists())


class PartialPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pcex_partial_missing_translation_renders_original_once(self):
        from pdf_craft import RenderMode
        from tests.test_epub_optional_cfi import mappings

        async def partial(_self, inline_segments, **kwargs):
            result = []
            for segment in inline_segments:
                if "Fail" in "".join(part.text for part in segment):
                    result.append(None)
                else:
                    result.extend(await mappings(_self, [segment], **kwargs))
            return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_extraction(root / "source", page_pixel_sizes={1: (100, 100)})
            save_xml(
                encode(
                    Chapter(
                        None,
                        -1,
                        [
                            paragraph(1, 0, "Fail source."),
                            paragraph(1, 1, "Good source."),
                        ],
                    )
                ),
                root / "source/chapters/chapter_head.xml",
            )
            with patch.object(
                XMLTranslator, "_translate_inline_segments_async", new=partial
            ):
                translated = await AsyncPDFCraft(
                    error_policy=True
                ).translate_extraction(
                    source, root / "translated.pcex", translator(), with_anchored=False
                )
            with translated._materialize() as paths:
                from pdf_craft.transformer.translation_coverage import read_coverage

                layer = next(
                    path for path in paths.translations.iterdir() if path.is_dir()
                )
                coverage = read_coverage(layer / "coverage.xml")
                self.assertEqual(coverage.narrative[("head", 1, 0)], "preserved")
                self.assertEqual(coverage.narrative[("head", 1, 1)], "translated")
            for mode in (
                RenderMode.REPLACE,
                RenderMode.BILINGUAL,
                RenderMode.BILINGUAL_TEXT,
            ):
                output = root / (mode.value + ".md")
                await AsyncPDFCraft().render_markdown(translated, output, mode=mode)
                text = output.read_text()
                self.assertEqual(text.count("Fail source."), 1)
                self.assertIn("译:Good source.", text)

    async def test_epub_error_locations_without_body_callback_and_no_auxiliary_success_mask(
        self,
    ):
        from zipfile import ZipFile
        from tests.test_epub_optional_cfi import book, document, options, mappings
        from pdf_craft._failure_sources import segment_locations

        async def partial(_self, inline_segments, **kwargs):
            result = []
            for segment in inline_segments:
                if "Fail" in "".join(part.text for part in segment):
                    await recover(
                        ValueError("cannot translate"),
                        FailureCategory.TRANSLATION,
                        "text_translation",
                        "omit_translation",
                        targets=segment_locations([segment]),
                    )
                    result.append(None)
                else:
                    result.extend(await mappings(_self, [segment], **kwargs))
            return result

        for all_failed in (False, True):
            with (
                self.subTest(all_failed=all_failed),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                source, output = root / "input.epub", root / "output.epub"
                book(
                    source,
                    [
                        (
                            "first.xhtml",
                            "application/xhtml+xml",
                            document("Fail XHTML."),
                        ),
                        (
                            "second.html",
                            "text/html",
                            document(
                                "Fail HTML." if all_failed else "Good HTML.", html=True
                            ),
                        ),
                    ],
                )
                seen = []
                with patch.object(
                    XMLTranslator, "_translate_inline_segments_async", new=partial
                ):
                    pending = AsyncPDFCraft().translate_epub(
                        source,
                        output,
                        **options(),
                        error_policy=lambda context: seen.append(context) or True,
                    )
                    if all_failed:
                        with self.assertRaises(AllFallbackError):
                            await pending
                        self.assertFalse(output.exists())
                    else:
                        await pending
                        with ZipFile(output) as archive:
                            self.assertIn(
                                "Fail XHTML.", archive.read("EPUB/first.xhtml").decode()
                            )
                            self.assertNotIn(
                                "译:Fail", archive.read("EPUB/first.xhtml").decode()
                            )
                            self.assertIn(
                                "译:Good HTML.",
                                archive.read("EPUB/second.html").decode(),
                            )
                xhtml = [
                    location
                    for event in seen
                    for location in event.targets
                    if location.member_path == "EPUB/first.xhtml"
                ]
                self.assertTrue(xhtml)
                self.assertTrue(all(location.epubcfi is not None for location in xhtml))
                if all_failed:
                    html = [
                        location
                        for event in seen
                        for location in event.targets
                        if location.member_path == "EPUB/second.html"
                    ]
                    self.assertTrue(html)
                    self.assertTrue(all(location.epubcfi is None for location in html))


class AnalysisRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_jev_uses_analysis_budget_then_reports_only_failed_target(self):
        from pdf_craft import JEV, OperationError
        from typesafe_sdk import TypeSafeAPIConnectionError
        from pdf_craft.jev import JEVRuntime

        calls, seen = [], []
        runtime = JEVRuntime(JEV("unused"), ConcurrentExecutor(FixedCapacity(2)))

        async def evaluate(request):
            page = request["state"]["target_page"]["page_index"]
            calls.append(page)
            if page == 1:
                raise OperationError("connection", cause=TypeSafeAPIConnectionError("lost"))
            return 0.95

        runtime._evaluate_once = evaluate
        runtime._client = cast(Any, object())

        @policy_operation
        async def review(*, retry_policy=None, error_policy=OMITTED):
            return await runtime.evaluate_many(
                [
                    (index, {"state": {"target_page": {"page_index": index}}})
                    for index in (1, 2)
                ]
            )

        result = await review(
            retry_policy=RetryPolicy(analysis=2),
            error_policy=lambda f: seen.append(f) or True,
        )
        self.assertEqual(result, [(2, 0.95)])
        self.assertEqual(calls.count(1), 3)
        self.assertEqual(calls.count(2), 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].targets[0].page_indexes, (1,))
        self.assertEqual(seen[0].error.attempts, 3)

    async def test_failed_page_repair_keeps_original_valid_graph_without_changing_caller(
        self,
    ):
        from copy import deepcopy
        from tests.test_page_repair import _page_with_reference
        from pdf_craft.extractor.chapter.page_repair import AllPageLlmRepairProcessor
        from pdf_craft.extractor.chapter.page_analysis import restore_streams

        source = [_page_with_reference()]
        before = deepcopy(source)
        seen = []
        request = AsyncMock(return_value="{}")
        processor = AllPageLlmRepairProcessor(
            request,
            retry_policy=RetryPolicy(analysis=1),
            error_policy=lambda f: seen.append(f) or True,
        )
        result = await processor([], source, {})
        self.assertEqual(source, before)
        self.assertEqual(result, before)
        restore_streams(result)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].stage, "footnote_repair")
        self.assertEqual(seen[0].targets[0].page_indexes, (1,))

    async def test_terminal_service_error_is_never_offered_to_error_policy(self):
        from pdf_craft import NonContinuableError

        called = []

        @policy_operation
        async def run(*, error_policy=OMITTED):
            await recover(
                NonContinuableError("quota exhausted"),
                FailureCategory.ANALYSIS,
                "jev_review",
                "algorithm_result",
            )

        with self.assertRaises(NonContinuableError):
            await run(error_policy=lambda f: called.append(f) or True)
        self.assertEqual(called, [])


class SyncPolicyTests(unittest.TestCase):
    def test_instance_defaults_reach_synchronous_execution(self):
        from pdf_craft import PDFCraft

        seen = []

        async def render(_self, *_args, **_kwargs):
            seen.append((current().retry.fill, current().errors))

        with (
            patch(
                "pdf_craft.craft._ensure_extraction_async",
                AsyncMock(return_value=object()),
            ),
            patch("pdf_craft.craft.MarkdownRenderer.render", render),
        ):
            PDFCraft(
                retry_policy=RetryPolicy(fill=0), error_policy=True
            ).render_markdown("source", "target")
        self.assertEqual(seen, [(0, True)])


class NestedDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_outer_fill_recovery_cannot_override_a_denied_translation_failure(
        self,
    ):
        from pdf_craft import AnchoredContent, AnchoredContentXMLTransformer
        from pdf_craft.extractor.chapter.chapter import SourceAsset
        from pdf_craft.transformer.anchored_translation import _transform_batch_async

        value = translator()
        value._translate_text_async = AsyncMock(
            side_effect=RuntimeError("translation unavailable")
        )
        seen = []
        item = AnchoredContent(
            "head", 0, -1, SourceAsset(1, "image", (0, 0, 20, 20), title=["Title"]), ""
        )
        with self.assertRaisesRegex(RuntimeError, "translation unavailable"):
            await _transform_batch_async(
                (item,),
                AnchoredContentXMLTransformer(value),
                error_policy=ErrorPolicy(
                    ignore_translation_errors=lambda f: seen.append(f) or False,
                    ignore_fill_errors=True,
                ),
            )
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].category, FailureCategory.TRANSLATION)
