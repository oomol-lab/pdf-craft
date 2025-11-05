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
        yield line

def _normalize_lines(layout: ParagraphLayout) -> Generator[str, None, None]:
    for _, text in layout.content:
        for line in text.splitlines():
            if line:
                yield line