"""Source-specific quotation proofs for rejected main-reading paragraph breaks."""
from .local_review import local_review_requests, review_batches
from .review_evidence import _quotes, previous_paragraph
from .review_questions import question
from .review_encoding import compact_review_evidence, TEXT_ENCODING_NOTE
from .structural_review import _decisions


def quotation_requests(state, scores, threshold):
    """A raw colon admits a question, never establishes a correct boundary."""
    pass_boundary = 1 - threshold
    risks = {key: threshold if value == pass_boundary else 1 - value
             for key, value in scores.items() if key.startswith('gap_')}
    if not any(value >= threshold for value in risks.values()):
        return
    seen = set()
    for packet in local_review_requests(state):
        jobs = []
        for checks in _decisions(packet['state']['checks']):
            local = dict(packet['state'], checks=checks)
            candidates = [job for job in _quotes(local, risks, threshold=threshold, entry='footnote_followup')
                          if job[0].startswith('quotation_boundary_')]
            for key, check in checks.items():
                if (not key.startswith('gap_') or check['present_decision']
                        or scores.get(key + '_syntax', 1) > pass_boundary):
                    continue
                prior = previous_paragraph(local, check)
                if prior is None or not prior['text_end'].rstrip().endswith((':', '：')):
                    continue
                name = 'quotation_boundary_' + key.rsplit('_', 1)[1]
                if any(job[0] == name for job in candidates):
                    continue
                spec = question('quotation_boundary', name, entry='footnote_followup', recitation=True)
                candidates.append((name, spec, dict(check,
                    raw_delimiter_cues=dict(preceding_hint_ends_colon=True))))
            for job in candidates:
                if job[0] not in seen:
                    jobs.append(job)
                    seen.add(job[0])
        for result in review_batches(packet['state'], jobs):
            result['state'] = compact_review_evidence(result['state'])
            result['state']['encoding_note'] = TEXT_ENCODING_NOTE
            yield state['target_page']['page_index'], result


def apply_quotation_proofs(values, proofs, threshold):
    """Only relax the matching rejected gap; preserve all independent faults."""
    for name, proof in proofs.items():
        order = name.rsplit('_', 1)[1]
        for key in (f'gap_{order}_word', f'gap_{order}_syntax'):
            if key in values and values[key] <= 1 - threshold:
                values[key] = max(values[key], proof)
