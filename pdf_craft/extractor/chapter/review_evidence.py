"""Read-only candidate selection for body-only structural review."""
from collections.abc import Iterator
from copy import deepcopy
import re

from .local_review import LOCAL_REVIEW_THRESHOLD, review_batches
from .review_questions import question


def normalized(text):
    """Literal containment ignores ASCII layout whitespace, not punctuation."""
    return re.sub(r'[ \t\r\n\f]+', ' ', text).strip()


def previous_paragraph(state, check):
    index = state['preceding_source_index']
    return next((index[key] for key in reversed(check['preceding_source_candidates'])
                 if index[key]['ownership_hint'] == 'paragraph'), None)


def target_layout(state, check):
    return next(layout for layout in state['local_raw_evidence']['target_page']['layouts']
                if layout['source_type'] == 'text' and layout['source_order'] == check['source_order'])


def contradictory_hint(state, check):
    """A lower same-column hint cannot certify a closed left expression."""
    prior = previous_paragraph(state, check)
    if prior is None or prior['page_index'] != state['local_raw_evidence']['target_page']['page_index']:
        return False
    a, b = prior['bbox_normalized'], target_layout(state, check)['bbox_normalized']
    return min(a[2], b[2]) > max(a[0], b[0]) and a[1] > b[3]


def possible_mixed_section(state, check):
    """Withhold an exception when its box covers pre-section body interiors."""
    layouts = state['local_raw_evidence']['target_page']['layouts']
    target = target_layout(state, check)
    box = target['bbox_normalized']
    by_order = {layout['source_order']: layout for layout in layouts if layout['source_type'] == 'text'}
    for heading in check['preceding_or_current_heading_candidates']:
        start = by_order[heading['source_order']]['document_order']
        for other in layouts:
            if (other['source_type'] != 'text' or other['source_order'] == target['source_order']
                    or other['document_order'] >= start or other.get('paragraph_role') == 'heading'
                    or not other.get('raw_text', '').strip()):
                continue
            a = other['bbox_normalized']
            cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
            if (min(a[2], box[2]) > max(a[0], box[0]) and min(a[3], box[3]) > max(a[1], box[1])
                    and box[0] < cx < box[2] and box[1] < cy < box[3]):
                return True
    return False


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _job(kind, check):
    name = f"{kind}_{check['source_order']}"
    return name, question(kind, name), deepcopy(check)


def _duplicates(state, _risks):
    retained = [layout for layout in state['local_raw_evidence']['target_page']['layouts']
                if layout['source_type'] == 'text' and layout['ownership'] == 'paragraph'
                and layout.get('raw_text', '').strip()]
    for key, check in state['checks'].items():
        if not key.startswith('owner_') or check['ownership'] != 'paragraph':
            continue
        target = target_layout(state, check)
        others = [layout for layout in retained if layout['source_order'] != target['source_order']
                  and _overlap(layout['bbox_normalized'], target['bbox_normalized'])]
        matches = []
        text = normalized(target['raw_text'])
        for other in others:
            raw = normalized(other['raw_text'])
            offset = raw.find(text)
            if offset < 0 or (raw == text and other['document_order'] > target['document_order']):
                continue
            matches.append(dict(other_source_order=other['source_order'], full_literal_match_start=offset))
        if matches:
            yield _job('redundancy', dict(check,
                same_page_overlapping_source_orders=[layout['source_order'] for layout in others],
                complete_literal_containment_candidates=matches))


def _owners(state, risks):
    for key, check in state['checks'].items():
        if (key.startswith('owner_') and check['ownership'] == 'paragraph'
                and risks.get(key, 0) >= LOCAL_REVIEW_THRESHOLD):
            yield check


def _sections(state, risks):
    layouts = state['local_raw_evidence']['target_page']['layouts']
    headings = [layout for layout in layouts if layout['source_type'] == 'text'
                and (layout.get('paragraph_role') == 'heading'
                     or re.match(r'^#{1,6}\s+', layout.get('raw_text', '')))]
    for check in _owners(state, risks):
        target = target_layout(state, check)
        candidates = [dict(source_order=h['source_order'], raw_text=h['raw_text'],
                           bbox_normalized=h['bbox_normalized'])
                      for h in headings if h['document_order'] <= target['document_order']]
        if candidates:
            value = dict(check, preceding_or_current_heading_candidates=candidates)
            yield _job('section_sequence', value)
            yield _job('section_member', value)


def _attributions(state, risks):
    for check in _owners(state, risks):
        yield _job('attribution', check)


