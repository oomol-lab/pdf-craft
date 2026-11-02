"""Body-only edge proofs with concrete source identity and measured geometry."""
from copy import deepcopy
import re

from .local_review import LOCAL_REVIEW_THRESHOLD, review_batches
from .review_evidence import previous_paragraph, supplementary_requests, contradictory_hint

from .review_templates import review_question

def body_supplementary_requests(state, risks):
    """Keep generic facts intact; specialize only the body review's questions."""
    yield from supplementary_requests(state, risks)
    yield from review_batches(state, list(_example_setups(state, risks)))
    yield from review_batches(state, list(_numbered_instructions(state)))
    yield from _role_packets(state, _closed_predecessor_frames(state, risks))
    yield from _role_packets(state, _publisher_apparatus(state))
    yield from _role_packets(state, _nonreading_traces(state))
    yield from _role_packets(state, _illustration_joins(state))


def _role_packets(state, facts):
    for packet in review_batches(state, list(facts)):
        packet['state']['local_raw_evidence'] = _role_evidence(packet['state']['local_raw_evidence'])
        packet['state'].pop('preceding_source_index', None)
        for check in packet['state']['checks'].values():
            check.pop('immediate_paragraph_hint', None)
        yield packet


def _closed_predecessor_frames(state, risks):
    """An odd target cannot retroactively open a complete left construction."""
    seen = set()
    for name, check in state['checks'].items():
        key = 'gap_' + str(check['source_order']) + '_syntax'
        if (not name.startswith('gap_') or check['present_decision'] or key in seen
                or risks.get(key, 0) < LOCAL_REVIEW_THRESHOLD):
            continue
        seen.add(key)
        prior = previous_paragraph(state, check)
        if (prior is None or prior['page_index'] != state['local_raw_evidence']['target_page']['page_index']
                or contradictory_hint(state, check)):
            continue
        label = 'closed_predecessor_frame_' + str(check['source_order'])
        spec = review_question('body_followup', kind='closed_predecessor_frame', label=label)
        selected = {k: prior[k] for k in ('page_index', 'source_order', 'text_end', 'bbox_normalized')}
        yield label, spec, dict(check, selected_predecessor=selected)
        role = 'closed_predecessor_main_reading_' + str(check['source_order'])
        prompt = review_question('body_followup', kind='closed_predecessor_main_reading', role=role)
        yield role, prompt, dict(check, selected_predecessor=selected)


def _nonreading_traces(state):
    """Punctuation-only pages still require positive evidence of scan noise."""
    page = state['local_raw_evidence']['target_page']
    texts = [x for x in page['layouts'] if x['source_type'] == 'text']
    if any(any(c.isalnum() for c in x['raw_text']) for x in texts):
        return
    for name, check in state['checks'].items():
        if not name.startswith('owner_') or check['ownership'] != 'paragraph':
            continue
        source = next(x for x in texts if x['source_order'] == check['source_order'])
        key = 'nonreading_page_trace_' + str(check['source_order'])
        spec = review_question('body_followup', kind='nonreading_page_trace', key=key)
        source = {k: source[k] for k in ('source_order', 'source_type', 'raw_text', 'bbox_normalized')}
        yield key, spec, dict(source=source)


def _publisher_apparatus(state):
    """Geometry admits a role question; only positive role evidence adds risk."""
    page = state['local_raw_evidence']['target_page']
    for name, check in state['checks'].items():
        if not name.startswith('owner_') or check['ownership'] != 'paragraph':
            continue
        source = next(x for x in page['layouts'] if x['source_type'] == 'text'
                      and x['source_order'] == check['source_order'])
        box = source['bbox_normalized']
        if len(source['raw_text']) > 160 or not (box[3] <= .10 or box[1] >= .90):
            continue
        key = 'publisher_apparatus_' + str(check['source_order'])
        spec = review_question('body_followup', kind='publisher_apparatus', key=key)
        source = {k: source[k] for k in ('source_order', 'source_type', 'raw_text', 'bbox_normalized')}
        yield key, spec, dict(source=source)


def _role_evidence(evidence):
    """Role facts must not see algorithmic ownership as an answer hint."""
    fields = ('document_order', 'source_order', 'source_type', 'raw_text', 'bbox_normalized',
              'text_scale_estimate', 'number_ink_bbox_normalized')
    result = {k: v for k, v in evidence.items() if k not in ('target_page', 'previous_page', 'next_page')}
    for side in ('target_page', 'previous_page', 'next_page'):
        page = evidence.get(side)
        if page is None:
            result[side] = None
            continue
        layouts = page.get('layouts', page.get('paragraph_boundary', []) + page.get('citation_boundary', [])
                           + page.get('excluded_candidates', []) + page.get('assets', []))
        result[side] = dict(page_index=page['page_index'], layouts=[
            {k: x[k] for k in fields if k in x} for x in sorted(layouts, key=lambda x: x['document_order'])])
    return result


def _example_setups(state, risks):
    for name, check in state['checks'].items():
        if (not name.startswith('gap_') or not check['present_decision']
                or risks.get(name, 0) < LOCAL_REVIEW_THRESHOLD):
            continue
        prior = previous_paragraph(state, check)
        if (prior is None or 'example' not in prior['text_end'].lower()
                or not re.match(r'^(?:Let|Suppose|Take|Consider)\b', check['current_start'])):
            continue
        value = dict(check, selected_predecessor=prior)
        key = f"same_example_setup_{check['source_order']}"
        spec = review_question('body_followup', kind='same_example_setup', key=key)
        yield key, spec, value


