"""Body-only edge proofs with concrete source identity and measured geometry."""
from copy import deepcopy
import re

from .local_review import LOCAL_REVIEW_THRESHOLD, review_batches
from .review_evidence import previous_paragraph, supplementary_requests


def body_supplementary_requests(state, risks):
    """Keep generic facts intact; specialize only the body review's questions."""
    for packet in supplementary_requests(state, risks):
        packet = deepcopy(packet)
        for key, spec in packet['questions'].items():
            if key.startswith('left_frame_complete_'):
                spec['instructions'].append(
                    'Judge whether the LEFT reporting/quoted frame is complete, not whether the current OCR opening is grammatical. Missing characters at the target opening do not create an unclosed left clause. Preserve any actual outer conditional or obligatory complement in the left text; never invent one to repair an OCR fragment.')
        yield packet
    yield from review_batches(state, list(_example_setups(state, risks)))
    for packet in review_batches(state, list(_illustration_joins(state))):
        packet['state']['local_raw_evidence'] = _role_evidence(packet['state']['local_raw_evidence'])
        packet['state'].pop('preceding_source_index', None)
        for check in packet['state']['checks'].values():
            check.pop('immediate_paragraph_hint', None)
        yield packet


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
        spec = dict(type='noul', instructions=[
            f'Assess only checks.{key}. Does the actual selected predecessor introduce ONE mathematical example whose immediate specification is the target opening, so keeping them in one paragraph is permissible?',
            'A short example introduction and its immediately following Let/Suppose specification may remain in one paragraph even if the introduction ends with a period. Do not require a forced split merely because both pieces have punctuation. Require a direct introduction-to-specification relation, not just related mathematics. A new numbered example, independent exercise, heading or change of task cannot use this proof. Use the actual nearest predecessor, not a more distant convenient one. No unprovided indentation or image text may be invented.'],
            criteria=dict(true='This existing join keeps one directly introduced example and its specification together.',
                          false='That direct same-example relationship is not established.'))
        yield key, spec, value


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
    for kind, ask, yes, no in [
        ('prior_illustration_unit',
         'Is the selected predecessor printed as a separate text band associated with the large nearby illustration, such as a caption or credited literary excerpt, instead of being a normal running-text paragraph?',
         'The selected source has a concrete independent illustration-associated role.',
         'That independent illustration role is not established.'),
        ('target_leaves_illustration',
         'Is the CURRENT target source ordinary main reading on its page, rather than part of an illustration caption or detached image/excerpt strip? Judge only its printed role, not whether it starts a complete word or sentence. A main prose fragment can continue earlier body text that is outside this three-page window; you need not find or invent that earlier body text to establish the target role. Related or repeated story wording does not make two sources the same printed unit.',
         'The target source belongs to ordinary main reading, not to the predecessor illustration caption/excerpt stream.',
         'The main-reading role outside the illustration caption/excerpt stream is not established.')]:
        key = f'{kind}_{order}'
        prompt = dict(type='noul', instructions=[
            f'Audit ONLY state.checks.{key}. The selected_predecessor identifies the concrete source that this proposed incoming JOIN attaches to. Judge that source and current target, not some older passage chosen because its wording fits.',
            ask,
            'measured_adjacency reports literal box measurements, not a role verdict. A literary passage can be an image caption even when its wording is ordinary narrative copied from the book. If a narrow text strip directly below the large image ends with a photo credit, evaluate the function of the WHOLE strip; do not label its prose ordinary narrative merely because the same story occurs elsewhere. Conversely a nearby small illustration or a personal name alone cannot establish a separate caption unit.',
            'Use complete local_raw_evidence, the selected source text and the supplied image positions. Image presence alone, a credit name alone, repeated wording alone, or related subject matter is not proof. A passage quoted from ordinary prose can be a separate printed unit with an illustration. Conversely running prose can flow past an image, and captions can continue across pages. Do not infer missing print, unprovided indentation or a fourth page. Ambiguous evidence means FALSE.'],
            criteria=dict(true=yes, false=no))
        yield key, prompt, deepcopy(value)


def apply_body_facts(risks, facts):
    """A local acceptance proof cannot erase a different source or stream fault."""
    result = dict(risks)
    for name, value in facts.items():
        if name.startswith('same_example_setup_') and value >= LOCAL_REVIEW_THRESHOLD:
            target = 'gap_' + name.rsplit('_', 1)[1]
            if result.get(target, 0) >= LOCAL_REVIEW_THRESHOLD:
                result[target] = min(result[target], 1-value)
        elif name.startswith('prior_illustration_unit_'):
            order = name.rsplit('_', 1)[1]
            result['joined_illustration_switch_' + order] = min(
                value, facts['target_leaves_illustration_' + order])
    return result
