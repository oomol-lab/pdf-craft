"""Local Nov-10 fill incident gate: five recorded answers and five live fills.

Private fixtures stay outside Git. No OCR, artist request, or whole-book smoke.
The gate exercises production parsing, validation, formula projection and submit;
records every provider request and feedback, then serializes the adopted group.
"""
# pylint: disable=protected-access,unused-argument
import argparse
import asyncio
from copy import deepcopy
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring
from zipfile import ZipFile, ZIP_STORED

from pdf_craft import ConcurrentExecutor, FixedCapacity, RetryPolicy
from pdf_craft._policy import policy_operation
from pdf_craft.llm.runtime import LLMRuntime
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.structured_translation import StructuredTranslation
from pdf_craft.transformer.canonical_math import CanonicalMath
from pdf_craft.common.whitespace import normalize_whitespace
from pdf_craft.transformer.xml_translator.segment import combine_text_segments
from pdf_craft.transformer.xml_translator.xml_translator import XMLTranslator, TranslationTask
from pdf_craft.transformer.xml_translator.xml_translator.callbacks import warp_callbacks
from pdf_craft.transformer.xml_translator.xml_translator.submitter import submit, SubmitKind
from ..runtime import load_project_env, create_llm_from_env, model_request_options
from ._incident_fixtures import WHITESPACE, CONTEXT, load_case, source_math, materialize_source

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
    def __init__(self, original, output, replay):
        super().__init__(original.config, executor=original.executor, protocol_version=original.protocol_version)
        self.output, self.replay, self.calls = output, replay, 0
        self.last_response = ''

    async def _invoke_async(self, messages, max_tokens, temperature, top_p, *, force_ipv4=False):
        self.calls += 1
        path = self.output / f'request-{self.calls:02}.json'
        record = {'messages': [{'role': item.role.name, 'content': item.message} for item in messages],
                  'max_tokens': max_tokens, 'temperature': temperature, 'top_p': top_p,
                  'mode': 'saved-answer' if self.replay is not None else 'live-fill'}
        save(path, record)
        try:
            response = self.replay if self.replay is not None else await super()._invoke_async(
                messages, max_tokens, temperature, top_p, force_ipv4=force_ipv4)
            self.last_response = response
            record['response'] = response
            return response
        except Exception as error:
            record['error_type'] = type(error).__name__
            raise
        finally:
            save(path, record)


def adopt(source, mappings, output):
    """Submit the complete projected group and verify every adopted block.

    The enclosing document is a local chapter, not the entire private book.
    Every original group block is included. Root replacement, omitted blocks,
    dropped code gaps and missing typed math fail before EPUB serialization.
    """
    if not mappings or any(item is None for item in mappings):
        raise AssertionError('Partial/fallback mappings cannot pass')
    expected = []
    for block, fields in mappings:
        combined = list(combine_text_segments(item.clone().strip_block_parents() for item in fields))
        if len(combined) != 1:
            save(output / 'mapping-mismatch.json', {'block': tostring(block, encoding='unicode'), 'fields': [{'text': item.text, 'parents': [str(n.attrib) + n.tag for n in item.parent_stack], 'depth': item.block_depth} for item in fields]})
            raise AssertionError(f'Incident block {block.tag}#{block.get("id")} materialized {len(combined)} roots from {len(fields)} fields')
        expected.append(tostring(combined[0][0]))
    source.tag = 'article'
    adopted = submit(source, SubmitKind.REPLACE, mappings)
    if adopted is not source or len(adopted) != len(expected):
        raise AssertionError('Adoption replaced the local document root or lost a block')
    # Block IDs in the saved request are engineering IDs. Remove nothing before
    # checking materialization: the expected projected nodes include them too.
    for index, (actual, wanted) in enumerate(zip(adopted, expected, strict=True)):
        candidate = deepcopy(actual)
        candidate.tail = None  # preserved source indentation lies outside this adopted block
        if tostring(candidate) != wanted:
            save(output / 'adoption-mismatch.json', {'index': index, 'actual': tostring(actual, encoding='unicode'), 'expected': wanted.decode()})
            raise AssertionError('Submit changed projected text, code whitespace or typed math')
    if any(node.tag in ('source-math', 'math-ref', 'boundary') for node in adopted.iter()):
        raise AssertionError('Engineering annotations escaped adoption')
    xml = tostring(adopted, encoding='unicode')
    (output / 'adopted.xml').write_text(xml)
    return {'adopted_blocks': len(expected), 'typed_math_nodes': sum(n.tag == 'math' for n in adopted.iter()),
            'scope': 'complete selected group, production submit and local EPUB round trip; not whole-book service publication'}


