"""Paid acceptance gate for three real paragraphs; never part of default tests.

Run ``python -m pdf_craft_tool.smoke.fill_formula_loop --output NEW-DIRECTORY``
from a configured repository. Three consecutive complete suites must pass.
"""
import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from unittest.mock import patch
from xml.etree.ElementTree import fromstring, tostring

from pdf_craft import ConcurrentExecutor, FixedCapacity, RetryPolicy
from pdf_craft.extractor.chapter.chapter import (
    Chapter, InlineExpression, SourceAsset, SourceTextFragment, TextFlowItem, decode, encode,
)
from pdf_craft.llm import Message, MessageRole
from pdf_craft.llm.loop import run_repair_loop
from pdf_craft.llm.runtime import LLMContext
from pdf_craft.transformer.narrative_xml import NarrativeXMLTransformer
from pdf_craft.transformer.xml_translator.xml_translator.translator import XMLTranslator
from ..runtime import create_llm_from_env, load_project_env

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'tests/assets/translation/fill_formula_cases.json'
TEMPERATURE = 0.7
TOP_P = 1.0
ROUNDS = 3


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _fingerprint():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'pdf_craft').rglob('*')):
        if path.is_file() and path.suffix in ('.py', '.jinja'):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    digest.update(FIXTURE.read_bytes())
    digest.update(Path(__file__).read_bytes())
    return digest.hexdigest()


def _formulas(chapter: Chapter):
    return Counter((part.kind.name, part.content) for item in chapter.flow_items if isinstance(item, TextFlowItem)
                   for child in item.children if isinstance(child, SourceTextFragment)
                   for part in child.content if isinstance(part, InlineExpression))


def _geometry(chapter):
    return [(child.page_index, child.source_order, child.bbox)
            for item in chapter.flow_items if isinstance(item, TextFlowItem)
            for child in item.children if isinstance(child, SourceTextFragment)]


class _RecordingProtocol:
    def __init__(self, protocol, path):
        self.protocol = protocol
        self.path = path

    async def _record(self, operation, response, state, attempt, maximum):
        result = (await self.protocol.empty(state, attempt, maximum) if operation == 'empty'
                  else await self.protocol.validate(response, state, attempt, maximum))
        row = {'attempt': attempt + 1, 'max_attempts': maximum,
               'outcome': type(result).__name__, 'feedback': getattr(result, 'feedback', None)}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        return result

    async def validate(self, response, state, attempt, maximum):
        return await self._record('validate', response, state, attempt, maximum)

    async def empty(self, state, attempt, maximum):
        return await self._record('empty', '', state, attempt, maximum)

    async def exhausted(self, *args):
        return await self.protocol.exhausted(*args)


class _Recorder:
    """Observe production requests and validation; never implement acceptance."""

    def __init__(self, directory):
        self.directory = directory
        self.requests = []
        self.groups = 0
        self._request = LLMContext.request

    async def request(self, context, *args, **kwargs):
        if 'input' in kwargs:
            value, remaining = kwargs.pop('input'), args
        else:
            value, *remaining = args
        messages = [Message(MessageRole.USER, value)] if isinstance(value, str) else value
        row = {'phase': context.runtime.protocol_version,
               'messages': [{'role': m.role.name, 'content': m.message} for m in messages],
               'temperature': TEMPERATURE, 'top_p': TOP_P,
               'retry_index': kwargs.get('retry_index'), 'retry_max': kwargs.get('retry_max')}
        self.requests.append(row)
        path = self.directory / f'request-{len(self.requests):03}.json'
        _save(path, row)
        try:
            response = await self._request(context, value, *remaining, **kwargs)
            row['response'] = response
            return response
        except BaseException as error:
            row['error_type'] = type(error).__name__
            raise
        finally:
            _save(path, row)

    async def loop(self, options):
        self.groups += 1
        options.protocol = _RecordingProtocol(options.protocol, self.directory / f'validation-{self.groups}.jsonl')
        return await run_repair_loop(options)

    def summary(self):
        return {'translation_requests': sum(r['phase'] == 'xml-translation-v1' for r in self.requests),
                'fill_requests': sum(r['phase'] == 'xml-fill-v1' for r in self.requests),
                'groups': self.groups}


