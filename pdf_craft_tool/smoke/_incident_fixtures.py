"""Load private incident evidence without adding book contents to the repository."""
# pylint: disable=protected-access
from copy import deepcopy
from io import BytesIO
import json
import hashlib
import re
from zipfile import ZipFile

from tiktoken import get_encoding

from pdf_craft.common.whitespace import normalize_whitespace
from pdf_craft.pipeline.epub.translation.xml_interrupter import XMLInterrupter
from pdf_craft.transformer.formula_source import SourceMath
from dataclasses import dataclass, field


@dataclass
class SourceEvidence:
    """Historical fixture evidence only; never a product protocol."""
    sources: list
    blocks: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)

from pdf_craft.transformer.xml_translator.segment import BlockSegment, search_inline_segments, search_text_segments
from pdf_craft.transformer.xml_translator.xml import XMLLikeNode, is_inline_element
from pdf_craft.transformer.xml_translator.xml.friendly import decode_friendly
from pdf_craft.transformer.xml_translator.xml_translator.hill_climbing import HillClimbing

WHITESPACE = ('0ae0fde2e18b4d26a26817fb17eac84d', '3f347c02320a4960b89fd1da8993c64f',
              '3967fdff93ba4e509c052ba1e0360e07', 'd4f61678140745b58ae884fb862aeba0',
              'f8bd2e3750a94f41bc9cee7f209a506b')
CONTEXT = ('2fc996758179438caf28f4dc71432321', '7ab7a9fbb1f74dd9a712b758b6226ce6',
           '148ef6845788402eb80b3470a1af53e8', 'f9b813761c8e420f9ced744d26ceff92',
           'aaad3285686b4693acbdaf586f9868c8')


EXTRA = ('47f9aeedb59c4337b01c215663ff73b1', '58e4c2f28a3747faaacdcda39dd39e2e',
         'c4abdd4df2c840cf9c76050837e13670', '0ba0da62b6084074af18549dbfb2ad51')


def read_case(base, key):
    path = base / 'text-cases' / f'{key}.json'
    if path.exists():
        return json.loads(path.read_text())
    # Reconstruct the additional multi-formula incident from its retained
    # first request. This is archived-data decoding, not artist preprocessing.
    path = base / 'unpacked/analysis/0/llm-failures' / key / 'attempts.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    message = rows[0]['messages'][1]['content']
    template = re.search(r'XML template:\n```XML\n(.*?)\n```', message, re.S)
    assert template is not None
    translation = next(decode_friendly(message, tags='translation'))
    table = dict(re.findall(r'(?ms)^m(\d+): (.*?)(?=^m\d+: |\n\n<math-ref)', message))
    values = {'m'+key: value.rstrip('\n') for key, value in table.items()}
    canonical = translation.text or ''
    for child in translation:
        canonical += values[child.get('key')] + (child.tail or '')
    return dict(id=key, job='b8d7ed0b-65c2-495e-8a2d-6857ab739bb4', canonical=canonical,
                source_template=template[1], artist_math_values=values,
                rounds=[dict(candidate=row['response']) for row in rows])


def load_case(base, key):
    data = read_case(base, key)
    source = next(decode_friendly(data['source_template'], tags='xml'))
    segments = []
    for node in source:
        parts = list(search_inline_segments(search_text_segments(node, normalize_whitespace=False)))
        if len(parts) != 1:
            raise ValueError(f'Incident block {node.tag} is not a single saved segment')
        segments.extend(parts)
    block = BlockSegment('xml', segments)
    # Request-local IDs must be reproduced by the ordinary constructor, not
    # patched into a validator or inferred from the translated answer.
    expected = [(node.tag, node.get('id')) for node in source.iter() if node.get('id')]
    actual = [(node.tag, node.get('id')) for node in block.create_element().iter() if node.get('id')]
    if expected != actual:
        raise ValueError('Saved source identities do not match reconstructed segment identities')
    hill = HillClimbing(get_encoding('o200k_base'), 10, block)
    if hill.validate(source) is not None:
        raise ValueError('Saved source does not validate against its reconstructed template')
    return data, source, segments, hill


