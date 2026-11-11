"""No-work XML tasks retain results, ordering and lifecycle without model calls."""
# Every subtest awaits its work before advancing to the next loop iteration.
# pylint: disable=protected-access,cell-var-from-loop
import re
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock
from xml.etree.ElementTree import Element, SubElement, tostring

from pdf_craft import RetryPolicy
from pdf_craft.progress import ProgressEventKind, UnitKind, UnitOutcome
from pdf_craft.transformer.furniture import FurnitureSection
from pdf_craft.transformer.furniture_xml import FurnitureXMLTransformer
from pdf_craft.transformer.xml_translator.xml import decode_friendly, encode_friendly
from pdf_craft.transformer.xml_translator.xml_translator import SubmitKind, TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLFillError
from tests.test_anchored_content_translation import _repairing_translator


def task(value, payload, *, action=SubmitKind.REPLACE):
    root = Element('document', {'name': str(payload)})
    SubElement(root, 'section', {'key': 'keep'}).text = value
    SubElement(root, 'asset', {'hash': 'unchanged', 'ref': 'image'})
    return TranslationTask(root, action, payload, word_count=1 if value in ('Alpha.', 'Beta.') else 0)


class EmptyTranslationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_work_single_result_preserves_source_for_all_submit_modes(self):
        for value in ('', ' ', '\t\n\u3000', '\u200b', '\ufeff\u2060\x08'):
            for action in SubmitKind:
                with self.subTest(value=repr(value), action=action):
                    translator, runtime = _repairing_translator(())
                    translator._translate_text_async = AsyncMock(side_effect=AssertionError('no artist work'))
                    source = task(value, object(), action=action)
                    expected = tostring(source.element)
                    root, payload = await translator.translate_element(source)
                    self.assertEqual(tostring(root), expected)
                    self.assertIs(payload, source.payload)
                    translator._translate_text_async.assert_not_awaited()
                    self.assertEqual(runtime.context_value.calls, 0)

    async def test_service_393_furniture_zero_width_sections_are_not_failures(self):
        for page in (11, 14, 19):
            translator, runtime = _repairing_translator(())
            translator._translate_text_async = AsyncMock(side_effect=AssertionError('no artist work'))
            # The production source section contains exactly U+200B. Geometry
            # is intentionally synthetic; retain the source identity/shape.
            section = FurnitureSection(page, (1, 2, 3, 4), '\u200b', '1')
            result = await FurnitureXMLTransformer(translator).transform_sections(page, [section])
            self.assertEqual(result, ['\u200b'])
            translator._translate_text_async.assert_not_awaited()
            self.assertEqual(runtime.context_value.calls, 0)

    async def test_mixed_tasks_keep_order_and_one_shared_request(self):
        for streaming in (False, True):
            for collect in (False, True):
                with self.subTest(streaming=streaming, collect=collect):
                    translator, runtime = _repairing_translator(())
                    translator._translate_text_async = AsyncMock(side_effect=lambda text, **_kw: text)
                    requests = []

                    async def fill(messages, **_kwargs):
                        match = re.search(r'```XML\n(.*?)```', messages[1].message, re.S)
                        assert match is not None
                        root = next(decode_friendly(match[1], tags='xml'))
                        requests.append(root)
                        return encode_friendly(root)

                    runtime.context_value.request = fill
                    sources = [task(value, i) for i, value in enumerate(
                        ('\u200b', 'Alpha.', '', 'Beta.', '\u3000'))]
                    originals = [deepcopy(source.element) for source in sources]
                    task_events, results, progress = [], [], []

                    async def inputs():
                        for source in sources:
                            yield source

                    output = await translator._translate_elements(
                        inputs() if streaming else sources, window=2,
                        collect_results=collect, on_result=results.append,
                        on_task_result=task_events.append, on_translation_event=progress.append,
                        unit_counts={UnitKind.CONTENT: 5}, total_words=2)
                    self.assertEqual([payload for _, payload in results], list(range(5)))
                    self.assertEqual([source.payload for _, source in task_events], list(range(5)))
                    self.assertEqual([payload for _, payload in output], list(range(5)) if collect else [])
                    for (root, _), original in zip(results, originals, strict=True):
                        self.assertEqual(tostring(root), tostring(original))
                        self.assertFalse(any(key.startswith('__pdf_craft_')
                                             for node in root.iter() for key in node.attrib))
                    translator._translate_text_async.assert_awaited_once()
                    self.assertEqual(len(requests), 1)
                    completed = [event for event in progress if event.unit_id is not None
                                 and event.kind == ProgressEventKind.COMPLETED]
                    self.assertEqual(len(completed), 5)
                    self.assertEqual(sum(event.outcome == UnitOutcome.SKIPPED for event in completed), 3)
                    self.assertFalse(any(event.outcome == UnitOutcome.FALLBACK for event in completed))

    async def test_unordered_stream_delivers_empty_once(self):
        translator, runtime = _repairing_translator(())
        translator._translate_text_async = AsyncMock(side_effect=AssertionError('no artist work'))
        sources = [task('', i) for i in range(3)]
        async def inputs():
            for source in sources:
                yield source
        completed = []
        values = await translator._translate_elements(inputs(), ordered_roots=False,
                                                       on_task_result=completed.append)
        self.assertEqual(sorted(payload for _, payload in values), [0, 1, 2])
        self.assertEqual(sorted(source.payload for _, source in completed), [0, 1, 2])
        self.assertEqual(runtime.context_value.calls, 0)

    async def test_empty_result_callback_failure_propagates(self):
        translator, _ = _repairing_translator(())
        def broken(_result):
            raise ValueError('cannot deliver result')
        with self.assertRaisesRegex(ValueError, 'cannot deliver result'):
            await translator._translate_elements([task('', 0)], on_result=broken)

    async def test_real_fill_failure_is_not_reclassified_as_no_work(self):
        translator, runtime = _repairing_translator(['<xml/>'])
        translator._translate_text_async = AsyncMock(return_value='Bonjour.')
        with self.assertRaises(XMLFillError):
            await translator.translate_element(task('Alpha.', 0), retry_policy=RetryPolicy(fill=0))
        self.assertEqual(runtime.context_value.calls, 1)
