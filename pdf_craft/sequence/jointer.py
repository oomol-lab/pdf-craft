from dataclasses import dataclass
import re

from typing import cast, Generator, Iterable

from ..expression import parse_latex_expressions, ExpressionKind, ParsedItem

from ..pdf import PageLayout
from ..common import ASSET_TAGS
from ..language import is_latin_letter
from ..markdown.paragraph import parse_raw_markdown, HTMLTag

from .chapter import ParagraphLayout, AssetLayout, BlockLayout, BlockMember, InlineExpression
from .content import expand_text_in_content, Content
from .reading_serials import split_reading_serials


TITLE_TAGS = ("title", "sub_title")

_ASSET_CAPTION_TAGS = tuple(f"{t}_caption" for t in ASSET_TAGS)

# to see https://github.com/opendatalab/MinerU/blob/fa1149cd4abf9db5e0f13e4e074cdb568be189f4/mineru/utils/span_pre_proc.py#L247
_LINE_STOP_FLAGS = (
    ".", "!", "?", "。", "！", "？", ")", "）", """, """, ";", "；",
    "]", "】", "}", ">", "》",
)

_LINE_CONTINUE_FLAGS = (
    "[", "【", "{", "<", "《", "、", ",", "，",
)

_LINK_FLAGS = (
    "-", "—", "–",
)

_MARKDOWN_HEAD_PATTERN = re.compile(r"^#+\s+")
_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


@dataclass
class _LastTail:
    page_para: ParagraphLayout
    override: list[AssetLayout]

