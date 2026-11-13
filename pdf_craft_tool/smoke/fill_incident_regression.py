"""Local Nov-10 fill incident gate: five recorded answers and five live fills.

Private fixtures stay outside Git. No OCR, artist request, or whole-book smoke.
The gate exercises production parsing, validation, formula projection and submit;
records every provider request and feedback, then serializes the adopted group.
"""
# pylint: disable=protected-access,unused-argument
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from zipfile import ZipFile, ZIP_STORED

from pdf_craft import ConcurrentExecutor, FixedCapacity, RetryPolicy
from pdf_craft.llm.runtime import LLMRuntime
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.xml_translator.xml_translator import XMLTranslator, TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.submitter import SubmitKind
from ..runtime import load_project_env, create_llm_from_env, model_request_options
from .fill_regression_cases import RETIRED_FILL_CASES, regular_fill_cases
from ._incident_fixtures import WHITESPACE, CONTEXT, load_case, source_math, materialize_source, EXTRA

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fingerprint():
    digest = hashlib.sha256()
    paths = list((ROOT / 'pdf_craft').rglob('*.py')) + list((ROOT / 'pdf_craft').rglob('*.jinja'))
    paths += [Path(__file__), Path(__file__).with_name('_incident_fixtures.py')]
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class RecordingRuntime(LLMRuntime):
    """Transport-only recording/replay; production validators remain untouched."""
    def __init__(self, original, output, replay, *, live_repairs=True):
        super().__init__(original.config, executor=original.executor, protocol_version=original.protocol_version)
        self.output, self.replay, self.calls = output, replay, 0
        self.live_repairs = live_repairs
        self.last_response = ''

    async def _invoke_async(self, messages, max_tokens, temperature, top_p, *, force_ipv4=False):
        self.calls += 1
        path = self.output / f'request-{self.calls:02}.json'
        record = {'messages': [{'role': item.role.name, 'content': item.message} for item in messages],
                  'max_tokens': max_tokens, 'temperature': temperature, 'top_p': top_p,
                  'mode': 'saved-answer' if self.replay is not None and (self.calls == 1 or not self.live_repairs) else 'live-fill'}
        save(path, record)
        try:
            response = self.replay if self.replay is not None and (self.calls == 1 or not self.live_repairs) else await super()._invoke_async(
                messages, max_tokens, temperature, top_p, force_ipv4=force_ipv4)
            self.last_response = response
            record['response'] = response
            return response
        except Exception as error:
            record['error_type'] = type(error).__name__
            raise
        finally:
            save(path, record)


