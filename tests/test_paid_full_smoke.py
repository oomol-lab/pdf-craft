"""The full paid runner cannot omit failures or expand OCR vendor scope."""
# pylint: disable=protected-access
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import AsyncMock, patch
import sys
import hashlib

from pdf_craft_tool.smoke import paid_full


class PaidFullSmokeTests(TestCase):
    def test_all_existing_routes_and_both_citation_modes_are_in_the_plan(self):
        cases = [entry for group in paid_full.MATRICES for entry in paid_full.matrix_cases(group)]
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({name for name, _ in cases}), 10)
        self.assertEqual({run.backend for _, run in cases}, {None, 'deepseek-ocr-vendor'})
        self.assertEqual(sum(run.asset.startswith('epub/') for _, run in cases), 2)
        self.assertEqual(sum(run.route == 'page-repair' for _, run in cases), 1)
        self.assertEqual(sum(run.route == 'pdf-patch' for _, run in cases), 1)

    def test_matrix_failure_does_not_skip_later_cases(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            calls = []
            def execute(run, **kwargs):
                calls.append(run)
                if len(calls) == 1:
                    raise RuntimeError('Simulated provider failure')
                path = kwargs['output_root']/'result'
                path.mkdir()
                (path/'manifest.json').write_text(json.dumps({'status': 'passed'}))
                return path
            with patch.object(paid_full.cli, '_resolve_matrix_runtime', side_effect=lambda run, _: run), \
                 patch.object(paid_full, 'run_smoke', side_effect=execute):
                entries = paid_full._matrix('table', output)
            self.assertEqual(len(calls), 5)
            self.assertEqual([row['status'] for row in entries], ['failed']+['passed']*4)

    def test_failed_refinement_does_not_skip_other_citation_mode_or_size(self):
        calls = []
        async def citation(_source, _output, _profile, **kwargs):
            calls.append(kwargs['footnotes'])
            if kwargs['footnotes']:
                raise ValueError('Original gate failed')
        async def size(*_args, **_kwargs):
            calls.append('size')
        with TemporaryDirectory() as directory, \
             patch.object(paid_full.citation_footnotes, 'run', citation), \
             patch.object(paid_full.pdf_size_growth, 'run', size):
            entries = asyncio.run(paid_full._refined(Path(directory), 'default'))
        self.assertEqual(calls, [True, False, 'size'])
        self.assertEqual([row['status'] for row in entries], ['failed', 'passed', 'passed'])

    def test_duplicate_rows_cannot_replace_a_missing_case(self):
        with TemporaryDirectory() as directory, patch.object(paid_full, 'source_fingerprint', return_value='fixed'):
            root = Path(directory)
            for group in paid_full.MATRICES:
                path = root/group
                path.mkdir()
                (path/'cases.json').write_text(json.dumps([
                    dict(name=name, status='passed') for name, _ in paid_full.matrix_cases(group)]))
            (root/'refined').mkdir()
            (root/'refined/cases.json').write_text(json.dumps([
                dict(name=name, status='passed') for name in ('citation-on', 'citation-off', 'pdf-size')]))
            (root/'formula').mkdir()
            (root/'formula/cases.json').write_text(json.dumps([
                dict(name='formula-three-rounds', status='passed')]))
            self.assertTrue(paid_full._collect(root, 'fixed')['complete'])
            path = root/'refined/cases.json'
            rows = json.loads(path.read_text())
            rows[-1]['name'] = 'citation-on'
            path.write_text(json.dumps(rows))
            self.assertFalse(paid_full._collect(root, 'fixed')['complete'])

    def test_each_vendor_can_run_without_calling_other_vendors(self):
        for backend in paid_full.OCR_BACKENDS:
            with self.subTest(backend=backend):
                selected = (backend,)
                cases = [entry for group in paid_full.MATRICES
                         for entry in paid_full.matrix_cases(group, selected)]
                self.assertEqual(len(cases), 10)
                self.assertEqual({run.backend for _, run in cases}, {None, backend})
                self.assertEqual(len(paid_full._expected_names(selected)), 14)
                self.assertEqual(sum(run.asset.startswith('epub/') for _, run in cases), 2)

    def test_multiple_vendors_have_distinct_cases_and_shared_llm_work(self):
        cases = [entry for group in paid_full.MATRICES
                 for entry in paid_full.matrix_cases(group, paid_full.OCR_BACKENDS)]
        self.assertEqual(len(cases), 24)
        self.assertEqual(len({name for name, _ in cases}), 24)
        self.assertEqual(sum(run.asset.startswith('epub/') for _, run in cases), 2)
        self.assertEqual(sum(run.route == 'page-repair' for _, run in cases), 1)
        self.assertEqual(len(paid_full._expected_names(paid_full.OCR_BACKENDS)), 34)
        for backend in paid_full.OCR_BACKENDS:
            self.assertEqual(sum(run.backend == backend for _, run in cases), 7)

    def test_refined_gates_receive_only_the_explicitly_selected_vendor(self):
        calls = []
        async def observe(*_args, **kwargs):
            calls.append(kwargs['ocr_mode'])
        selected = ('deepseek-ocr2-vendor', 'unlimited-ocr-vendor')
        with TemporaryDirectory() as directory, \
             patch.object(paid_full.citation_footnotes, 'run', observe), \
             patch.object(paid_full.pdf_size_growth, 'run', observe):
            entries = asyncio.run(paid_full._refined(Path(directory), 'default', selected))
        self.assertEqual(calls, [selected[0]]*3 + [selected[1]]*3)
        self.assertEqual(len({row['name'] for row in entries}), 6)
        self.assertTrue(all(row['status'] == 'passed' for row in entries))

    def test_cli_passes_backend_selection_to_parent_and_workers(self):
        selected = ['deepseek-ocr2-vendor', 'unlimited-ocr-vendor']
        base = ['paid_full', '--output', '/unused/output', '--ocr-backends', *selected]
        with patch.object(sys, 'argv', base), patch.object(paid_full, 'run', return_value=True) as run:
            with self.assertRaises(SystemExit) as result:
                paid_full.main()
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(run.call_args.args[-1], tuple(selected))
        with patch.object(sys, 'argv', base + ['--worker', 'refined']), \
             patch.object(paid_full, '_worker', return_value=True) as worker:
            with self.assertRaises(SystemExit) as result:
                paid_full.main()
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(worker.call_args.args[-1], tuple(selected))

    def test_invalid_selection_is_rejected_before_creating_output(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)/'not-created'
            for selected in ((), ('deepseek-ocr-local',), ('deepseek-ocr-vendor',)*2):
                with self.subTest(selected=selected), self.assertRaises(ValueError):
                    paid_full.run(output, backends=selected)
            self.assertFalse(output.exists())

    def test_backend_is_forwarded_by_the_real_citation_and_size_gates(self):
        selected = 'unlimited-ocr-vendor'
        for module in (paid_full.citation_footnotes, paid_full.pdf_size_growth):
            with self.subTest(module=module.__name__), TemporaryDirectory() as directory, \
                 patch.object(module, 'run_refined', new_callable=AsyncMock) as run:
                run.side_effect = RuntimeError('Stop before paid execution')
                with self.assertRaisesRegex(RuntimeError, 'Stop before paid execution'):
                    asyncio.run(module.run(module.SOURCE, Path(directory)/'new', 'default',
                                           ocr_mode=selected))
                self.assertEqual(run.call_args.kwargs['ocr_mode'], selected)

    def test_shared_refinement_passes_selected_backend_to_configuration_factory(self):
        module = paid_full.deepseek_refined
        source = paid_full.citation_footnotes.SOURCE
        data = source.read_bytes()
        with TemporaryDirectory() as directory, \
             patch.object(module, 'load_project_env'), \
             patch.object(module, 'create_llm_from_env'), \
             patch.object(module, 'create_jev_from_env'), \
             patch.object(module, 'create_ocr_config_from_env',
                          side_effect=RuntimeError('Selected OCR configuration')) as configure:
            with self.assertRaisesRegex(RuntimeError, 'Selected OCR configuration'):
                asyncio.run(module.run(source, Path(directory)/'new', 'default',
                                       source_sha256=hashlib.sha256(data).hexdigest(),
                                       source_bytes=len(data), pages=3, ocr_mode='deepseek-ocr2-vendor'))
            configure.assert_called_once_with('deepseek-ocr2-vendor')

    def test_selected_vendor_coverage_must_be_complete(self):
        selected = ('deepseek-ocr2-vendor', 'unlimited-ocr-vendor')
        with TemporaryDirectory() as directory, patch.object(paid_full, 'source_fingerprint', return_value='fixed'):
            root = Path(directory)
            for group in paid_full.MATRICES:
                path = root/group
                path.mkdir()
                (path/'cases.json').write_text(json.dumps([
                    dict(name=name, status='passed') for name, _ in paid_full.matrix_cases(group, selected)]))
            (root/'refined').mkdir()
            (root/'refined/cases.json').write_text(json.dumps([
                dict(name=name, status='passed') for name, _, _ in paid_full._refined_cases(selected)]))
            (root/'formula').mkdir()
            (root/'formula/cases.json').write_text(json.dumps([
                dict(name='formula-three-rounds', status='passed')]))
            result = paid_full._collect(root, 'fixed', selected)
            self.assertTrue(result['complete'])
            self.assertEqual(result['passed'], 24)
            path = root/'table/cases.json'
            rows = json.loads(path.read_text())
            path.write_text(json.dumps(rows[:-1]))
            self.assertFalse(paid_full._collect(root, 'fixed', selected)['complete'])
