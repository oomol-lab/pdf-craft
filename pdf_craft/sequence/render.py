from typing import Generator
from .chapter import ParagraphLayout
from .language import is_chinese_char


def render_paragraph_layout(layout: ParagraphLayout) -> Generator[str, None, None]:
    last_char: str | None = None
    for line in _normalize_lines(layout):
        if last_char is not None and (
            not is_chinese_char(last_char) or \
            not is_chinese_char(line[0])
        ):
            yield " "
        last_char = line[-1] if line else None
        yield line

def _normalize_lines(layout: ParagraphLayout) -> Generator[str, None, None]:
    for line_layout in layout.lines:
        for line in line_layout.content.splitlines():
            if line:
                yield line