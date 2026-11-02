"""Body-only edge proofs with concrete source identity and measured geometry."""
from copy import deepcopy
import re

from .local_review import LOCAL_REVIEW_THRESHOLD, review_batches
from .review_evidence import previous_paragraph, supplementary_requests, contradictory_hint


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
        spec = dict(type='noul', instructions=[
            f'Judge only checks.{label}. Is the LEFT selected_predecessor already a complete grammatical frame, so the target does not supply any obligatory missing clause or complement to it?',
            'Locate the exact left source in local_raw_evidence by page_index and source_order. Evaluate what that LEFT text requires, not whether the target looks like a complete sentence. A standalone description, screenplay shot or displayed variable-definition row may start its own paragraph after a complete left frame. Shared topic or the conjunction and does not itself create an unfinished left construction.',
            'Do not certify a left conditional still requiring its main clause, an incomplete verb/preposition or missing obligatory complement. A semicolon, period or formula alone does not close an actual unfinished surrounding frame. Do not invent missing OCR text or an earlier convenient predecessor. If the left frame is not demonstrably closed, answer FALSE.'],criteria=dict(true='The exact left frame is closed; the target does not complete an obligatory construction.',false='An unfinished left construction remains or closure cannot be established.'))
        selected = {k: prior[k] for k in ('page_index', 'source_order', 'text_end', 'bbox_normalized')}
        yield label, spec, dict(check, selected_predecessor=selected)
        role = 'closed_predecessor_main_reading_' + str(check['source_order'])
        prompt = dict(type='noul', instructions=[
            f'Judge only checks.{role}. Is the exact selected_predecessor genuinely in the main reading stream, rather than a detached footnote, caption or publisher apparatus that happens to have been misclassified?',
            'Read its complete raw source and all local sources on that page, including neighboring note markers, smaller-print regions and continuations. A footnote can have several paragraphs, quotations and a long unmarked final block. Do not accept the last supplied paragraph as main reading merely because it is grammatical. Conversely autonomous commentary sections and ordinary narrative are main reading. Require positive role evidence; ambiguity means FALSE.',
            'This question only verifies whether this particular source can certify a new main-paragraph break. It does not select or invent a different, earlier predecessor.'], criteria=dict(
                true='The exact source is genuine main reading.',
                false='It belongs to an ancillary stream, or its main-reading role is not established.'))
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
        spec = dict(type='noul', instructions=[f'Judge only checks.{key}. Is this retained punctuation-only source a non-reading scan/OCR trace on a page without textual reading content, rather than an intentional reading unit?',
            'Use all raw target layouts, literal source text and its normalized box. A tiny punctuation trace represented by an almost whole-page OCR text rectangle, on an otherwise text-empty page, can be noise; it must not be joined to main prose on a neighboring page. The large rectangle is not evidence of a large printed character.',
            'Punctuation alone does not prove noise. Preserve actual dialogue punctuation, intentional poem/section separators, standalone mathematical notation and signs that have a concrete reading role. Do not invent image pixels or absent text. Require positive evidence of a non-reading artifact; ambiguity means FALSE.'],criteria=dict(true='The source is a non-reading page trace kept as prose.',false='That artifact role is not established.'))
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
        spec = dict(type='noul', instructions=[
            f'Judge only checks.{key}. Is this exact source a printed page number, running header/footer or repeated publisher/navigation label, incorrectly kept in the main reading stream?',
            'Use its literal text, page-edge geometry and the surrounding original sources. A combined page number plus chapter/book label at the outer margin can be one apparatus block, including on an otherwise blank page. An empty page does not turn its footer into body text.',
            'Require positive evidence of a publisher/navigation role. Page-edge position, short length, digits or a personal name alone are insufficient. Preserve real chapter openings, section headings, author attributions, dialogue, numbered exercises, endnotes/footnotes and short narrative paragraphs. A heading may start low on a page and remain real content. Do not infer missing text or a fourth page. Ambiguous evidence means FALSE.'],criteria=dict(true='The exact source is page apparatus, not main reading content.',false='A publisher/navigation role is not established.'))
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
        spec = dict(type='noul', instructions=[
            f'Assess only checks.{key}. Does the actual selected predecessor introduce ONE mathematical example whose immediate specification is the target opening, so keeping them in one paragraph is permissible?',
            'A short example introduction and its immediately following Let/Suppose specification may remain in one paragraph even if the introduction ends with a period. Do not require a forced split merely because both pieces have punctuation. Require a direct introduction-to-specification relation, not just related mathematics. A new numbered example, independent exercise, heading or change of task cannot use this proof. Use the actual nearest predecessor, not a more distant convenient one. No unprovided indentation or image text may be invented.'],
            criteria=dict(true='This existing join keeps one directly introduced example and its specification together.',
                          false='That direct same-example relationship is not established.'))
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
        for kind, ask in [
            ('one_numbered_instruction', 'Do the selected predecessor condition and the target command belong to ONE explicitly numbered exercise instruction, with no new numbered item, heading or independent task starting at the target?'),
            ('conditional_command_required', 'Does the target command supply the actual required main instruction governed by the unfinished If-condition ending in a comma in selected_predecessor, so the proposed break cuts one grammatical exercise instruction?')]:
            key = f"{kind}_{check['source_order']}"
            prompt = dict(type='noul', instructions=[
                f'Judge only checks.{key}. Use the exact selected_predecessor and current target in local_raw_evidence.', ask,
                'Confirm the literal numbered condition and its immediate command, not merely a shared mathematical topic. A completed example followed by a new problem, independently displayed alternative transformation rows, or an intervening standalone formula cannot establish this fault. Do not invent omitted words or impose paragraph-style preferences. If the target is a distinct item or that grammatical dependency is not established, answer FALSE.'],
                criteria=dict(true='This specific literal relationship is established.',
                              false='That specific relationship is not established.'))
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