def _numbered_instructions(state):
    evidence = state['local_raw_evidence']
    for name, check in state['checks'].items():
        if not name.startswith('gap_') or check['present_decision']:
            continue
        if not re.match(r'^(?:find|calculate|determine|show|prove|evaluate|solve)\b', check['current_start'].strip(), re.I):
            continue
        prior = previous_paragraph(state, check)
        if prior is None:
            continue
        page = next((evidence[k] for k in ('target_page', 'previous_page')
                     if evidence.get(k) and evidence[k]['page_index'] == prior['page_index']), None)
        if page is None:
            continue
        layouts = page.get('layouts', page.get('paragraph_boundary', []) + page.get('citation_boundary', []))
        source = next((x for x in layouts if x['source_type'] == 'text'
                       and x['source_order'] == prior['source_order']), None)
        if source is None:
            continue
        raw = source['raw_text'].strip()
        if not re.match(r'^(?:\(\d+\)|\d+[.)])\s*If\b', raw, re.I) or not raw.endswith(','):
            continue
        value = dict(check, selected_predecessor=dict(page_index=prior['page_index'],
                     source_order=prior['source_order'], raw_text=source['raw_text']))
        for kind in ('one_numbered_instruction', 'conditional_command_required'):
            key = f"{kind}_{check['source_order']}"
            prompt = review_question('body_followup', kind=kind, key=key)
            yield key, prompt, deepcopy(value)


def _illustration_joins(state):
    evidence = state['local_raw_evidence']
    page = evidence['target_page']
    prior_page = evidence.get('previous_page')
    if not prior_page or not any(x['source_type'] == 'image' for x in prior_page.get('assets', [])):
        return
    for name, check in state['checks'].items():
        if not name.startswith('gap_') or not check['present_decision']:
            continue
        prior = previous_paragraph(state, check)
        if prior is None or prior['page_index'] != prior_page['page_index']:
            continue
        source = next((x for x in prior_page['paragraph_boundary']
                       if x['source_order'] == prior['source_order']), None)
        if source is None:
            continue
        geometry = []
        box = source['bbox_normalized']
        for image in prior_page.get('assets', []):
            if image['source_type'] != 'image':
                continue
            a = image['bbox_normalized']
            geometry.append(dict(image_bbox=a, image_page_area=(a[2]-a[0])*(a[3]-a[1]),
                source_is_below_image=box[1] >= a[3], vertical_gap=box[1]-a[3],
                source_band_height=box[3]-box[1]))
        target = next(x for x in page['layouts'] if x['source_type'] == 'text'
                      and x['source_order'] == check['source_order'])
        target_view = dict(source_order=target['source_order'], raw_text=target['raw_text'],
                           bbox_normalized=target['bbox_normalized'])
        organization = dict(text_boxes=[x['bbox_normalized'] for x in page['layouts'] if x['source_type'] == 'text'],
                            image_boxes=[x['bbox_normalized'] for x in page['layouts'] if x['source_type'] == 'image'])
        value = dict(check, target_source=target_view, target_page_organization=organization,
            measured_adjacency=geometry, selected_predecessor=dict(page_index=prior['page_index'],
                source_order=prior['source_order'], raw_text=source['raw_text'],
                bbox_normalized=source['bbox_normalized']))
        yield from _illustration_questions(check['source_order'], value)


def _illustration_questions(order, value):
    for kind in ('prior_illustration_unit', 'target_leaves_illustration'):
        key = f'{kind}_{order}'
        prompt = review_question('body_followup', kind=kind, key=key)
        yield key, prompt, deepcopy(value)


def apply_body_facts(risks, facts):
    """A local acceptance proof cannot erase a different source or stream fault."""
    result = dict(risks)
    for name, value in facts.items():
        if name.startswith('same_example_setup_') and value >= LOCAL_REVIEW_THRESHOLD:
            target = 'gap_' + name.rsplit('_', 1)[1]
            if result.get(target, 0) >= LOCAL_REVIEW_THRESHOLD:
                result[target] = min(result[target], 1-value)
        elif name.startswith('one_numbered_instruction_'):
            order = name.rsplit('_', 1)[1]
            result['split_numbered_instruction_' + order] = min(
                value, facts['conditional_command_required_' + order])
        elif name.startswith('closed_predecessor_frame_') and value >= LOCAL_REVIEW_THRESHOLD:
            target = 'gap_' + name.rsplit('_', 1)[1] + '_syntax'
            if result.get(target, 0) >= LOCAL_REVIEW_THRESHOLD:
                proof = min(value, facts['closed_predecessor_main_reading_' + name.rsplit('_', 1)[1]])
                if proof >= LOCAL_REVIEW_THRESHOLD:
                    result[target] = min(result[target], 1-proof)
        elif name.startswith(('publisher_apparatus_', 'nonreading_page_trace_')):
            result[name] = value
        elif name.startswith('prior_illustration_unit_'):
            order = name.rsplit('_', 1)[1]
            result['joined_illustration_switch_' + order] = min(
                value, facts['target_leaves_illustration_' + order])
    return result
