"""Independent reader for character-coordinate comparison feedback in tests."""
import re
from pdf_craft.transformer.fill_text import comparison_text


def apply_displayed_diff(message: str, candidate: str) -> str:
    match = re.search(r'(?m)^(?P<fence>`{3,})diff\n(.*?)\n(?P=fence)$', message, re.S)
    assert match is not None
    lines = match.group(2).splitlines()
    assert lines[:2] == ['--- candidate.normalized.txt', '+++ translation.normalized.txt']
    candidate = comparison_text(candidate)
    output, cursor, index = [], 0, 2
    while index < len(lines):
        header = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', lines[index])
        assert header is not None, lines[index]
        old, old_count, new, new_count = map(int, header.groups())
        old -= bool(old_count)
        new -= bool(new_count)
        index += 1
        old_text, new_text = [], []
        while index < len(lines) and not lines[index].startswith('@@ '):
            line = lines[index]
            assert line and line[0] in ' +-'
            if line[0] != '+':old_text.append(line[1:])
            if line[0] != '-':new_text.append(line[1:])
            index += 1
        removed, added = ''.join(old_text), ''.join(new_text)
        assert len(removed) == old_count
        assert len(added) == new_count
        assert old >= cursor
        assert candidate[old:old+old_count] == removed
        output.append(candidate[cursor:old])
        assert sum(map(len, output)) == new
        output.append(added)
        cursor = old+old_count
    output.append(candidate[cursor:])
    return ''.join(output)
