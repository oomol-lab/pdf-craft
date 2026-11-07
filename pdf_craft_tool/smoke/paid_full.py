"""Opt-in complete paid regression, using only first-generation DeepSeek OCR.

Run with --output NEW-DIRECTORY. Every case runs even if another case fails.
Existing gate assertions are the acceptance criteria; this module orchestrates
and records them without changing model messages, answers or validation.
"""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from unittest.mock import patch

from pdf_craft.llm.runtime import LLMContext
from ..runtime import load_project_env
from .runner import expand_matrix, run_smoke
from . import citation_footnotes, deepseek_refined, fill_formula_loop, pdf_size_growth
from .. import cli

REPO = Path(__file__).resolve().parents[2]
BACKEND = 'deepseek-ocr-vendor'
MATRICES = {'vendor': ('vendor_real', 4), 'table': ('table_formula_real', 5),
            'page-repair': ('citation_large_page_repair', 1)}
WORKERS = (*MATRICES, 'refined', 'formula')


def _save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def matrix_cases(group):
    """Select configurations by backend, never by historical array offsets."""
    config, expected = MATRICES[group]
    values = json.loads((REPO / f'tests/smoke/{config}.json').read_text(encoding='utf-8'))
    runs = [run for run in expand_matrix(values, REPO/'tests/assets')
            if run.backend in (None, BACKEND)]
    if len(runs) != expected:
        raise ValueError(f'{config}: expected {expected} DeepSeek/LLM cases, got {len(runs)}')
    return [(f'{config}-{Path(run.asset).stem}-{run.route}-'
             f'{"translated" if run.translation else "source"}', run) for run in runs]


