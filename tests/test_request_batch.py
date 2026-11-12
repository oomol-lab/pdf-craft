"""Translation transport integrity, separate from content grouping regressions."""
# pylint: disable=protected-access
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from xml.etree.ElementTree import fromstring

from pdf_craft import RetryPolicy
from pdf_craft._policy import policy_operation
from pdf_craft.extractor.chapter.chapter import Chapter, SourceTextFragment, TextFlowItem, InlineExpression, SourceAsset, Reference, search_references_in_chapter
from pdf_craft.expression import ExpressionKind
from pdf_craft.transformer import AnchoredContent, AnchoredContentXMLTransformer
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask, SubmitKind
from pdf_craft.usage import UsageCollector


from tests.translation_model import translator_fixture, paragraph

class RequestBatchTests(IsolatedAsyncioTestCase):

    @policy_operation
    async def test_window_limits_requests_without_limiting_content_readiness(self):
        for window in (1, 2):
            translator, model = translator_fixture(max_group_score=45)
            original = model.invoke
            active = peak = 0
            async def invoke(*args, invoke_original=original, **kwargs):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0.01)
                    return await invoke_original(*args, **kwargs)
                finally:
                    active -= 1
            translator._translation_runtime.__dict__['_invoke'] = invoke
            translator._fill_runtime.__dict__['_invoke'] = invoke
            async def roots():
                for index in range(24):
                    yield TranslationTask(fromstring(f'<html><body><p>Paragraph {index}.</p></body></html>'),
                                          SubmitKind.REPLACE, index)
            results = await asyncio.wait_for(translator._translate_elements(
                roots(), **StructuredTranslation().arguments(), window=window), 5)
            self.assertEqual(len(results), 24)
            self.assertGreater(len(model.calls), 2)
            self.assertEqual(peak, window)
            self.assertEqual(active, 0)


    async def test_repeated_local_formula_ids_stay_in_their_own_units(self):
        translator, model = translator_fixture()
        units = [AnchoredContent(str(index), 0, -1, SourceAsset(
            1, 'image', (0, 0, 100, 100), caption=[f'Caption {index} ',
                InlineExpression(ExpressionKind.INLINE_DOLLAR, letter), ' after.']), '')
                 for index, letter in enumerate(('x', 'y'))]
        results = await AnchoredContentXMLTransformer(translator).transform_assets(units)
        self.assertEqual([result.caption for result in results if result is not None],
                         [unit.asset.caption for unit in units])
        self.assertEqual(len(model.calls), 2)

    async def test_exhausted_unit_retains_real_batch_evidence_and_counts_usage_once(self):
        with TemporaryDirectory() as directory:
            translator, model = translator_fixture()
            config = translator._fill_llm
            config.failure_log_path = Path(directory)
            original = model.answer
            def answer(messages):
                value = original(messages)
                if messages[0]['content'].startswith('You are an XML structure engineer.'):
                    return value.replace('Paragraph 1.', 'wrong')
                return value
            model.answer = answer
            collector = UsageCollector()
            await NarrativeXMLTransformer(translator).transform_many([
                (Chapter(index, 1, [paragraph(index)]), index) for index in range(2)],
                error_policy=True, retry_policy=RetryPolicy(fill=1), usage=collector)
            logs = list(Path(directory).iterdir())
            self.assertEqual(len(logs), 1)
            summary = json.loads((logs[0] / 'summary.json').read_text())
            self.assertEqual(summary['attempts'], 2)
            transports = [json.loads(line) for line in (logs[0] / 'transports.jsonl').read_text().splitlines()]
            requests = [record for record in transports if record['event'] == 'request']
            self.assertEqual(len(requests), 2)
            self.assertIn('XML structure', requests[0]['messages'][0]['content'])
            self.assertIn('Paragraph 0.', requests[1]['messages'][1]['content'])
            self.assertEqual([message['role'] for message in requests[1]['messages']], ['system', 'user'])
            self.assertIn('Working candidate XML', requests[1]['messages'][1]['content'])
            usage = collector.report()[config]
            self.assertEqual(usage.effective.requests, 0)
            self.assertEqual(usage.wasted.requests, 3)

    async def test_independent_operations_never_share_a_batch(self):
        translator, model = translator_fixture()
        async def operation(index):
            return await NarrativeXMLTransformer(translator).transform_many([
                (Chapter(index, 1, [paragraph(index)]), index)],
                request_headers={'X-Document': str(index)})
        await asyncio.gather(operation(0), operation(1))
        self.assertEqual(len(model.calls), 4)
        self.assertTrue(all(len(batch) == 1 for batch in model.calls))


    async def test_body_and_footnote_with_reused_ids_keep_chapter_ownership(self):
        translator, model = translator_fixture()
        chapters = []
        for index in range(2):
            note = Reference(1, 1, '*', [TextFlowItem('body', 0, [
                SourceTextFragment(1, 2, (0, 50, 100, 70), [f'Note {index}.'])])])
            body = TextFlowItem('body', 0, [SourceTextFragment(
                1, 1, (0, 0, 100, 30), [f'Body {index}.', note])])
            chapters.append((Chapter(index, 1, [body]), index))
        results = await NarrativeXMLTransformer(translator).transform_many(chapters)
        for index, chapter in enumerate(results):
            notes = list(search_references_in_chapter(chapter))
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].mark, '*')
            flow = notes[0].flow_items[0]
            assert isinstance(flow, TextFlowItem)
            fragment = flow.children[0]
            assert isinstance(fragment, SourceTextFragment)
            self.assertEqual(fragment.content, [f'Note {index}.'])
        self.assertEqual(len(model.calls), 2)




    async def test_group_usage_is_charged_once_after_atomic_adoption(self):
        translator, model = translator_fixture()
        collector = UsageCollector()
        await NarrativeXMLTransformer(translator).transform_many([
            (Chapter(index, 1, [paragraph(index)]), index) for index in range(2)],
            error_policy=False, usage=collector)
        self.assertEqual(len(model.wire_calls), 2)
        report = collector.report()[translator._fill_llm]
        self.assertEqual(report.effective.requests, 2)
        self.assertEqual(report.wasted.requests, 0)