async def _case(case, directory, llm, executor):
    directory.mkdir()
    source = decode(fromstring(case['source_xml']), allow_legacy=False)
    translator = XMLTranslator(llm, llm, 'zh-CN', None, 3, 2600, executor=executor,
                               retry_policy=RetryPolicy(translation=8, analysis=6, fill=12), error_policy=False)
    recorder = _Recorder(directory)
    async def request(context, *args, **kwargs):
        return await recorder.request(context, *args, **kwargs)
    try:
        with patch.object(LLMContext, 'request', request), patch(
            'pdf_craft.transformer.xml_translator.xml_translator.translator.run_repair_loop', recorder.loop,
        ):
            target = await NarrativeXMLTransformer(translator, window=2).transform(deepcopy(source), error_policy=False)
        if any(getattr(item, '_translation_missing', False) for item in target.flow_items):
            raise AssertionError('Missing translation cannot pass this gate')
        if _formulas(target) != _formulas(source) or _geometry(target) != _geometry(source):
            raise AssertionError('Post-loop restoration changed source formulas or geometry')
        def assets(chapter):
            return [child for item in chapter.flow_items if isinstance(item, TextFlowItem)
                    for child in item.children if isinstance(child, SourceAsset)]
        if assets(target) != assets(source):
            raise AssertionError('Anchored content changed')
        xml = tostring(encode(target), encoding='unicode')
        if '<expression' in xml or '<anchor' in xml or '__PDF_CRAFT_CHAPTER_FORMULA' in xml:
            raise AssertionError('Temporary translation nodes escaped post-processing')
        if decode(fromstring(xml), allow_legacy=False) != target:
            raise AssertionError('Translated PCEX cannot round-trip')
        if not recorder.requests or not recorder.groups:
            raise AssertionError('Production translation/fill was not exercised')
        (directory / 'accepted.xml').write_text(xml, encoding='utf-8')
        result = {'case': case['name'], 'passed': True, **recorder.summary()}
    except Exception as error:
        # Model/client configuration and credentials never become serialized state.
        result = {'case': case['name'], 'passed': False, 'error_type': type(error).__name__, **recorder.summary()}
    _save(directory / 'result.json', result)
    print(json.dumps(result), flush=True)
    return result


async def run(output: Path, profile: str):
    output.mkdir(parents=True, exist_ok=False)
    load_project_env(ROOT)
    llm = create_llm_from_env(profile, cache_path=output / 'unused-cache', log_dir_path=output / 'request-status')
    llm.cache_path = None  # Fresh first-stage and fill responses in every round.
    llm.temperature, llm.top_p = TEMPERATURE, TOP_P
    executor = ConcurrentExecutor(FixedCapacity(1))
    fingerprint = _fingerprint()
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    cases = json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']
    report = {'commit': commit, 'source_fingerprint': fingerprint, 'temperature': TEMPERATURE,
              'top_p': TOP_P, 'model': llm.model, 'passed': False, 'streak': 0, 'rounds': []}
    _save(output / 'acceptance.json', report)
    for number in range(1, ROUNDS + 1):
        directory = output / f'round-{number}'
        directory.mkdir()
        results = [await _case(case, directory / case['name'], llm, executor) for case in cases]
        passed = len(results) == 3 and all(r['passed'] for r in results) and _fingerprint() == fingerprint
        report['rounds'].append({'number': number, 'passed': passed, 'results': results})
        report['streak'] = report['streak'] + 1 if passed else 0
        report['passed'] = report['streak'] == ROUNDS
        _save(output / 'acceptance.json', report)
        if not passed:
            return False
    return report['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', default='default')
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.output.resolve(), args.profile)) else 1)


if __name__ == '__main__':
    main()