def publish_group(target, output):
    # A real, reopenable local EPUB carries the COMPLETE selected group. This
    # is not a claim that unrelated chapters or the service upload were tested.
    html = Element('html', xmlns='http://www.w3.org/1999/xhtml')
    head = SubElement(html, 'head')
    SubElement(head, 'title').text = 'Incident regression'
    body = SubElement(html, 'body')
    article = deepcopy(target)
    article.tag = 'article'
    body.append(article)
    payload = tostring(html, encoding='utf-8', xml_declaration=True)
    with ZipFile(output / 'adopted.epub', 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip', compress_type=ZIP_STORED)
        archive.writestr('META-INF/container.xml', '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        archive.writestr('package.opf', '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book">incident</dc:identifier><dc:title>Incident regression</dc:title><dc:language>und</dc:language><meta property="dcterms:modified">2026-11-10T00:00:00Z</meta></metadata><manifest><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="content"/></spine></package>')
        archive.writestr('content.xhtml', payload)
        archive.writestr('nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="content.xhtml">Incident</a></li></ol></nav></body></html>')
    with ZipFile(output / 'adopted.epub') as archive:
        if tostring(fromstring(archive.read('content.xhtml'))) != tostring(fromstring(payload)):
            raise AssertionError('EPUB materialization did not round trip')


def prose(root):
    def walk(node):
        if node.tag == 'math':
            return ''
        return (node.text or '') + ''.join(walk(child) + (child.tail or '') for child in node)
    text = walk(root)
    for match in reversed(list(CanonicalMath.matches(text))):
        text = text[:match.start()] + text[match.end():]
    return normalize_whitespace(text, trim=True)


async def verify_delivery(data, original, math, display_nodes, accepted, llm, output, reference):
    """Replay accepted fill through the normal transformer and formula adapter.

    Only artist/provider transports are fixed. This second offline pass also
    exercises source interruption, real mapping callbacks, submit and finish.
    """
    source = materialize_source(original, math, display_nodes)

    class FixedArtist(XMLTranslator):
        async def _translate_text_async(self, text, *, context=''):
            return data['canonical']

    translator = FixedArtist(llm, llm, 'Chinese' if math else 'Portuguese', None, 10, 1000000,
                            executor=ConcurrentExecutor(FixedCapacity(1)))
    replay_output = output / 'delivery-replay'
    replay_output.mkdir()
    runtime = RecordingRuntime(translator._fill_runtime, replay_output, accepted)
    translator._fill_runtime = runtime
    protocol = StructuredTranslation(formulas=XMLInterrupter() if math else None)
    target, _ = await protocol.translate(translator, TranslationTask(source, SubmitKind.REPLACE, None),
                                         error_policy=False)
    (output / 'delivered.xhtml').write_text(tostring(target, encoding='unicode'))
    if target.tag != 'body' or len(target) != len(reference):
        raise AssertionError('Normal delivery lost the selected document root or blocks')
    if any(node.tag in ('expression', 'source-math', 'math-ref', 'boundary') for node in target.iter()):
        raise AssertionError('Temporary engineering elements escaped normal delivery')
    if [''.join(node.itertext()) for node in target.iter('pre')] != [
            ''.join(node.itertext()) for node in reference.iter('pre')]:
        raise AssertionError('Normal delivery changed protected code whitespace')
    if prose(target) != prose(reference):
        save(output / 'delivery-prose-mismatch.json', {'expected': prose(reference), 'actual': prose(target)})
        raise AssertionError('Normal delivery changed the accepted artist prose')
    def math_nodes(root):
        return Counter(tostring(node, encoding='unicode') for node in root.iter('math'))
    # Formula identity/projection was already strict. Verify those typed nodes
    # also survived actual adapter callbacks and submission, including values.
    if math_nodes(reference) - math_nodes(target):
        raise AssertionError('Normal delivery lost or changed projected formula nodes')
    publish_group(target, output)
    return target


@policy_operation
async def case(base, key, output, llm, *, recorded_fill=None, retry_policy=None, error_policy=False, request_headers=None):
    output.mkdir()
    result = {'id': key, 'passed': False, 'max_attempts': 13,
              'input_sha256': hashlib.sha256((base / 'text-cases' / f'{key}.json').read_bytes()).hexdigest()}
    runtime = None
    try:
        data, source, segments, hill = load_case(base, key)
        math, display_nodes, provenance = source_math(base, key, data, source, segments)
        protocol = StructuredTranslation(formulas=XMLInterrupter() if math is not None else None)
        translator = XMLTranslator(llm, llm, 'Chinese' if math else 'Portuguese', None, 10, 2600,
                                   executor=ConcurrentExecutor(FixedCapacity(1)))
        replay = data['rounds'][-1]['candidate'] if key in WHITESPACE else recorded_fill
        runtime = RecordingRuntime(translator._fill_runtime, output, replay)
        translator._fill_runtime = runtime
        projected = await translator._request_and_submit_async(
            hill_climbing=hill, source_text='\n\n'.join(''.join(n.itertext()) for n in source),
            translated_text=data['canonical'], callbacks=warp_callbacks(None, None, None),
            canonical_text_validator=lambda candidate, text: protocol.validate(segments, text, candidate),
            allow_direct_fill=False, source_math=math,
            canonical_math_projector=(lambda mappings, canonical, response:
                protocol.project_math(mappings, canonical, math, response, segments)) if math is not None else None)
        mappings = projected if projected is not None else list(hill.gen_mappings())
        original = deepcopy(source)
        # Display replacements are keyed by live source objects; transfer the
        # verified mapping to the private replay copy before submit mutates it.
        replay_displays = {id(clone): display_nodes[id(node)]
                           for node, clone in zip(source, original) if id(node) in display_nodes}
        result.update(adopt(source, mappings, output))
        await verify_delivery(data, original, math, replay_displays, runtime.last_response, llm, output, source)
        result['delivery_verified'] = True
        result.update(passed=True, source_math_provenance=provenance)
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
    result.update(fill_requests=runtime.calls if runtime else 0, translation_requests=0,
                  fill_mode='saved-original-answer' if key in WHITESPACE else ('live' if recorded_fill is None else 'accepted-answer-replay'))
    save(output / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


async def run(fixtures, output, profile):
    output.mkdir(parents=True, exist_ok=False)
    load_project_env(ROOT)
    llm = create_llm_from_env(profile, cache_path=output / 'unused-cache', log_dir_path=output / 'request-status')
    llm.cache_path = None
    llm.temperature, llm.top_p = 0.7, 1.0
    report = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'model': llm.model, 'profile': profile, 'source_sha256': fingerprint(),
              'passed': False, 'results': []}
    for key in WHITESPACE + CONTEXT:
        report['results'].append(await case(fixtures, key, output / key, llm,
            retry_policy=RetryPolicy(fill=12), error_policy=False, **model_request_options()))
        save(output / 'report.json', report)
    report['source_unchanged'] = fingerprint() == report['source_sha256']
    report['passed'] = (report['source_unchanged'] and len(report['results']) == 10
                        and all(item['passed'] for item in report['results']))
    save(output / 'report.json', report)
    return report['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', default='fill')
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.fixtures.resolve(), args.output.resolve(), args.profile)) else 1)


if __name__ == '__main__':
    main()
