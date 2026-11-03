"""Source-bound applicability and consequences of accepted note definitions."""
from unicodedata import normalize

from .content import _parse_block_content
from .reference import extract_head_mark


def reference_check_applies(state, check):
    """An absent mapping needs a possible explicit target before semantic review.

    Existing mappings and edits always require review. Prune only when simple
    decimal marks provably cannot occur in the source. Unsupported notation is
    left to the model; this is not a classifier for genuine printed references.
    """
    if check.get('references') or check.get('literal_edits'):
        return True
    definitions = check.get('target_definitions', [])
    if not definitions:
        return False
    marks = [normalize('NFKC', str(item.get('mark') or '')) for item in definitions]
    if any(not mark or not mark.isascii() or not mark.isdecimal() for mark in marks):
        return True
    source = next((layout for layout in state['target_page']['layouts']
                   if layout['source_order'] == check['source_order']), None)
    if source is None:
        return True
    raw = source['raw_text']
    if any(char in raw for char in ('\\', '<', '&')):
        return True
    raw = normalize('NFKC', raw)
    if any(char.isnumeric() and not char.isascii() for char in raw):
        return True
    return any(mark in raw for mark in marks)


def apply_new_note_boundaries(state, values):
    """A positively accepted new marked definition supports its separate opening.

    Do not trust the proposed definition alone: require its exact printed mark,
    first-member identity and both definition/ownership review evidence. Errors
    in those independent checks remain in the page verdict.
    """
    page = state['target_page']
    definitions = {item['citation_id']: (index, item)
                   for index, item in enumerate(page['citations']) if item['index'] is not None}
    marks = tuple(str(item['mark']) for _, item in definitions.values() if item['mark'])
    result = dict(values)
    seen = set()
    for layout in page['layouts']:
        cid = layout.get('citation_id')
        if layout['ownership'] != 'citation' or cid in seen:
            continue
        seen.add(cid)
        if cid not in definitions or layout['continues_from_previous']:
            continue
        index, definition = definitions[cid]
        order = layout['source_order']
        name = f'note_gap_{order}'
        if (order is None or name not in result or not definition['mark']
                or layout.get('citation_index') != definition['index']):
            continue
        mark, _ = extract_head_mark(_parse_block_content(layout['raw_text']), numeric_marks=marks)
        if mark is None or normalize('NFKC', str(mark)) != normalize('NFKC', str(definition['mark'])):
            continue
        proofs = [result[key] for key in (f'footnote_role_{order}', f'main_reading_{order}', f'furniture_{order}')
                  if key in result]
        definition_key = f'definition_{index}'
        if not proofs or definition_key not in result:
            continue
        result[name] = max(result[name], min(result[definition_key], *proofs))
    return result
