from .chapter import AssetLayout, Chapter, ParagraphLayout, search_references_in_chapter
from .content import Content, expand_text_in_content

_LEFT_ONLY_ASCII_TO_FULLWIDTH = {
    ",": "，",
    ";": "；",
    "?": "？",
    "!": "！",
}

_BOTH_SIDES_ASCII_TO_FULLWIDTH = {
    ":": "：",
}


# to fix https://github.com/oomol-lab/pdf-craft/issues/310
def normalize_punctuation_in_chapter(chapter: Chapter) -> Chapter:
    _normalize_layouts(chapter.layouts)
    for ref in search_references_in_chapter(chapter):
        _normalize_layouts(ref.layouts)
    return chapter


def _normalize_layouts(layouts: list[ParagraphLayout | AssetLayout]) -> None:
    for layout in layouts:
        if isinstance(layout, ParagraphLayout):
            for block in layout.blocks:
                _normalize_content(block.content)
        elif isinstance(layout, AssetLayout):
            _normalize_content(layout.title)
            _normalize_content(layout.caption)


def _normalize_content(content: Content) -> None:
    segments: list[str] = []

    def collect_text_segments(text: str) -> str:
        segments.append(text)
        return text

    expand_text_in_content(
        content=content,
        expand=lambda text: (collect_text_segments(text),),
    )

    normalized_segments = _normalize_segments(segments)
    if normalized_segments is None:
        return

    segment_index = 0

    def replace_text(_: str):
        nonlocal segment_index
        text = normalized_segments[segment_index]
        segment_index += 1
        return (text,)

    expand_text_in_content(
        content=content,
        expand=replace_text,
    )


def _normalize_segments(segments: list[str]) -> list[str] | None:
    if not segments:
        return None

    full_chars: list[str] = []
    owners: list[tuple[int, int]] = []
    for segment_idx, text in enumerate(segments):
        for char_idx, char in enumerate(text):
            full_chars.append(char)
            owners.append((segment_idx, char_idx))

    segment_chars = [list(text) for text in segments]
    changed = False

    for idx, char in enumerate(full_chars):
        left = _search_near_char(full_chars, idx, reverse=True)
        if left is None:
            continue

        mapped = _LEFT_ONLY_ASCII_TO_FULLWIDTH.get(char)
        if mapped is not None:
            if not _is_han_char(left):
                continue
        else:
            mapped = _BOTH_SIDES_ASCII_TO_FULLWIDTH.get(char)
            if mapped is None:
                continue
            right = _search_near_char(full_chars, idx, reverse=False)
            if right is None or not (_is_han_char(left) and _is_han_char(right)):
                continue

        owner_segment_idx, owner_char_idx = owners[idx]
        segment_chars[owner_segment_idx][owner_char_idx] = mapped
        changed = True

    if not changed:
        return None

    return ["".join(chars) for chars in segment_chars]


def _search_near_char(chars: list[str], center: int, reverse: bool) -> str | None:
    if reverse:
        indexes = range(center - 1, -1, -1)
    else:
        indexes = range(center + 1, len(chars))

    for idx in indexes:
        char = chars[idx]
        if char.isspace():
            continue
        return char
    return None


def _is_han_char(char: str) -> bool:
    code = ord(char)
    return (
        0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x20000 <= code <= 0x2A6DF  # CJK Unified Ideographs Extension B
        or 0x2A700 <= code <= 0x2B73F  # CJK Unified Ideographs Extension C
        or 0x2B740 <= code <= 0x2B81F  # CJK Unified Ideographs Extension D
        or 0x2B820 <= code <= 0x2CEAF  # CJK Unified Ideographs Extension E
        or 0x2CEB0 <= code <= 0x2EBEF  # CJK Unified Ideographs Extension F/G
    )
