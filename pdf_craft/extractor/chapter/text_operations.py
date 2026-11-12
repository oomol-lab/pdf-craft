"""Initial word-boundary suggestions and literal source-local edit operations.

All offsets address the unchanged reference-text surface. Projection executes
only declared removals; it never looks at another fragment or infers a word.
"""
from dataclasses import replace

from ...common.whitespace import is_whitespace
from ...language import is_latin_letter
from .analysis_types import _AnalysedHTMLTag, _ReferencePlaceholder
from .content import LINK_FLAGS, first, last


HYPHENS = (*LINK_FLAGS, '-')


def removal_error(kind):
    from .review_templates import review_text
    return ValueError(review_text('page_repair_text_feedback', kind=kind))


def propose_word_joins(paragraph):
    """Run only during initial analysis, before model review/repair."""
    from .chapter import SourceTextFragment
    result = {}
    texts = [item for item in paragraph.children if isinstance(item, SourceTextFragment)]
    for left, right in zip(texts, texts[1:]):
        ending, opening = last(left.content), first(right.content)
        if not isinstance(ending, str) or not isinstance(opening, str):
            continue
        end, start = ending.rstrip(), opening.lstrip()
        if not (len(end) >= 2 and start and end[-1] in HYPHENS
                and is_latin_letter(end[-2]) and is_latin_letter(start[0])):
            continue
        # Use the original source surface, not a stripped definition or a
        # projected text. In particular never delete a formula's last token.
        from .analysis_content import _analyse_content, _visible_content_text
        raw_left = list(left.source.content) if left.source is not None else left.content
        raw_right = list(right.source.content) if right.source is not None else right.content
        left_text = _visible_content_text(_analyse_content(raw_left, []))
        right_text = _visible_content_text(_analyse_content(raw_right, []))
        if not left_text.rstrip().endswith(end) or not right_text.lstrip().startswith(start):
            continue
        boundary = len(left_text.rstrip()) - 1
        result.setdefault(id(left), []).append((boundary, len(left_text)))
        leading = len(right_text) - len(right_text.lstrip())
        if leading:
            result.setdefault(id(right), []).append((0, leading))
    return result


def removal_anchors(layout):
    from .analysis_content import layout_reference_text
    text = layout_reference_text(layout)
    return [dict(before=text[max(0, start-32):start], mark=text[start:end],
                 after=text[end:end+32]) for start, end in layout.annotations.text_removals]


def apply_text_removals(content, removals):
    """Apply exact literal spans without changing rich-node/path identities."""
    if not removals:
        return content
    offset = 0
    visited = []

    def walk(parts):
        nonlocal offset
        result = []
        for part in parts:
            if isinstance(part, _AnalysedHTMLTag):
                result.append(replace(part, children=walk(part.children)))
                continue
            text = (part if isinstance(part, str) else part.text
                    if isinstance(part, _ReferencePlaceholder) else part.content)
            start, end = offset, offset + len(text)
            offset = end
            cuts = sorted((a, b) for a, b in removals if a < end and start < b)
            if cuts:
                if not isinstance(part, str) or any(a < start or b > end for a, b in cuts):
                    raise removal_error('leaf')
                pieces, cursor = [], 0
                for a, b in cuts:
                    pieces.append(text[cursor:a-start])
                    cursor = b-start
                    visited.append((a, b))
                pieces.append(text[cursor:])
                result.append(''.join(pieces))
            else:
                result.append(part)
        return tuple(result)

    ordered = sorted(removals)
    if any(a < 0 or b <= a or i and a < ordered[i-1][1]
           for i, (a, b) in enumerate(ordered)):
        raise removal_error('ranges')
    # The codec accepts only whitespace/hyphen removal, never missing words.
    from .analysis_content import _visible_content_text
    surface = _visible_content_text(content)
    if any(b > len(surface) or any(not is_whitespace(c) and c not in HYPHENS
                                  for c in surface[a:b]) for a, b in ordered):
        raise removal_error('characters')
    projected = walk(content)
    if sorted(visited) != ordered:
        raise removal_error('span')
    return projected
