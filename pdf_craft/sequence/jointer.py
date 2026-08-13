import re
from dataclasses import dataclass
from typing import Generator, Iterable, cast

from ..common import ASSET_TAGS, AssetRef
from ..expression import ExpressionKind, ParsedItem, parse_latex_expressions
from ..language import is_latin_letter
from ..markdown.paragraph import parse_raw_markdown
from ..pdf import TITLE_TAGS, PageLayout
from .chapter import AssetLayout, BlockLayout, InlineExpression, ParagraphLayout
from .content import Content, expand_text_in_content, first, last
from .mergeable import LINK_FLAGS, check_mergeable
from .reading_serials import split_reading_serials

_ASSET_CAPTION_TAGS = tuple(f"{t}_caption" for t in ASSET_TAGS)

_MARKDOWN_HEAD_PATTERN = re.compile(r"^#+\s+")
_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_TABLE_TITLE_PATTERN = re.compile(
    r"^\s*(?:table|tab\.?|表|表格)\s*[\d一二三四五六七八九十ivxlcdm]+[\s.:：、-]",
    re.IGNORECASE,
)
_TABLE_CAPTION_PATTERN = re.compile(
    r"^\s*(?:source|sources|note|notes|资料来源|来源|注|备注)\s*[:：]",
    re.IGNORECASE,
)
_FOOTNOTE_PATTERN = re.compile(
    r"^\s*(?:\d{1,2}[\).、]|[a-z][\).、]|[①-⑳]|[¹²³⁴⁵⁶⁷⁸⁹⁰]+)\s+",
    re.IGNORECASE,
)

_MAX_TABLE_TITLE_CHARS = 180
_MAX_TABLE_CAPTION_CHARS = 260
_LATEX_PLACEHOLDER_MARKER = "\uE000PDF_CRAFT_LATEX"


@dataclass
class _LastTail:
    page_para: ParagraphLayout
    override: list[AssetLayout]


@dataclass
class _AssetHolder:
    page_index: int
    ref: AssetRef
    det: tuple[int, int, int, int]
    title: str | None
    content: str
    caption: str | None
    hash: str | None


@dataclass
class _PendingParagraph:
    source: PageLayout
    paragraph: ParagraphLayout


