"""Read-only completion proofs for footnote-mode main-reading boundaries."""
import re
from .local_review import local_review_requests, review_batches
from .review_evidence import _quotes, previous_paragraph, contradictory_hint, target_layout
from .review_questions import question
from .review_encoding import compact_review_evidence, TEXT_ENCODING_NOTE
from .structural_review import _decisions

PAIRED_WORD_PREFIXES = ('seam_predecessor_', 'seam_word_')

COMPLETION_TARGETS = {
    'left_word_complete_': 'word',
    'left_frame_complete_': 'syntax',
    'ended_utterance_boundary_': 'syntax',
    'math_unit_boundary_': 'syntax',
    'reporting_join_': 'join',
}


def _left_completions(local, scores, threshold):
    boundary = 1 - threshold
    risks = {key: threshold if value == boundary else 1 - value
             for key, value in scores.items()}
    yield from (job for job in _quotes(local, risks, threshold=threshold)
                if job[0].startswith(('left_word_complete_', 'left_frame_complete_')))
    for key, check in local['checks'].items():
        if (not key.startswith('gap_') or check['present_decision']
                or scores.get(key + '_syntax', 1) > boundary):
            continue
        prior = previous_paragraph(local, check)
        if (prior is None
                or prior['page_index'] != local['local_raw_evidence']['target_page']['page_index']
                or contradictory_hint(local, check)):
            continue
        ending = prior['text_end'].rstrip()
        if (len(ending) < 2 or ending[-1] not in '”」』»"'
                or not ending[:-1].rstrip().endswith(('…', '...'))):
            continue
        name = 'ended_utterance_boundary_' + key.rsplit('_', 1)[1]
        yield name, question('ended_utterance_boundary', name), dict(check,
            raw_delimiter_cues=dict(preceding_hint_ends_ellipsis_and_quote=True))


def _math_units(local, scores, threshold):
    for key, check in local['checks'].items():
        if (not key.startswith('gap_') or check['present_decision']
                or scores.get(key + '_syntax', 1) > 1 - threshold):
            continue
        text = target_layout(local, check)['raw_text']
        prior = previous_paragraph(local, check)
        if ('\\(' not in text and not re.fullmatch(r'[\s\d.,…+-]+', text)
                and not (prior and '\\(' in prior['text_end'])):
            continue
        name = 'math_unit_boundary_' + key.rsplit('_', 1)[1]
        yield name, question('math_unit_boundary', name), check


def _reporting_joins(local, scores, threshold):
    pairs = {'"': '"', '“': '”', '「': '」', '『': '』', '«': '»'}
    for key, check in local['checks'].items():
        if (not key.startswith('gap_') or not check['present_decision']
                or scores.get(key, 1) > 1 - threshold):
            continue
        prior = previous_paragraph(local, check)
        text = target_layout(local, check)['raw_text'].strip()
        if (prior is None or not prior['text_end'].rstrip().endswith((':', '：'))
                or not text or text[0] not in pairs or not text.endswith(pairs[text[0]])):
            continue
        name = 'reporting_join_' + key.rsplit('_', 1)[1]
        yield name, question('reporting_join', name), check


def _paired_words(local, scores, threshold):
    for key, check in local['checks'].items():
        if (not key.startswith('gap_') or not check['present_decision']
                or scores.get(key, 1) > 1 - threshold):
            continue
        text = target_layout(local, check)['raw_text']
        prefix = re.match(r'^([\u4e00-\u9fff]{1,3})[，。！？、…,!?]', text)
        prior = previous_paragraph(local, check)
        if prefix is None or prior is None:
            continue
        value = dict(check, proposed_left_source=dict(
            page_index=prior['page_index'], source_order=prior['source_order']),
            literal_left_tail=prior['text_end'][-24:], short_current_prefix=prefix[1])
        for kind in ('seam_predecessor', 'seam_word'):
            name = kind + '_' + key.rsplit('_', 1)[1]
            yield name, question(kind, name), value


def completion_requests(state, scores, threshold):
    """Keep families separate so existing proof packets stay byte-identical."""
    if not any(key.startswith('gap_') and value <= 1 - threshold
               for key, value in scores.items()):
        return
    for family in (_left_completions, _math_units, _reporting_joins, _paired_words):
        seen = set()
        for packet in local_review_requests(state):
            jobs = []
            for checks in _decisions(packet['state']['checks']):
                local = dict(packet['state'], checks=checks)
                for job in family(local, scores, threshold):
                    if job[0] not in seen:
                        jobs.append(job)
                        seen.add(job[0])
            for result in review_batches(packet['state'], jobs):
                result['state'] = compact_review_evidence(result['state'])
                result['state']['encoding_note'] = TEXT_ENCODING_NOTE
                yield state['target_page']['page_index'], result


def apply_completion_proofs(values, proofs, threshold):
    """No completion proof approves ownership, another gap, or another test."""
    for name, proof in proofs.items():
        part = next(part for prefix, part in COMPLETION_TARGETS.items() if name.startswith(prefix))
        key = f"gap_{name.rsplit('_', 1)[1]}"
        if part != 'join':
            key += '_' + part
        if key in values and values[key] <= 1 - threshold:
            values[key] = max(values[key], proof)


def apply_paired_word_proofs(values, proofs, threshold):
    """Literal word completion cannot authorize using the wrong predecessor."""
    for name, preceding in proofs.items():
        if not name.startswith('seam_predecessor_'):
            continue
        order = name.rsplit('_', 1)[1]
        key = 'gap_' + order
        if key in values and values[key] <= 1 - threshold:
            values[key] = max(values[key], min(preceding, proofs['seam_word_' + order]))
