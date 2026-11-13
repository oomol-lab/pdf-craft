"""Character diff presentation and tentative, exact-match-based reminders.

No semantic classification, XML field allocation, acceptance or text mutation.
"""
import re

from jinja2 import Environment, PackageLoader

_FEEDBACK = Environment(loader=PackageLoader("pdf_craft.transformer.xml_translator", "data"), autoescape=False)
_CONTEXT = 40
_MAX_EDITS = 10
_MAX_PIECE = 6000
_MAX_PAYLOAD = 12000


def _count(text: str, passage: str) -> int | None:
    """Count exact occurrences only when no two occurrences overlap."""
    position, end, count = 0, 0, 0
    while (found := text.find(passage, position)) >= 0:
        if found < end:
            return None
        count += 1
        end = found + len(passage)
        position = found + 1
    return count


def _intersects_match(passage: str, text: str, ranges: list[tuple[int, int]]) -> bool:
    # A complete exact occurrence must overlap a changed range. The window
    # includes boundary punctuation which a valid diff may align on either side.
    return any(passage in text[max(0, start-len(passage)+1):end+len(passage)-1]
               for start, end in ranges)


def render_difference(expected: str, actual: str, edits: list[tuple[str, int, int, int, int]]) -> str:
    displayed = edits[:_MAX_EDITS]
    additions = list(dict.fromkeys(expected[a:b] for _, a, b, _, _ in displayed if b > a))
    removals = list(dict.fromkeys(actual[c:d] for _, _, _, c, d in displayed if d > c))
    added_ranges = [(a, b) for _, a, b, _, _ in displayed if b > a]
    removed_ranges = [(c, d) for _, _, _, c, d in displayed if d > c]
    matched = (any(_intersects_match(value, actual, removed_ranges) for value in additions)
               or any(_intersects_match(value, expected, added_ranges) for value in removals))
    count_pairs = set()
    for passage in dict.fromkeys([*additions, *removals]):
        if len(passage) > _MAX_PIECE:
            continue
        expected_count, actual_count = _count(expected, passage), _count(actual, passage)
        if (expected_count is not None and actual_count is not None
                and expected_count > 0 and actual_count > 0 and expected_count != actual_count):
            count_pairs.add((expected_count, actual_count))
    # Different passages may have different counts. Report numbers only if one
    # pair describes all eligible passages, rather than selecting a mystery one.
    counts = next(iter(count_pairs)) if len(count_pairs) == 1 else None
    presence = (not matched and not count_pairs and any(value in actual for value in additions))
    hunks, remaining = [], _MAX_PAYLOAD
    truncated = len(edits) > len(displayed)
    groups: list[list[tuple[str, int, int, int, int]]] = []
    for edit in displayed:
        if groups and edit[1]-groups[-1][-1][2] <= 2*_CONTEXT:
            groups[-1].append(edit)
        else:
            groups.append([edit])
    for group in groups:
        _, a, _, c, _ = group[0]
        _, _, b, _, d = group[-1]
        before = after = 0
        while before < _CONTEXT and before < min(a, c) and expected[a-before-1] == actual[c-before-1]:
            before += 1
        while (after < _CONTEXT and b+after < len(expected) and d+after < len(actual)
               and expected[b+after] == actual[d+after]):
            after += 1
        lines = [' '+actual[c-before:c]] if before else []
        previous = c
        for _, left, right, start, end in group:
            if previous < start:
                lines.append(' '+actual[previous:start])
            for marker, value in [('-', actual[start:end]), ('+', expected[left:right])]:
                if not value:
                    continue
                shown = min(len(value), _MAX_PIECE, remaining)
                if shown:
                    lines.append(marker+value[:shown])
                remaining -= shown
                truncated |= shown < len(value)
            previous = end
        if after:
            lines.append(' '+actual[d:d+after])
        old_length, new_length = before+d-c+after, before+b-a+after
        hunks.append(dict(old_start=c-before+bool(old_length), old_length=old_length,
                          new_start=a-before+bool(new_length), new_length=new_length, lines=lines))
    longest = max((len(run) for hunk in hunks for line in hunk['lines']
                   for run in re.findall(r'`+', line)), default=0)
    return _FEEDBACK.get_template('text_diff.jinja').render(
        fence='`'*max(3, longest+1),
        hunks=hunks, truncated=truncated, matched=matched, presence=presence,
        counts=counts, uncertain_counts=len(count_pairs) > 1).strip()