class Jointer:
    def __init__(self, layouts: Iterable[tuple[int, list[PageLayout]]]) -> None:
        self._layouts = layouts

    def execute(self) -> Generator[ParagraphLayout | AssetLayout, None, None]:
        last_tail: _LastTail | None = None

        for page_index, raw_layouts in self._iter_layout_serials():
            # 此处为完成如下业务要求：
            # 1. 当阅读序列跨越 group（跨页、跨分栏、跨因图片而挤变形拆分的段落）时，必须对连接处验证。若它们是被拆分的自然段，则拼起来。
            # 2. 因为插图、表格而拆分的自然段，需将插图存起来接到完整的自然段最后，而不是任其分割自然段。
            layouts = list(self._join_and_handle_asset_layouts(page_index, raw_layouts))
            head, body, tail = self._split_layouts(layouts)

            if not body:
                if last_tail:
                    last_tail.override.extend(head)
                    last_tail.override.extend(tail)
                else:
                    yield from head
                    yield from tail
                continue

            first_layout = cast(ParagraphLayout, body[0])
            if last_tail and self._can_merge_paragraphs(
                last_tail.page_para, first_layout
            ):
                last_tail.page_para.blocks.extend(first_layout.blocks)
                del body[0]

            if not body:
                if last_tail:
                    last_tail.override.extend(head)
                    last_tail.override.extend(tail)
                else:
                    yield from head
                    yield from tail
                continue

            # 至此，连续吞并段落的流程遇阻而结束
            if last_tail:
                _normalize_paragraph_content(last_tail.page_para)
                yield last_tail.page_para
                yield from last_tail.override
                last_tail = None

            yield from head
            for i in range(len(body) - 1):
                yield body[i]

            last_tail = _LastTail(
                page_para=cast(ParagraphLayout, body[-1]),
                override=list(tail),
            )

        if last_tail:
            _normalize_paragraph_content(last_tail.page_para)
            yield last_tail.page_para
            yield from last_tail.override

    def _iter_layout_serials(
        self,
    ) -> Generator[tuple[int, list[PageLayout]], None, None]:
        for page_index, raw_layouts in self._layouts:
            for layouts in split_reading_serials(raw_layouts):
                yield page_index, layouts

    def _split_layouts(self, layouts: list[ParagraphLayout | AssetLayout]):
        head: list[AssetLayout] = []
        tail: list[AssetLayout] = []

        for layout in layouts:
            if isinstance(layout, ParagraphLayout):
                break
            head.append(layout)

        for i in range(len(layouts) - 1, -1, -1):
            if i < len(head):
                break
            layout = layouts[i]
            if isinstance(layout, ParagraphLayout):
                break
            tail.append(layout)

        tail.reverse()
        body = layouts[len(head) : len(layouts) - len(tail)]

        return head, body, tail

    def _join_and_handle_asset_layouts(
        self, page_index, layouts: list[PageLayout]
    ) -> Generator[ParagraphLayout | AssetLayout, None, None]:
        # layout 可能被后续处理，必须等待所有 layout 处理完毕
        for layout in list(
            self._join_asset_layouts(
                page_index=page_index,
                layouts=layouts,
            )
        ):
            if not isinstance(layout, _AssetHolder):
                yield layout
                continue

            if layout.ref == "equation":
                _normalize_equation(layout)
            if layout.ref == "table":
                _normalize_table(layout)

            yield AssetLayout(
                page_index=page_index,
                ref=layout.ref,
                det=layout.det,
                title=_parse_block_content(layout.title),
                content=_parse_block_content(layout.content),
                caption=_parse_block_content(layout.caption),
                hash=layout.hash,
            )

    def _join_asset_layouts(self, page_index, layouts: list[PageLayout]):
        last_asset: _AssetHolder | None = None
        pending_paragraph: _PendingParagraph | None = None

        def flush_pending_paragraph():
            nonlocal pending_paragraph
            if pending_paragraph:
                pending = pending_paragraph
                pending_paragraph = None
                return pending.paragraph
            return None

        for layout in layouts:
            if layout.ref in ASSET_TAGS:
                pending_title: str | None = None
                if pending_paragraph:
                    if layout.ref == "table" and _can_join_table_title(
                        pending_paragraph.source, layout
                    ):
                        pending_title = pending_paragraph.source.text
                        pending_paragraph = None
                    else:
                        pending = flush_pending_paragraph()
                        if pending:
                            yield pending
                if last_asset:
                    yield last_asset
                last_asset = _AssetHolder(
                    page_index=page_index,
                    ref=layout.ref,
                    det=layout.det,
                    title=pending_title,
                    content=layout.text,
                    caption=None,
                    hash=layout.hash,
                )
            elif layout.ref in _ASSET_CAPTION_TAGS:
                pending = flush_pending_paragraph()
                if pending:
                    yield pending
                if last_asset:
                    if last_asset.caption:
                        last_asset.caption += "\n" + layout.text
                    else:
                        last_asset.caption = layout.text
            else:
                if (
                    last_asset
                    and last_asset.ref == "table"
                    and _can_join_table_caption(last_asset, layout)
                ):
                    if last_asset.caption:
                        last_asset.caption += "\n" + layout.text
                    else:
                        last_asset.caption = layout.text
                    continue

                if last_asset:
                    yield last_asset
                    last_asset = None

                pending = flush_pending_paragraph()
                if pending:
                    yield pending

                if layout.ref in TITLE_TAGS:
                    # 将 Markdown 标题前的 `##` 之类的符号删除，DeepSeek OCR 总会生成这种符号
                    layout.text = _MARKDOWN_HEAD_PATTERN.sub("", layout.text)

                paragraph = ParagraphLayout(
                    ref=layout.ref,
                    level=-1,
                    blocks=[
                        BlockLayout(
                            page_index=page_index,
                            order=layout.order,
                            det=layout.det,
                            content=_parse_block_content(layout.text),
                        )
                    ],
                )
                if _can_wait_for_table_title(layout):
                    pending_paragraph = _PendingParagraph(
                        source=layout,
                        paragraph=paragraph,
                    )
                else:
                    yield paragraph
        if last_asset:
            yield last_asset
        pending = flush_pending_paragraph()
        if pending:
            yield pending

    def _can_merge_paragraphs(
        self, para1: ParagraphLayout, para2: ParagraphLayout
    ) -> bool:
        if para1.ref != "text":
            return False
        if para1.ref != para2.ref:
            return False

        block1 = para1.blocks[-1]
        block2 = para2.blocks[0]

        return check_mergeable(block1.content, block2.content)


