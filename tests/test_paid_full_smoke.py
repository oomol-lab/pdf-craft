"""The full paid runner cannot omit failures or expand OCR vendor scope."""
# pylint: disable=protected-access
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

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
        async def size(*_args):
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
