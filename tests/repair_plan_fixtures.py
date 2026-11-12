"""Build wire plans from explicit assembly fixtures, never a product fallback.

Existing invariant tests manipulate fully resolved compiler records. Serialize
those test records into today's protocol before exercising real model loops.
"""
import json

from pdf_craft.extractor.chapter.repair_plan import RepairPlan, materialize_plan, suggested_destination


def expanded_packet(packet):
    """Present a model input suggestion as a compiler fixture for older tests."""
    plan = RepairPlan.model_validate(dict(layouts=[dict(layout_id=x['layout_id'], **x['initial_proposal'])
                              for x in packet['layouts']], notes=packet['notes'], references=packet['references']))
    resolved = materialize_plan(plan, packet['page_index']).model_dump()
    return dict(packet, citations=resolved['citations'], references=resolved['references'],
                layouts=[dict(item, initial_proposal=record) for item, record in
                         zip(packet['layouts'], resolved['layouts'])])


def serialize_plan(data, **kwargs):
    if not isinstance(data, dict) or 'layouts' not in data or 'notes' in data:
        return json.dumps(data, **kwargs)
    notes = sorted(data.get('citations', []), key=lambda x: x['stream_order'])
    by_index = {x['index']: x['citation_id'] for x in notes if x['index'] is not None}
    parts = []
    for item in data['layouts']:
        destination = suggested_destination(item)
        if item.get('preserve_as_image') and destination['kind'] in ('body', 'note'):
            destination['as_image'] = True
        parts.append(dict(layout_id=item['layout_id'], source_range=item.get('source_range'),
                          destination=destination))
    return json.dumps(dict(reading=data.get('stream_assessment', ''), layouts=parts,
        notes=[dict(operation='continue', note_id=x['citation_id']) if x.get('continues_previous') else
               dict(operation='define', note_id=x['citation_id'], mark=x.get('mark')) for x in notes],
        references=[dict(layout_id=x['layout_id'], note_id=by_index.get(x['citation_index'], 'unknown'),
                         anchor=x['anchor'], extract_from_expression=x.get('extract_from_expression', False))
                    for x in data.get('references', [])]), **kwargs)