def _normalize_equation(layout: _AssetHolder):
    if layout.ref != "equation" or not layout.content:
        return

    found_first_expression: bool = False
    expression_content: str = ""
    prefix_texts: list[str] = []
    tail_items: list[ParsedItem] = []

    for item in parse_latex_expressions(layout.content):
        if not found_first_expression and item.kind != ExpressionKind.TEXT:
            expression_content = item.content
            found_first_expression = True
        elif found_first_expression:
            tail_items.append(item)
        else:
            prefix_texts.append(item.content)

    if not found_first_expression:
        return

    if layout.title is not None:
        prefix_texts.insert(0, layout.title)

    if layout.caption is not None:
        tail_items.append(ParsedItem(kind=ExpressionKind.TEXT, content=layout.caption))

    if prefix_texts:
        layout.title = "".join(prefix_texts)

    layout.content = expression_content

    if tail_items:
        layout.caption = "".join(item.reverse() for item in tail_items)


def _normalize_table(layout: _AssetHolder):
    found_table_content: str | None = None
    head_buffer: list[str] = []
    tail_buffer: list[str] = []

    for part in (layout.title, "\n", layout.content, "\n", layout.caption):
        if not part:
            continue

        table_match = _TABLE_PATTERN.search(part)
        if not table_match:
            if found_table_content is None:
                head_buffer.append(part)
            else:
                tail_buffer.append(part)
            continue

        table_start = table_match.start()
        table_end = table_match.end()

        table_content = part[table_start:table_end]
        before = part[:table_start].rstrip()
        after = part[table_end:].lstrip()

        if before.strip():
            head_buffer.append(before)
        if after.strip():
            tail_buffer.append(after)

        found_table_content = table_content

    if not found_table_content:
        return

    head = "".join(head_buffer).strip()
    tail = "".join(tail_buffer).strip()

    layout.title = head if head else None
    layout.caption = tail if tail else None
    layout.content = found_table_content


def _can_wait_for_table_title(layout: PageLayout) -> bool:
    if layout.ref in TITLE_TAGS:
        return False
    return _is_table_title_text(layout.text)


def _can_join_table_title(text_layout: PageLayout, table_layout: PageLayout) -> bool:
    if not _is_table_title_text(text_layout.text):
        return False
    if not _is_block_above(text_layout.det, table_layout.det):
        return False
    if not _is_close_to_table(text_layout.det, table_layout.det):
        return False
    return _is_horizontally_related(text_layout.det, table_layout.det)


def _can_join_table_caption(asset: _AssetHolder, text_layout: PageLayout) -> bool:
    if not _is_table_caption_text(text_layout.text):
        return False
    if not _is_block_below(asset.det, text_layout.det):
        return False
    if not _is_close_to_table(asset.det, text_layout.det):
        return False
    return _is_horizontally_related(text_layout.det, asset.det)


def _is_table_title_text(text: str) -> bool:
    normalized = _normalize_table_adjacent_text(text)
    if not normalized or len(normalized) > _MAX_TABLE_TITLE_CHARS:
        return False
    if "\n\n" in normalized:
        return False
    return bool(_TABLE_TITLE_PATTERN.search(normalized)) or normalized.endswith((":", "："))


def _is_table_caption_text(text: str) -> bool:
    normalized = _normalize_table_adjacent_text(text)
    if not normalized or len(normalized) > _MAX_TABLE_CAPTION_CHARS:
        return False
    if "\n\n" in normalized:
        return False
    return bool(
        _TABLE_CAPTION_PATTERN.search(normalized)
        or _FOOTNOTE_PATTERN.search(normalized)
        or normalized.lower() == "category not applicable."
    )


def _normalize_table_adjacent_text(text: str) -> str:
    return " ".join(text.strip().split())