def source_fingerprint():
    digest = hashlib.sha256()
    paths = [*REPO.joinpath('pdf_craft').rglob('*.py'), *REPO.joinpath('pdf_craft').rglob('*.jinja'),
             *REPO.joinpath('pdf_craft_tool').rglob('*.py'), *REPO.joinpath('tests/smoke').glob('*.json'),
             REPO/'tests/assets/translation/fill_formula_cases.json']
    for path in sorted(paths):
        digest.update(str(path.relative_to(REPO)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@contextmanager
def _requests(output):
    """Record effective request limits, not credentials or document content."""
    original = LLMContext.request
    async def observe(context, *args, **kwargs):
        row = {'protocol': context.runtime.protocol_version,
               'retry_index': kwargs.get('retry_index'), 'retry_max': kwargs.get('retry_max')}
        with (output/'requests.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
        return await original(context, *args, **kwargs)
    with patch.object(LLMContext, 'request', observe):
        yield


def _matrix(group, output):
    entries = []
    for name, spec in matrix_cases(group):
        case = output/name
        case.mkdir()
        row = {'name': name, 'backend': spec.backend, 'route': spec.route, 'status': 'running'}
        entries.append(row)
        _save(output/'cases.json', entries)
        started = monotonic()
        try:
            resolved = cli._resolve_matrix_runtime(spec, case)  # pylint: disable=protected-access
            for settings in (resolved.translation, resolved.page_repair):
                if settings:
                    for key in ('llm', 'fill_llm'):
                        if isinstance(settings.get(key), dict):
                            settings[key]['failure_log_path'] = case/'llm-failures'
            # Preserve the established paid suite's provider PNG limit.
            if spec.backend:
                resolved = replace(resolved, max_page_image_file_size=10_000_000)
            target = run_smoke(resolved, assets_root=REPO/'tests/assets', output_root=case)
            manifest = json.loads((target/'manifest.json').read_text(encoding='utf-8'))
            row.update(status=manifest['status'], output=str(target))
        except (Exception, SystemExit) as error:
            row.update(status='failed', error_type=type(error).__name__, output=str(case))
        row['elapsed_seconds'] = round(monotonic()-started, 2)
        _save(output/'cases.json', entries)
    return entries


async def _refined(output, profile):
    entries = []
    original = deepseek_refined.create_llm_from_env
    def evidence_llm(*args, **kwargs):
        result = original(*args, **kwargs)
        result.failure_log_path = Path(kwargs['log_dir_path']).parent/'llm-failures'
        return result
    original_options = deepseek_refined.ExtractionOptions
    def bounded_options(*args, **kwargs):
        kwargs['max_page_image_file_size'] = 10_000_000
        return original_options(*args, **kwargs)
    cases = [('citation-on', citation_footnotes.run, citation_footnotes.SOURCE, {'footnotes': True}),
             ('citation-off', citation_footnotes.run, citation_footnotes.SOURCE, {'footnotes': False}),
             ('pdf-size', pdf_size_growth.run, pdf_size_growth.SOURCE, {})]
    with patch.object(deepseek_refined, 'create_llm_from_env', evidence_llm), \
         patch.object(deepseek_refined, 'ExtractionOptions', bounded_options):
        for name, function, source, options in cases:
            row = {'name': name, 'status': 'running', 'output': str(output/name)}
            entries.append(row)
            _save(output/'cases.json', entries)
            started = monotonic()
            try:
                await function(source, output/name, profile, **options)
                row['status'] = 'passed'
            except Exception as error:
                row.update(status='failed', error_type=type(error).__name__)
            row['elapsed_seconds'] = round(monotonic()-started, 2)
            _save(output/'cases.json', entries)
    return entries


def _worker(group, output, profile):
    load_project_env(REPO)
    with _requests(output):
        if group in MATRICES:
            entries = _matrix(group, output)
        elif group == 'refined':
            entries = asyncio.run(_refined(output, profile))
        else:
            passed = asyncio.run(fill_formula_loop.run(output/'three-rounds', profile))
            entries = [{'name': 'formula-three-rounds', 'status': 'passed' if passed else 'failed',
                        'output': str(output/'three-rounds')}]
            _save(output/'cases.json', entries)
    return all(row['status'] == 'passed' for row in entries)


def _collect(output, fingerprint):
    cases = []
    for group in WORKERS:
        path = output/group/'cases.json'
        if path.exists():
            cases.extend(json.loads(path.read_text(encoding='utf-8')))
    passed = sum(row['status'] == 'passed' for row in cases)
    return {'cases': cases, 'passed': passed, 'failed': len(cases)-passed,
            'complete': (len(cases) == 14 and {row['name'] for row in cases} ==
                         {name for group in MATRICES for name, _ in matrix_cases(group)}
                         | {'citation-on', 'citation-off', 'pdf-size', 'formula-three-rounds'}
                         and all(row['status'] != 'running' for row in cases)),
            'source_unchanged': source_fingerprint() == fingerprint}


def run(output, profile='default'):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    plan = [dict(name=name, config=asdict(config)) for group in MATRICES for name, config in matrix_cases(group)]
    _save(output/'plan.json', {'matrix': plan, 'other': ['citation-on', 'citation-off', 'pdf-size',
                                                       'formula-three-rounds'], 'ocr_allowlist': [BACKEND]})
    fingerprint = source_fingerprint()
    commit = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    report = {'commit': commit, 'source_fingerprint': fingerprint, 'status': 'running', 'workers': {}}
    processes, logs = {}, {}
    started = monotonic()
    try:
        for group in WORKERS:
            destination = output/group
            destination.mkdir()
            logs[group] = (output/f'{group}.log').open('w', encoding='utf-8')
            command = [sys.executable, '-u', '-m', 'pdf_craft_tool.smoke.paid_full',
                       '--output', str(destination), '--profile', profile, '--worker', group]
            environment = dict(os.environ, PYTHONPATH=str(REPO), PYTHONUNBUFFERED='1')
            processes[group] = subprocess.Popen(command, cwd=REPO, env=environment,
                                                stdout=logs[group], stderr=subprocess.STDOUT)
        while True:
            for group, process in processes.items():
                code = process.poll()
                report['workers'][group] = {'status': 'running' if code is None else
                                            'passed' if code == 0 else 'failed', 'exit_code': code}
            report.update(_collect(output, fingerprint))
            report['elapsed_seconds'] = round(monotonic()-started, 2)
            _save(output/'report.json', report)
            if all(process.poll() is not None for process in processes.values()):
                break
            sleep(2)
        success = (report['complete'] and report['failed'] == 0 and report['source_unchanged']
                   and all(process.returncode == 0 for process in processes.values()))
        report['status'] = 'passed' if success else 'failed'
        _save(output/'report.json', report)
        return success
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in logs.values():
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--worker', choices=WORKERS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    passed = _worker(args.worker, args.output, args.profile) if args.worker else run(args.output, args.profile)
    raise SystemExit(0 if passed else 1)


if __name__ == '__main__':
    main()