def publish_group(target, output):
    # A real, reopenable local EPUB carries the COMPLETE selected group. This
    # is not a claim that unrelated chapters or the service upload were tested.
    html = Element('html', xmlns='http://www.w3.org/1999/xhtml')
    head = SubElement(html, 'head')
    SubElement(head, 'title').text = 'Incident regression'
    body = SubElement(html, 'body')
    article = deepcopy(target)
    article.tag = 'article'
    for math in article.iter('math'):
        math.set('xmlns', 'http://www.w3.org/1998/Math/MathML')
    body.append(article)
    payload = tostring(html, encoding='utf-8', xml_declaration=True)
    with ZipFile(output / 'adopted.epub', 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=ZIP_STORED)
        archive.writestr('META-INF/container.xml', '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr('package.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book">incident</dc:identifier><dc:title>Incident regression</dc:title><dc:language>und</dc:language><meta property="dcterms:modified">2026-11-10T00:00:00Z</meta></metadata><manifest><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="content"/></spine></package>')
        archive.writestr('content.xhtml', payload)
        archive.writestr('nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="content.xhtml">Incident</a></li></ol></nav></body></html>')
    with ZipFile(output / 'adopted.epub') as archive:
        reopened = fromstring(archive.read('content.xhtml'))
        if sum(node.tag == '{http://www.w3.org/1998/Math/MathML}math' for node in reopened.iter()) != sum(node.tag == 'math' for node in target.iter()):
            raise AssertionError('Published formulas lost their MathML namespace')
        if tostring(reopened) != tostring(fromstring(payload)):
            raise AssertionError('EPUB materialization did not round trip')


def historical_answer(data):
    """Expand retired annotations in a SAVED answer, not in production."""
    from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly, encode_friendly
    root = next(decode_friendly(data['rounds'][-1]['candidate'], tags='xml'))
    values = data['artist_math_values']
    def walk(parent):
        previous = None
        for child in list(parent):
            walk(child)
            if child.tag in ('math-ref', 'source-math'):
                value = values[child.get('key')] if child.tag == 'math-ref' else child.text or ''
                if previous is None:
                    parent.text = (parent.text or '') + value
                else:
                    previous.tail = (previous.tail or '') + value
                index = list(parent).index(child)
                for nested in list(child):
                    child.remove(nested)
                    parent.insert(index, nested)
                    index += 1
                    previous = nested
                if previous is None:
                    parent.text = (parent.text or '') + (child.tail or '')
                else:
                    previous.tail = (previous.tail or '') + (child.tail or '')
                parent.remove(child)
            else:
                previous = child
    walk(root)
    return encode_friendly(root)


async def case(base, key, output, llm, *, recorded_fill=None):
    from dataclasses import asdict
    from pdf_craft import UsageCollector
    from pdf_craft.transformer.fill_text import plain_translation, comparison_text
    output.mkdir()
    evidence = base / 'text-cases' / f'{key}.json'
    if not evidence.exists():
        evidence = base / 'unpacked/analysis/0/llm-failures' / key / 'attempts.jsonl'
    result = {'id': key, 'passed': False, 'max_attempts': 13,
              'input_sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}
    runtime, usage = None, UsageCollector()
    try:
        data, source, segments, _hill = load_case(base, key)
        math, displays, provenance = source_math(base, key, data, source, segments)
        source = materialize_source(source, math, displays)
        if any(node.tag == 'expression' for node in source.iter()):
            raise AssertionError('Reconstructed source contains a temporary formula')
        initial_count = len(source)
        # The artist is fixed at the original response, not called or rewritten.
        class FixedArtist(XMLTranslator):
            async def _translate_text_async(self, text, *, context=''):
                return data['canonical']
        translator = FixedArtist(llm, llm, 'Chinese' if math else 'Portuguese', None, 10, 1000000,
                                executor=ConcurrentExecutor(FixedCapacity(1)))
        replay = recorded_fill if recorded_fill is not None else (historical_answer(data) if key in WHITESPACE else None)
        runtime = RecordingRuntime(translator._fill_runtime, output, replay, live_repairs=recorded_fill is None)
        translator._fill_runtime = runtime
        protocol = StructuredTranslation(formulas=XMLInterrupter() if math else None)
        async def tasks():
            yield TranslationTask(source, SubmitKind.REPLACE, None)
        protocol.prepare(source)
        results = await translator.translate_elements(tasks(), **protocol.arguments(),
            error_policy=False, retry_policy=RetryPolicy(fill=12), usage=usage,
            **model_request_options())
        target, _ = results[0]
        protocol.finish(target)
        if target.tag != 'body' or len(target) != initial_count:
            raise AssertionError('Delivery lost the body or source blocks')
        actual = protocol.formulas.visible_text(target) if protocol.formulas else ''.join(target.itertext())
        if comparison_text(actual) != comparison_text(plain_translation(data['canonical'])):
            raise AssertionError('Delivered content differs from the shared artist view')
        if any(node.tag in ('expression', 'source-math', 'math-ref', 'boundary') for node in target.iter()):
            raise AssertionError('Temporary annotations escaped final delivery')
        (output / 'delivered.xhtml').write_text(tostring(target, encoding='unicode'))
        publish_group(target, output)
        result.update(passed=True, delivery_verified=True, adopted_blocks=len(target),
                      source_math_provenance=provenance,
                      canonical_sha256=hashlib.sha256(data['canonical'].encode()).hexdigest(),
                      view_sha256=hashlib.sha256(plain_translation(data['canonical']).encode()).hexdigest())
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
    result['fixture_unchanged'] = hashlib.sha256(evidence.read_bytes()).hexdigest() == result['input_sha256']
    if not result['fixture_unchanged']:
        result['passed'] = False
    result.update(fill_requests=runtime.calls if runtime else 0,
                  live_fill_requests=(runtime.calls - (1 if runtime.replay is not None else 0)) if runtime and runtime.live_repairs else 0,
                  translation_requests=0,
                  fill_mode='accepted-answer-offline-replay' if recorded_fill is not None else ('saved-first-answer-then-live-repair' if key in WHITESPACE else 'live'),
                  usage=[asdict(value) for value in usage.report().values()])
    save(output / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


async def run(fixtures, output, profile, cases=None):
    requested = tuple(cases) if cases else WHITESPACE + CONTEXT + EXTRA
    selected = regular_fill_cases(requested)
    if not selected:
        raise ValueError('No active fill regression cases selected; retired cases are not passing tests.')
    output.mkdir(parents=True, exist_ok=False)
    load_project_env(ROOT)
    llm = create_llm_from_env(profile, cache_path=output / 'unused-cache', log_dir_path=output / 'request-status')
    llm.cache_path = None
    report = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'model': llm.model, 'profile': profile, 'temperature': llm.temperature,
              'top_p': llm.top_p, 'source_sha256': fingerprint(),
              'passed': False, 'results': []}
    report['retired_cases'] = {key: RETIRED_FILL_CASES[key] for key in requested if key in RETIRED_FILL_CASES}
    for key in selected:
        report['results'].append(await case(fixtures, key, output / key, llm))
        save(output / 'report.json', report)
    report['usage_totals'] = {name: sum(usage[kind]['input_tokens'] + usage[kind]['output_tokens']
        for item in report['results'] for usage in item['usage'])
        for name, kind in [('effective_tokens', 'effective'), ('wasted_tokens', 'wasted')]}
    report['source_unchanged'] = fingerprint() == report['source_sha256']
    report['passed'] = (report['source_unchanged'] and len(report['results']) == len(selected)
                        and all(item['passed'] for item in report['results']))
    save(output / 'report.json', report)
    return report['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', default='fill')
    parser.add_argument('--cases', nargs='+', choices=regular_fill_cases(WHITESPACE + CONTEXT + EXTRA))
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.fixtures.resolve(), args.output.resolve(), args.profile, args.cases)) else 1)


if __name__ == '__main__':
    main()