class Jointer:
    def __init__(self, layouts: Iterable[tuple[int, list[PageLayout]]]) -> None:
        self._layouts = layouts

    def execute(self) -> Generator[ParagraphLayout | AssetLayout, None, None]:
        last: _LastTail | None = None

        for page_index, raw_layouts in self._iter_layout_serials():
            # 此处为完成如下业务要求：
            # 1. 当阅读序列跨越 group（跨页、跨分栏、跨因图片而挤变形拆分的段落）时，必须对连接处验证。若它们是被拆分的自然段，则拼起来。
            # 2. 因为插图、表格而拆分的自然段，需将插图存起来接到完整的自然段最后，而不是任其分割自然段。
            layouts = self._transform_and_join_asset_layouts(page_index, raw_layouts)
            head, body, tail = self._split_layouts(layouts)

            if not body:
                if last:
                    last.override.extend(head)
                    last.override.extend(tail)
                else:
                    yield from head
                    yield from tail
                continue

            first_layout = cast(ParagraphLayout, body[0])
            if last and self._can_merge_paragraphs(last.page_para, first_layout):
                last.page_para.blocks.extend(first_layout.blocks)
                del body[0]

            if not body:
                if last:
                    last.override.extend(head)
                    last.override.extend(tail)
                else:
                    yield from head
                    yield from tail
                continue

            # 至此，连续吞并段落的流程遇阻而结束
            if last:
                _normalize_paragraph_content(last.page_para)
                yield last.page_para
                yield from last.override
                last = None

            yield from head
            for i in range(len(body) - 1):
                yield body[i]

            last = _LastTail(
                page_para=cast(ParagraphLayout, body[-1]),
                override=list(tail),
            )

        if last:
            _normalize_paragraph_content(last.page_para)
            yield last.page_para
            yield from last.override

    def _iter_layout_serials(self) -> Generator[tuple[int, list[PageLayout]], None, None]:
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
        body = layouts[len(head):len(layouts) - len(tail)]

        return head, body, tail

    def _transform_and_join_asset_layouts(self, page_index, layouts: list[PageLayout]):
        last_asset: AssetLayout | None = None
        jointed_layouts: list[ParagraphLayout | AssetLayout] = []
        for layout in layouts:
            if layout.ref in ASSET_TAGS:
                if last_asset:
                    jointed_layouts.append(last_asset)
                last_asset = AssetLayout(
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
                    jointed_layouts.append(last_asset)
                    last_asset = None
                if layout.ref in TITLE_TAGS:
                    # 将 Markdown 标题前的 `##` 之类的符号删除，DeepSeek OCR 总会生成这种符号
                    layout.text = _MARKDOWN_HEAD_PATTERN.sub("", layout.text)

                jointed_layouts.append(ParagraphLayout(
                    ref=layout.ref,
                    blocks=[BlockLayout(
                        page_index=page_index,
                        det=layout.det,
                        content=_parse_block_content(layout.text),
                    )],
                ))

        if last_asset:
            jointed_layouts.append(last_asset)

        for layout in jointed_layouts:
            if isinstance(layout, AssetLayout):
                if layout.ref == "equation":
                    _normalize_equation(layout)
                if layout.ref == "table":
                    _normalize_table(layout)
        return jointed_layouts

    # too see https://github.com/opendatalab/MinerU/blob/fa1149cd4abf9db5e0f13e4e074cdb568be189f4/mineru/backend/pipeline/para_split.py#L253
    def _can_merge_paragraphs(self, para1: ParagraphLayout, para2: ParagraphLayout) -> bool:
        if para1.ref != "text":
            return False
        if para1.ref != para2.ref:
            return False

        block1 = para1.blocks[-1]
        block2 = para2.blocks[0]
        _, text1 = block1.det, _block_plain_text(block1)
        _, text2 = block2.det, _block_plain_text(block2)

        if not text1 or not text2:
            return False

        text1_stripped = text1.rstrip()
        if not text1_stripped:
            return False

        text2_stripped = text2.lstrip()
        if not text2_stripped:
            return False

        # 条件1：前一个段落如果以句尾符号结尾，说明是完整段落，不应合并
        if text1_stripped.endswith(_LINE_STOP_FLAGS):
            return False

        # 条件2：前一个段落结束的符号明显表明句子未结束，则必须合并
        if text1_stripped.endswith(_LINE_CONTINUE_FLAGS):
            return True

        first_char = text2_stripped[0]

        # 条件3：下一个段落的第一个字符不是数字
        # 如果以数字开头，可能是编号列表的新段落（如"1. xxx"）
        if first_char.isdigit():
            return False

        # 条件4：下一个段落的第一个字符不是大写字母
        # 如果以大写字母开头，可能是新段落的开始（特别是英文）
        if first_char.isupper():
            return False

        # 条件5：如果 para1 结尾是拉丁字母 + `-`，para2 开头是拉丁字母，则允许合并（跨段单词拼接）
        if is_latin_letter(text2[0]):
            if len(text1) >= 2 and text1[-1] in _LINK_FLAGS and \
               is_latin_letter(text1[-2]):
                return True
            if is_latin_letter(text1[-1]):
                return False

        return True

def _block_plain_text(block: BlockLayout) -> str:
    # FIXME: 不应该打印完整的文字，而是打印第一和最后一组文字就够了
    #        这里涉及树的遍历的第一元素和最后一个元素的概念，真正渲染的时候，人眼看见的是树的第一元素，而非第一节点
    return "".join(list(_search_visiable_texts(block.content)))

def _normalize_equation(layout: AssetLayout):
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


def _normalize_table(layout: AssetLayout):
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

def _normalize_paragraph_content(paragraph: ParagraphLayout):
    if len(paragraph.blocks) < 2:
        return

    for i in range(1, len(paragraph.blocks)):
        block1 = paragraph.blocks[i - 1]
        block2 = paragraph.blocks[i]

        # FIXME: 这里假设首尾都是 str，实际上这不一定
        content1 = _block_plain_text(block1).rstrip()
        content2 = _block_plain_text(block2).lstrip()

        if not _is_splitted_word(content1, content2):
            continue

        tail_end = 0
        for j in range(len(content2)):
            if is_latin_letter(content2[j]):
                tail_end = j + 1
            else:
                break

        block1.content[0] = content1[:-1] + content2[:tail_end]
        block2.content[0] = content2[tail_end:].lstrip()

    paragraph.blocks = [
        block for block in paragraph.blocks
        if _block_plain_text(block).strip()
    ]

def _parse_block_content(text: str) -> Content:
    root_content: Content = parse_raw_markdown(text)

    def expand_text(text: str):
        for item in parse_latex_expressions(text):
            if item.kind != ExpressionKind.TEXT:
                yield InlineExpression(
                    kind=item.kind,
                    content=item.content,
                )
            elif item.content: # Only add non-empty strings
                yield item.content

    expand_text_in_content(
        content=root_content,
        expand=expand_text,
    )
    return root_content

def _is_splitted_word(text1: str, text2: str) -> bool:
    return (
        len(text1) >= 2 and text1[-1] == "-" and \
        is_latin_letter(text1[-2]) and \
        is_latin_letter(text2[0])
    )

def _search_visiable_texts(content: Content) -> Generator[str, None, None]:
    for child in content:
        if isinstance(child, HTMLTag):
            yield from _search_visiable_texts(child.children)
        elif isinstance(child, InlineExpression):
            yield "<expr>"
            yield child.content
            yield "</expr>"
        elif isinstance(child, str):
            yield child