def source_math(base, key, data, source, segments):
    """Recover real MathML via unique prose anchors and a complete value sequence.

    This is fixture reconstruction, not runtime formula identity inference.
    The model still supplies every translated source-math assignment.
    """
    paths = list((base / 'unpacked/analysis/0/llm-failures').glob(f'{key}/attempts.jsonl'))
    paths += list((base / 'other-jobs').glob(f'*/analysis/0/llm-failures/{key}/attempts.jsonl'))
    if len(paths) != 1:
        raise ValueError('Expected exactly one original repair conversation')
    first = json.loads(paths[0].read_text().splitlines()[0])
    encoded = re.search(r'<source-formulas\b(?:[^>]*?/>|.*?</source-formulas>)', first['messages'][1]['content'], re.S)
    if encoded is None:
        raise ValueError('Original source formula table is absent')
    table = next(decode_friendly(encoded[0], tags='source-formulas'))
    if not len(table):
        return None, {}, {}
    wanted = [node.findtext('value') for node in table]
    # The four mathematical fixtures all belong to the same original input.
    # Verify this relationship instead of substituting synthetic math nodes.
    if not data['job'].startswith('b8d7ed0b'):
        raise ValueError('No original MathML input registered for this job')
    source_bytes = (base / 'unpacked/inputs/0.epub').read_bytes()
    source_digest = hashlib.sha256(source_bytes).hexdigest()
    with ZipFile(BytesIO(source_bytes)) as archive:
        root = XMLLikeNode(BytesIO(archive.read('EPUB/text/content.xhtml'))).element
    article = next(root.iter('article'))
    children = list(article)
    parents = {id(child): parent for parent in root.iter() for child in parent}
    anchors = [normalize_whitespace(n.text or '', trim=True)[:85] for n in source
               if n.tag == 'p' and len(normalize_whitespace(n.text or '', trim=True)) >= 65]
    positions = []
    for anchor in anchors:
        found = [node for node in root.iter('p') if normalize_whitespace(node.text or '', trim=True).startswith(anchor)]
        if len(found) != 1:
            continue
        node = found[0]
        while id(node) in parents and parents[id(node)] is not article:
            node = parents[id(node)]
        if node in children:
            positions.append(children.index(node))
    if not positions:
        raise ValueError('No unique original source prose anchor')
    lo, hi = max(0, min(positions)-30), min(len(children), max(positions)+31)
    interrupter = XMLInterrupter()
    available, displays = [], []
    for child in children[lo:hi]:
        for node in child.iter('math'):
            value = interrupter._render_latex(list(search_text_segments(node, normalize_whitespace=False))).strip()
            (available if is_inline_element(node) else displays).append((value, node))
    matches = [i for i in range(len(available)-len(wanted)+1)
               if [value for value, _ in available[i:i+len(wanted)]] == wanted]
    if len(matches) != 1:
        raise ValueError('Original source formula sequence is not unique')
    found = available[matches[0]:matches[0]+len(wanted)]
    math = SourceEvidence([SourceMath(value, deepcopy(node)) for value, node in found])
    by_id = {segment.id: segment for segment in segments}
    for row in table:
        identity = row.get('key')
        block_id = row.findtext('source-block')
        if block_id is None:
            raise ValueError('Source formula has no saved block identity')
        math.blocks[identity] = by_id[int(block_id)]
        math.evidence[identity] = {name: row.findtext(name, '') for name in ('before', 'text', 'after')}
    # Preserve display-slot structure in the adopted local XHTML. Match the
    # complete sequence as well; repeated equations must never choose a node.
    slots = [node for node in source if node.tag == 'expression']
    values = [''.join(node.itertext()).strip() for node in slots]
    display_matches = [i for i in range(len(displays)-len(values)+1)
                       if [value for value, _ in displays[i:i+len(values)]] == values]
    if slots and len(display_matches) != 1:
        raise ValueError('Original display equation sequence is not unique')
    display_nodes = {} if not slots else {
        id(slot): deepcopy(pair[1]) for slot, pair in zip(slots, displays[display_matches[0]:])}
    return math, display_nodes, {'source_window': [lo, hi], 'original_mathml_count': len(found),
                                 'unique_source_sequence': True, 'unique_display_sequence': True,
                                 'source_epub_sha256': source_digest,
                                 'source_formula_table_sha256': hashlib.sha256(encoded[0].encode()).hexdigest()}


def materialize_source(source, math, display_nodes):
    """Rebuild the whole selected source group with its real MathML nodes."""
    parents = {id(child): parent for parent in source.iter() for child in parent}
    replacements = dict(display_nodes)
    if math is not None:
        inline = [node for node in source.iter('expression') if node not in list(source)]
        if len(inline) != len(math.sources):
            raise ValueError('Original inline formula count differs from saved source slots')
        for node, original in zip(inline, math.sources, strict=True):
            if ''.join(node.itertext()).strip() != original.latex:
                raise ValueError('Original inline formula sequence differs from saved source slots')
            replacements[id(node)] = deepcopy(original.element)
    for node in list(source.iter('expression')):
        if id(node) not in replacements:
            raise ValueError('Missing original formula for saved source slot')
        parent, replacement = parents[id(node)], replacements[id(node)]
        replacement.tail = node.tail
        parent[list(parent).index(node)] = replacement
    source.tag = 'body'
    return source
