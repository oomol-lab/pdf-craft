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
        for layout in layouts:
            if layout.ref in ASSET_TAGS:
                if last_asset:
                    yield last_asset
                last_asset = _AssetHolder(
                    page_index=page_index,
                    ref=layout.ref,
                    det=layout.det,
                    title=None,
                    content=layout.text,
                    caption=None,
                    hash=layout.hash,
                )
            elif layout.ref in _ASSET_CAPTION_TAGS:
                if last_asset:
                    if last_asset.caption:
                        last_asset.caption += "\n" + layout.text
                    else:
                        last_asset.caption = layout.text
            else:
                if last_asset:
                    yield last_asset
                    last_asset = None
                if layout.ref in TITLE_TAGS:
                    # 将 Markdown 标题前的 `##` 之类的符号删除，DeepSeek OCR 总会生成这种符号
                    layout.text = _MARKDOWN_HEAD_PATTERN.sub("", layout.text)

                yield ParagraphLayout(
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
        if last_asset:
            yield last_asset

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

    root_content: Content = parse_raw_markdown(text)

    def expand_text(text: str):
        for item in parse_latex_expressions(text):
            if item.kind != ExpressionKind.TEXT:
                yield InlineExpression(
                    kind=item.kind,
                    content=item.content,
                )
            elif item.content:  # Only add non-empty strings
                yield item.content

    expand_text_in_content(
        content=root_content,
        expand=expand_text,
    )
    return root_content


def _is_splitted_word(text1: str, text2: str) -> bool:
    return (
        len(text1) >= 2
        and text1[-1] in LINK_FLAGS
        and is_latin_letter(text1[-2])
        and is_latin_letter(text2[0])
    )
