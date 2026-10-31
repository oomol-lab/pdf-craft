"""No network: exercise real requests, repair budgets and failure propagation."""
# pylint: disable=protected-access,unused-argument
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import pickle
import tempfile
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pdf_craft import LLM, ConcurrentExecutor, FixedCapacity, RetryPolicy
from pdf_craft._policy import error_details
from pdf_craft.llm import runtime_for, Message, MessageRole
from pdf_craft.llm.loop import RepairLoopOptions, ProtocolRetry, ProtocolSuccess, run_repair_loop
from pdf_craft.llm.failure_log import FailureLog
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLTranslator
from tests.test_fill_acceptance_regressions import chapter, local_encoding


class Exhausted(Exception):
    failure_log_id: str
    attempts: int
    request_id = 'provider-id'


class Protocol:
    async def validate(self, response, state, attempt, max_attempts):
        if response == 'RIGHT':
            return ProtocolSuccess(response, state)
        return ProtocolRetry('Expected RIGHT', state, reset_history=True)

    async def empty(self, state, attempt, max_attempts):
        return ProtocolRetry('Empty response', state)

    async def exhausted(self, state, attempts, response):
        error = Exhausted('Expected RIGHT; exhausted')
        error.attempts = attempts
        raise error


class FailureLogTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def runtime(self, responses, *, enabled=True, path=None, temperature=None):
        llm = LLM('private-api-secret', 'https://example.invalid/v1', 'test-model', 'o200k_base',
                  failure_log_path=(path or self.root / 'evidence') if enabled else None,
                  cache_path=self.root / 'cache', retry_times=0, temperature=temperature)
        llm._encoding = local_encoding()
        runtime = runtime_for(llm, ConcurrentExecutor(FixedCapacity(1)))
        stream = iter(responses)
        seen = []
        async def invoke(messages, max_tokens, temp, top_p):
            seen.append({'messages': [{'role': m.role.name.lower(), 'content': m.message} for m in messages],
                         'parameters': {'max_tokens': max_tokens, 'temperature': temp, 'top_p': top_p}})
            value = next(stream)
            if isinstance(value, BaseException):
                raise value
            return value
        runtime.__dict__['_invoke'] = invoke
        return llm, runtime, seen

    async def execute(self, runtime, attempts=13, messages=None):
        async with runtime.context() as ctx:
            return await run_repair_loop(RepairLoopOptions(
                messages=messages or [Message(MessageRole.USER, 'source')], protocol=Protocol(), state=None,
                request=lambda current, index, maximum: ctx.request(current, retry_index=index,
                    retry_max=maximum, use_cache=False), max_attempts=attempts))

    def read(self, identifier):
        folder = self.root / 'evidence' / identifier
        return json.loads((folder / 'summary.json').read_text()), [json.loads(line)
            for line in (folder / 'attempts.jsonl').read_text().splitlines()]

    async def test_exhaustion_preserves_all_actual_messages_and_effective_parameters(self):
        _, runtime, seen = self.runtime(['WRONG'] * 13, temperature=(0.2, 0.8))
        with self.assertRaises(Exhausted) as caught:
            await self.execute(runtime)
        error = caught.exception
        identifier = error.failure_log_id
        summary, records = self.read(identifier)
        self.assertTrue(summary['complete'])
        self.assertEqual((summary['attempts'], len(records)), (13, 13))
        for index, (record, actual) in enumerate(zip(records, seen)):
            self.assertEqual(record['messages'], actual['messages'])
            self.assertEqual(record['parameters'], actual['parameters'])
            self.assertEqual(record['attempt'], index + 1)
            self.assertEqual(record['response'], 'WRONG')
            self.assertEqual(record['feedback'], 'Expected RIGHT')
        self.assertAlmostEqual(records[0]['parameters']['temperature'], 0.2)
        self.assertAlmostEqual(records[-1]['parameters']['temperature'], 0.8)
        # History resets; the log must still retain all the earlier responses.
        self.assertEqual([len(r['messages']) for r in records], [1] + [3] * 12)
        details = pickle.loads(pickle.dumps(error_details(error)))
        self.assertEqual((details.failure_log_id, details.request_id), (identifier, 'provider-id'))
        wrapper = RuntimeError('wrapper')
        wrapper.__cause__ = error
        self.assertEqual(error_details(wrapper).failure_log_id, identifier)
        self.assertEqual(asdict(details)['failure_log_id'], identifier)
        self.assertFalse((self.root / 'cache').exists())
        self.assertFalse(list((self.root / 'evidence').glob('.pending-*')))

    async def test_disabled_does_not_create_directories(self):
        _, runtime, _ = self.runtime(['WRONG'], enabled=False)
        with self.assertRaises(Exhausted) as caught:
            await self.execute(runtime, 1)
        self.assertIsNone(getattr(caught.exception, 'failure_log_id', None))
        self.assertEqual(list(self.root.iterdir()), [])
        _, ordinary, _ = self.runtime(['ordinary result'])
        self.assertEqual(await ordinary.request('outside a repair loop', use_cache=False), 'ordinary result')
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_success_and_repair_success_remove_evidence(self):
        for responses in (['RIGHT'], ['WRONG', 'RIGHT']):
            _, runtime, _ = self.runtime(responses)
            self.assertEqual(await self.execute(runtime), 'RIGHT')
            self.assertEqual(list((self.root / 'evidence').iterdir()), [])

    async def test_concurrent_loops_are_isolated(self):
        async def one():
            _, runtime, _ = self.runtime(['WRONG'] * 2)
            try:
                await self.execute(runtime, 2)
            except Exhausted as error:
                return error.failure_log_id
            self.fail('expected exhaustion')
        identifiers = await asyncio.gather(one(), one())
        self.assertEqual(len(set(identifiers)), 2)
        for identifier in identifiers:
            _, rows = self.read(identifier)
            self.assertEqual(len(rows), 2)

    async def test_cancel_and_transport_failure_remove_staging(self):
        for failure in (asyncio.CancelledError(), ValueError('transport failed')):
            _, runtime, _ = self.runtime(['WRONG', failure])
            with self.assertRaises(BaseException):
                await self.execute(runtime)
            self.assertEqual(list((self.root / 'evidence').iterdir()), [])

    async def test_empty_protocol_response_is_recorded(self):
        async def request(*_args):
            return ''
        with self.assertRaises(Exhausted) as caught:
            await run_repair_loop(RepairLoopOptions([Message(MessageRole.USER, 'source')],
                request, Protocol(), None, max_attempts=2, failure_log_path=self.root / 'evidence'))
        _, rows = self.read(caught.exception.failure_log_id)
        self.assertEqual([r['response'] for r in rows], ['', ''])
        self.assertEqual([r['feedback'] for r in rows], ['Empty response'] * 2)

    async def test_credentials_are_redacted_in_all_files(self):
        payload = ('private-api-secret Authorization: Bearer another-secret\n'
                   'https://example.invalid/file?X-Amz-Signature=download-secret\n'
                   'https://user:password@example.invalid/private\n'
                   'https://example.invalid/blob?sig=azure-secret\nAuthorization: Basic basic-secret\n'
                   '"Authorization": "Bearer quoted-secret"')
        _, runtime, _ = self.runtime([payload])
        with self.assertRaises(Exhausted) as caught:
            await self.execute(runtime, 1, [Message(MessageRole.USER, payload)])
        folder = self.root / 'evidence' / caught.exception.failure_log_id
        all_text = ''.join(path.read_text() for path in folder.iterdir())
        for secret in ('private-api-secret', 'another-secret', 'download-secret', 'password', 'azure-secret', 'basic-secret', 'quoted-secret'):
            self.assertNotIn(secret, all_text)
        self.assertIn('REDACTED', all_text)

    async def test_io_failure_does_not_replace_business_result(self):
        _, runtime, _ = self.runtime(['WRONG'])
        with patch.object(FailureLog, '_append', side_effect=OSError('disk full')):
            with self.assertLogs('pdf_craft.llm.failure_log', level='WARNING'):
                with self.assertRaises(Exhausted) as caught:
                    await self.execute(runtime, 1)
        summary = json.loads((self.root/'evidence'/caught.exception.failure_log_id/'summary.json').read_text())
        self.assertFalse(summary['complete'])
        _, runtime, _ = self.runtime(['RIGHT'])
        with patch.object(FailureLog, '_append', side_effect=OSError('disk full')):
            with self.assertLogs('pdf_craft.llm.failure_log', level='WARNING'):
                self.assertEqual(await self.execute(runtime, 1), 'RIGHT')

    async def test_unwritable_destination_leaves_original_exception(self):
        destination = self.root / 'file'
        destination.write_text('occupied')
        _, runtime, _ = self.runtime(['WRONG'], path=destination)
        with self.assertLogs('pdf_craft.llm.failure_log', level='WARNING'):
            with self.assertRaises(Exhausted) as caught:
                await self.execute(runtime, 1)
        self.assertIsNone(getattr(caught.exception, 'failure_log_id', None))

    async def test_ignored_xml_failure_callback_can_find_retained_log(self):
        llm, runtime, _ = self.runtime(['WRONG'] * 2)
        failures = []
        def accept(context):
            failures.append(context)
            self.assertTrue((self.root/'evidence'/context.error.failure_log_id/'summary.json').exists())
            return True
        translator = XMLTranslator(llm, llm, 'ru', None, 10, 2600,
            executor=runtime.executor, retry_policy=RetryPolicy(fill=1), error_policy=accept)
        translator._fill_runtime = runtime
        translator._translate_text_async = AsyncMock(return_value='RIGHT')
        await NarrativeXMLTransformer(translator).transform(chapter(), error_policy=accept)
        self.assertEqual(len(failures), 1)
        summary, rows = self.read(failures[0].error.failure_log_id)
        self.assertEqual(summary['stage'], 'xml_fill')
        self.assertEqual(summary['targets'][0]['unit_id'], 'body:21:2')
        self.assertEqual(len(rows), 2)
