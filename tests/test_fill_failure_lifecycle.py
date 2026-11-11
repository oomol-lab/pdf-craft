"""Offline requests exercise evidence through real candidate adoption/submission."""
# pylint: disable=protected-access
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from pdf_craft import SubmitKind
from pdf_craft.transformer.xml_translator.xml_translator import TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing
from pdf_craft.llm.failure_log import FailureLog
from tests.fill_fixtures import model_replies
from tests.translation_model import translator_fixture


class FillFailureLifecycleTests(IsolatedAsyncioTestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'evidence'
        self.translator, self.model = translator_fixture()
        self.translator._fill_llm.failure_log_path = self.root
        self.good = '<xml><p id="1">Right.</p><p id="2">Done.</p></xml>'
        model_replies(self.model, 'Right. Done.', [
            '<xml><p id="1">Wrong.</p><p id="2">Done.</p></xml>', self.good])

    def task(self):
        return TranslationTask(fromstring('<body><p>Original.</p><p>Next.</p></body>'),
                               SubmitKind.REPLACE, None)

    async def translate(self, **kwargs):
        return await self.translator._translate_elements(
            [self.task()], **kwargs)

    def archived(self, error):
        folder = self.root / error.failure_log_id
        self.assertTrue(folder.is_dir())
        self.assertFalse(any(p.name.startswith('.pending-') for p in self.root.iterdir()))
        summary = json.loads((folder / 'summary.json').read_text())
        rows = [json.loads(line) for line in (folder / 'attempts.jsonl').read_text().splitlines()]
        return summary, rows

    async def test_submit_failure_keeps_all_attempts_and_original_error(self):
        failure = RuntimeError('submission failed')
        def submit(*_args, **_kwargs):
            self.assertEqual(len(list(self.root.glob('.pending-*'))), 1)
            raise failure
        with patch('pdf_craft.transformer.xml_translator.xml_translator.translator.submit', submit):
            with self.assertRaises(RuntimeError) as caught:
                await self.translate()
        self.assertIs(caught.exception, failure)
        summary, rows = self.archived(failure)
        self.assertEqual(summary['attempts'], 2)
        self.assertEqual([r['validation'] for r in rows], ['ProtocolRetry', 'ProtocolSuccess'])
        self.assertIn('Wrong.', rows[0]['response'])
        self.assertIn('Right.', rows[1]['response'])
        self.assertIn('Missing', rows[0]['feedback'])
        self.assertEqual(summary['artist_text'], 'Right. Done.')
        self.assertIn('RuntimeError: submission failed', summary['error']['traceback'])
        self.assertEqual(len(list(self.root.iterdir())), 1)

    async def test_adapter_failure_after_fill_and_content_callback_failure(self):
        for phase in ('adapter', 'callback'):
            with self.subTest(phase=phase):
                failure = ValueError(phase)
                model_replies(self.model, 'Right. Done.', [self.good])
                if phase == 'adapter':
                    with self.assertRaises(ValueError) as caught:
                        await self.translate(interrupt_translated_text_segments=lambda _, error=failure: (_ for _ in ()).throw(error))
                else:
                    with self.assertRaises(ValueError) as caught:
                        await self.translate(on_result=lambda _, error=failure: (_ for _ in ()).throw(error))
                self.assertIs(caught.exception, failure)
                summary, rows = self.archived(failure)
                self.assertEqual(summary['attempts'], 1)
                self.assertEqual(rows[0]['validation'], 'ProtocolSuccess')

    async def test_success_closes_only_after_result_callback(self):
        async def adopted(_result):
            self.assertEqual(len(list(self.root.glob('.pending-*'))), 1)
            self.assertFalse(any(not p.name.startswith('.pending-') for p in self.root.iterdir()))
        result = await self.translate(on_result=adopted)
        self.assertEqual(''.join(result[0][0].itertext()), 'Right.Done.')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(len(self.model.wire_calls), 3)  # Artist once, fill twice.

    async def test_first_response_parse_and_validation_exceptions_are_archived(self):
        for method in ('parse', 'validate', 'feedback'):
            with self.subTest(method=method):
                failure = RuntimeError(f'{method} failed')
                model_replies(self.model, 'Right. Done.', [self.good])
                if method == 'parse':
                    context = patch.object(self.translator, '_extract_xml_element', side_effect=failure)
                elif method == 'validate':
                    context = patch.object(HillClimbing, 'validate', side_effect=failure)
                else:
                    # Real malformed inline IDs exercise feedback construction.
                    model_replies(self.model, 'Right. Done.', [
                        '<xml><p id="1"><b id="oops">Right.</b></p><p id="2">Done.</p></xml>'])
                    context = patch('pdf_craft.transformer.xml_translator.xml_translator.hill_climbing.generate_error_message',
                                    side_effect=failure)
                with context, self.assertRaises(RuntimeError) as caught:
                    await self.translate()
                self.assertIs(caught.exception, failure)
                summary, rows = self.archived(failure)
                self.assertEqual(summary['attempts'], 1)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['validation'], 'Exception')
                self.assertIn('<xml>', rows[0]['response'])

    async def test_invalid_inline_id_retries_instead_of_blank_runtime_error(self):
        model_replies(self.model, 'Right. Done.', [
            '<xml><p id="1"><b id="oops">Right.</b></p><p id="2">Done.</p></xml>', self.good])
        await self.translate()
        self.assertIn('Invalid inline ID', self.model.wire_calls[-1][-1].message)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_diagnostics_disabled_does_not_create_archive(self):
        self.translator._fill_llm.failure_log_path = None
        failure = OSError('storage failed')
        with patch('pdf_craft.transformer.xml_translator.xml_translator.translator.submit', side_effect=failure):
            with self.assertRaises(OSError) as caught:
                await self.translate()
        self.assertIs(caught.exception, failure)
        self.assertFalse(self.root.exists())
        self.assertIsNone(getattr(failure, 'failure_log_id', None))

    async def test_summary_write_failure_does_not_replace_submission_error(self):
        failure = OSError('business storage')
        retain = FailureLog.retain
        async def unavailable(log, error, attempts):
            log.path = self.root / 'unwritable'
            log.path.write_text('not a directory')
            return await retain(log, error, attempts)
        with patch.object(FailureLog, 'retain', unavailable), patch(
                'pdf_craft.transformer.xml_translator.xml_translator.translator.submit', side_effect=failure):
            with self.assertLogs('pdf_craft.llm.failure_log', level='WARNING'):
                with self.assertRaises(OSError) as caught:
                    await self.translate()
        self.assertIs(caught.exception, failure)
        self.assertFalse(list(self.root.glob('.pending-*')))

    async def test_concurrent_operations_do_not_share_archives(self):
        # Separate translators share a destination; neither owns the other's log.
        async def operation(label, fail):
            translator, model = translator_fixture()
            translator._fill_llm.failure_log_path = self.root
            model_replies(model, label, [f'<xml><p id="1">{label}</p><p id="2"/></xml>'])
            async def callback(_result):
                await asyncio.sleep(0)
                if fail:
                    raise ValueError(label)
            try:
                await translator._translate_elements([self.task()], on_result=callback)
            except ValueError as error:
                return error
            return None
        success_text = 'completed-operation-private-marker'
        failure_text = 'failed-operation-private-marker'
        good, bad = await asyncio.gather(operation(success_text, False), operation(failure_text, True))
        self.assertIsNone(good)
        assert bad is not None
        summary, rows = self.archived(bad)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        self.assertEqual(summary['artist_text'], failure_text)
        self.assertIn(failure_text, json.dumps(rows))
        self.assertNotIn(success_text, json.dumps(rows))

    async def test_later_operation_failure_does_not_retain_committed_loops(self):
        await self.translate()
        self.assertEqual(list(self.root.iterdir()), [])
        # An unrelated archive finalizer cannot recover or republish old logs.
        self.assertFalse(list(self.root.glob('*/summary.json')))

    async def test_cross_root_group_is_kept_until_both_submissions_finish(self):
        model_replies(self.model, 'A. B. C. D.', [
            '<xml><p id="1">A.</p><p id="2">B.</p><p id="3">C.</p><p id="4">D.</p></xml>'])
        tasks = [self.task(), self.task()]
        calls = []
        failure = OSError('second root submit')
        async def adopted(_result):
            calls.append(None)
            self.assertEqual(len(list(self.root.glob('.pending-*'))), 1)
            if len(calls) == 2:
                raise failure
        with self.assertRaises(OSError) as caught:
            await self.translator._translate_elements(tasks, on_result=adopted)
        self.assertIs(caught.exception, failure)
        summary, rows = self.archived(failure)
        self.assertEqual(summary['artist_text'], 'A. B. C. D.')
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(self.model.wire_calls), 2)

    async def test_detached_async_task_reorder_keeps_evidence(self):
        async def tasks():
            yield self.task()
        failure = OSError('detached task submission')
        async def adopted(_result):
            self.assertTrue(list(self.root.glob('.pending-*')))
            raise failure
        with self.assertRaises(OSError) as caught:
            await self.translator._translate_elements(tasks(), on_result=adopted, ordered_roots=False)
        self.assertIs(caught.exception, failure)
        _, rows = self.archived(failure)
        self.assertEqual(len(rows), 2)

    async def test_parallel_groups_in_one_operation_keep_only_failed_group(self):
        translator, model = translator_fixture(max_group_score=48)
        translator._fill_llm.failure_log_path = self.root
        tasks = [TranslationTask(fromstring(f'<body><p>{label}' + ' word' * 35 + '.</p></body>'),
                                 SubmitKind.REPLACE, label) for label in ('Alpha', 'Beta')]
        failure = OSError('Beta submission')
        async def adopted(result):
            if result[1] == 'Beta':
                raise failure
        with self.assertRaises(OSError) as caught:
            await translator._translate_elements(tasks, on_result=adopted, window=2)
        self.assertIs(caught.exception, failure)
        summary, rows = self.archived(failure)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        self.assertIn('Beta', summary['artist_text'])
        self.assertNotIn('Alpha', summary['artist_text'])
        self.assertNotIn('Alpha', json.dumps(rows))
        self.assertEqual(len(model.wire_calls), 4)  # Two groups, one artist/fill each.