def _is_block_above(block_det, table_det) -> bool:
    return block_det[3] <= table_det[1]


def _is_block_below(table_det, block_det) -> bool:
    return table_det[3] <= block_det[1]


def _is_close_to_table(det1, det2) -> bool:
    vertical_gap = max(det1[1], det2[1]) - min(det1[3], det2[3])
    if vertical_gap < 0:
        return False

    table_height = max(_height(det1), _height(det2))
    return vertical_gap <= max(30, int(table_height * 0.12))


def _is_horizontally_related(text_det, table_det) -> bool:
    text_width = _width(text_det)
    table_width = _width(table_det)
    if text_width <= 0 or table_width <= 0:
        return False

    overlap = max(0, min(text_det[2], table_det[2]) - max(text_det[0], table_det[0]))
    overlap_ratio = overlap / min(text_width, table_width)
    width_ratio = min(text_width, table_width) / max(text_width, table_width)
    center_distance = abs(
        (text_det[0] + text_det[2]) / 2 - (table_det[0] + table_det[2]) / 2
    )

    return overlap_ratio >= 0.6 or (
        width_ratio >= 0.75 and center_distance <= table_width * 0.2
    )


def _width(det) -> int:
    return max(0, det[2] - det[0])


def _height(det) -> int:
    return max(0, det[3] - det[1])


# 将单词的连接符 `-` 删去，并将后半节单词移到前面一段拼接
def _normalize_paragraph_content(paragraph: ParagraphLayout):
    if len(paragraph.blocks) < 2:
        return

    for i in range(1, len(paragraph.blocks)):
        block1 = paragraph.blocks[i - 1]
        block2 = paragraph.blocks[i]

        text1 = last(block1.content)
        text2 = first(block2.content)
        if not isinstance(text1, str) or not isinstance(text2, str):
            continue

        text1 = text1.rstrip()
        text2 = text2.lstrip()
        if not _is_splitted_word(text1, text2):
            continue

        tail_end = 0
        for j in range(len(text2)):
            if is_latin_letter(text2[j]):
                tail_end = j + 1
            else:
                break

        block1.content[-1] = text1[:-1] + text2[:tail_end]
        block2.content[0] = text2[tail_end:].lstrip()
        if not block2.content[0]:
            del block2.content[0]

    # 极端情况下 block2 会因为单词被移走而被清空。此时要将其整个删去。
    paragraph.blocks = [block for block in paragraph.blocks if block.content]


def _parse_block_content(text: str | None) -> Content:
    if not text:
        return []

    protected_text, expressions, placeholder_pattern = _protect_latex_expressions(text)
    root_content: Content = parse_raw_markdown(protected_text)

    def expand_text(text: str):
        pos = 0
        for match in placeholder_pattern.finditer(text):
            if match.start() > pos:
                yield text[pos : match.start()]
            expression = expressions[int(match.group(1))]
            yield InlineExpression(
                kind=expression.kind,
                content=expression.content,
            )
            pos = match.end()
        if pos < len(text):
            yield text[pos:]

    expand_text_in_content(
        content=root_content,
        expand=expand_text,
    )
    return root_content


def _protect_latex_expressions(text: str) -> tuple[str, list[ParsedItem], re.Pattern]:
    expressions: list[ParsedItem] = []
    parts: list[str] = []
    placeholder_prefix = _create_latex_placeholder_prefix(text)

    for item in parse_latex_expressions(text):
        if item.kind == ExpressionKind.TEXT:
            parts.append(item.content)
            continue

        parts.append(f"{placeholder_prefix}{len(expressions)}\uE001")
        expressions.append(item)

    placeholder_pattern = re.compile(f"{re.escape(placeholder_prefix)}(\\d+)\uE001")
    return "".join(parts), expressions, placeholder_pattern


def _create_latex_placeholder_prefix(text: str) -> str:
    index = 0
    while True:
        prefix = f"{_LATEX_PLACEHOLDER_MARKER}_{index}_"
        if prefix not in text:
            return prefix
        index += 1


def _is_splitted_word(text1: str, text2: str) -> bool:
    return (
        len(text1) >= 2
        and text1[-1] in LINK_FLAGS
        and is_latin_letter(text1[-2])
        and is_latin_letter(text2[0])
    )