def _quotes(state, risks):
    pairs = {'“': '”', '‘': '’', '"': '"', "'": "'", '「': '」', '『': '』', '«': '»'}
    for key, check in state['checks'].items():
        if (not key.startswith('gap_') or check['present_decision']
                or max(risks.get(key + '_' + part, 0) for part in ('word', 'syntax')) < LOCAL_REVIEW_THRESHOLD):
            continue
        text = target_layout(state, check)['raw_text'].strip()
        whole = bool(text and text[0] in pairs and text.endswith(pairs[text[0]]))
        prior = previous_paragraph(state, check)
        same = prior is not None and prior['page_index'] == state['local_raw_evidence']['target_page']['page_index']
        ending = prior['text_end'].rstrip() if prior else ''
        closed = bool(same and len(ending) > 1 and ending[-1] in pairs.values()
                      and ending[:-1].rstrip().endswith(('。', '！', '？', '.', '!', '?')))
        if not whole and not closed:
            continue
        value = dict(check, raw_delimiter_cues=dict(current_has_matching_outer_quote_delimiters=whole,
            preceding_paragraph_hint_is_same_page=same, preceding_hint_ends_sentence_ending_quote=closed))
        if whole or not closed:
            yield _job('quotation_boundary', value)
        elif not contradictory_hint(state, check):
            for part, kind in (('word', 'left_word_complete'), ('syntax', 'left_frame_complete')):
                if risks.get(key + '_' + part, 0) >= LOCAL_REVIEW_THRESHOLD:
                    yield _job(kind, value)


def _image_candidates(state):
    page = state['local_raw_evidence']['target_page']
    images = [layout for layout in page['layouts'] if layout['source_type'] == 'image']
    if not images:
        return
    index = state['preceding_source_index']
    for key, check in state['checks'].items():
        if not key.startswith('gap_') or not check['present_decision']:
            continue
        if any(index[k]['page_index'] == page['page_index'] and index[k]['ownership_hint'] == 'paragraph'
               for k in check['preceding_source_candidates']):
            continue
        text = normalized(target_layout(state, check)['raw_text'])
        matches = []
        for side in ('previous_page', 'next_page'):
            adjacent = state['local_raw_evidence'].get(side)
            for layout in adjacent.get('paragraph_boundary', []) if adjacent else ():
                offset = normalized(layout.get('raw_text', '')).find(text)
                if text and offset >= 0:
                    matches.append(dict(page_index=adjacent['page_index'], source_order=layout['source_order'],
                                        full_literal_match_start=offset))
        yield dict(check, image_boxes=[layout['bbox_normalized'] for layout in images],
                   read_only_full_text_occurrences_in_neighbors=matches)


def _image_starts(state, _risks):
    for check in _image_candidates(state):
        yield _job('image_caption_role', check)
        yield _job('caption_starts_here', check)


def _selected_strips(state, _risks):
    prior = state['local_raw_evidence'].get('previous_page')
    if not prior:
        return
    for check in _image_candidates(state):
        text = normalized(target_layout(state, check)['raw_text'])
        matches = []
        for layout in prior.get('paragraph_boundary', []):
            if layout['source_type'] != 'text':
                continue
            raw = normalized(layout['raw_text'])
            offset = raw.find(text)
            if text and offset >= 0 and raw[offset + len(text):].strip():
                matches.append(dict(page_index=prior['page_index'], source_order=layout['source_order'],
                    full_literal_match_start=offset, remaining_text_after_match=raw[offset + len(text):]))
        if matches:
            value = dict(check, earlier_whole_text_occurrences_with_following_text=matches)
            for kind in ('detached_image_strip', 'prior_narrative_role', 'selected_whole_passage'):
                yield _job(kind, value)


def _dialogue(state, _risks):
    for key, check in state['checks'].items():
        if not key.startswith('gap_') or not check['present_decision']:
            continue
        if not any(char in target_layout(state, check)['raw_text'] for char in '“”「」『』«»"'):
            continue
        prior = previous_paragraph(state, check)
        if (prior is None or not prior['text_end'].rstrip().endswith(tuple('”」』»"'))
                or contradictory_hint(state, check)):
            continue
        yield _job('fresh_spoken_turn', check)
        yield _job('different_dialogue_speaker', check)


def _word_joins(state, risks):
    for key, check in state['checks'].items():
        if key.startswith('gap_') and check['present_decision'] and risks.get(key, 0) >= LOCAL_REVIEW_THRESHOLD:
            yield _job('joined_word_complete', check)


def supplementary_requests(state, risks) -> Iterator[dict]:
    """Batch independent facts using the same four-decision boundaries."""
    for family in (_duplicates, _sections, _attributions, _quotes, _selected_strips,
                   _image_starts, _dialogue, _word_joins):
        for packet in review_batches(state, list(family(state, risks))):
            if family is _sections:
                keys = [name for name, check in packet['state']['checks'].items()
                        if not possible_mixed_section(packet['state'], check)]
                if not keys:
                    continue
                packet['questions'] = {name: packet['questions'][name] for name in keys}
                packet['state']['checks'] = {name: packet['state']['checks'][name] for name in keys}
            yield packet
