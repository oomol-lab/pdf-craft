import re

from typing import Generator, Iterable

from ..pdf import PageLayout
from ..common import ASSET_TAGS
from .chapter import ParagraphLayout, AssetLayout, LineLayout, Reference
from .language import is_latin_letter


TITLE_TAGS = ("title", "sub_title")

_ASSET_CAPTION_TAGS = tuple(f"{t}_caption" for t in ASSET_TAGS)

# to see https://github.com/opendatalab/MinerU/blob/fa1149cd4abf9db5e0f13e4e074cdb568be189f4/mineru/utils/span_pre_proc.py#L247
_LINE_STOP_FLAGS = (
    ".", "!", "?", "。", "！", "？", ")", "）", """, """, ":", "：", ";", "；",
    "]", "】", "}", "}", ">", "》", "、", ",", "，", "-", "—", "–",
)

_MARKDOWN_HEAD_PATTERN = re.compile(r"^#+\s+")
_LATEX_PATTERNS = [
    (re.compile(r"\\\["), re.compile(r"\\\]")),  # \[...\]
    (re.compile(r"\$\$"), re.compile(r"\$\$")),  # $$...$$
    (re.compile(r"\\\("), re.compile(r"\\\)")),  # \(...\)
    (re.compile(r"\$"), re.compile(r"\$")),      # $...$
]

_TABLE_PATTERN = re.compile(r"<table[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


class Jointer:
    def __init__(self, layouts: Iterable[tuple[int, list[PageLayout]]]) -> None:
        self._layouts = layouts

    def execute(self) -> Generator[ParagraphLayout | AssetLayout, None, None]:
        last_page_para: ParagraphLayout | None = None
        for page_index, raw_layouts in self._layouts:
            layouts = self._transform_and_join_asset_layouts(page_index, raw_layouts)
            if not layouts:
                continue

            first_layout = layouts[0]
            if last_page_para and isinstance(first_layout, ParagraphLayout) and \
               self._can_merge_paragraphs(last_page_para, first_layout):
                last_page_para.lines.extend(first_layout.lines)
                del layouts[0]

            if not layouts:
                continue

            if last_page_para:
                _normalize_paragraph_content(last_page_para)
                yield last_page_para
                last_page_para = None

            for i in range(len(layouts) - 1):
                yield layouts[i]

            last_layout = layouts[-1]
            if last_layout:
                if isinstance(last_layout, ParagraphLayout):
                    last_page_para = last_layout
                else:
                    yield last_layout

        if last_page_para:
            _normalize_paragraph_content(last_page_para)
            yield last_page_para

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
                    lines=[LineLayout(
                        page_index=page_index,
                        det=layout.det,
                        content=[layout.text],
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

        line1 = para1.lines[-1]
        line2 = para2.lines[0]
        det1, text1 = line1.det, line_text(line1)
        det2, text2 = line2.det, line_text(line2)

        if not text1 or not text2:
            return False

        text1_stripped = text1.rstrip()
        if not text1_stripped:
            return False

        text2_stripped = text2.lstrip()
        if not text2_stripped:
            return False

        # 条件1：前一个段落的末尾不以句尾符号结尾
        # 如果以句尾符号结尾，说明是完整段落，不应合并
        if text1_stripped.endswith(_LINE_STOP_FLAGS):
            return False

        layout_width1 = det1[2] - det1[0]
        layout_width2 = det2[2] - det2[0]

        # 条件2：两个段落的宽度相似
        # 差异不应超过较小宽度
        min_layout_width = min(layout_width1, layout_width2)
        if abs(layout_width1 - layout_width2) >= min_layout_width:
            return False

        first_char = text2_stripped[0]

        # 条件3：下一个段落的第一个字符不是数字
        # 如果以数字开头，可能是编号列表的新段落（如"1. xxx"）
        if first_char.isdigit():
            return False

        # 条件4：下一个段落的第一个字符不是大写字母
        # 如果以大写字母开头，可能是新段落的开始（特别是英文）
        if first_char.isupper():
            return False

        # 条件5：如果 para1 结尾是拉丁字母+"-"，para2 开头是拉丁字母，则允许合并（跨段单词拼接）
        if _is_splitted_word(text1, text2):
            return True

        return True

def _normalize_equation(layout: AssetLayout):
    content = layout.content
    if not content:
        return
    latex_start = -1
    latex_end = -1

    for start_pat, end_pat in _LATEX_PATTERNS:
        start_match = start_pat.search(content)
        if start_match:
            start_idx = start_match.start()
            end_match = end_pat.search(content[start_match.end():])
            if end_match:
                latex_start = start_idx
                latex_end = start_match.end() + end_match.end()
                break

    if latex_start >= 0 and latex_end > latex_start:
        _extract_and_split_content(layout, latex_start, latex_end)

def _normalize_table(layout: AssetLayout):
    content = layout.content
    if not content:
        return
    table_match = _TABLE_PATTERN.search(content)

    if table_match:
        table_start = table_match.start()
        table_end = table_match.end()
        _extract_and_split_content(layout, table_start, table_end)

def _normalize_paragraph_content(paragraph: ParagraphLayout):
    if len(paragraph.lines) < 2:
        return

    for i in range(1, len(paragraph.lines)):
        line1 = paragraph.lines[i - 1]
        line2 = paragraph.lines[i]
        content1 = line_text(line1).rstrip()
        content2 = line_text(line2).lstrip()

        if not _is_splitted_word(content1, content2):
            continue

        tail_end = 0
        for j in range(len(content2)):
            if is_latin_letter(content2[j]):
                tail_end = j + 1
            else:
                break

        line1.content[0] = content1[:-1] + content2[:tail_end]
        line2.content[0] = content2[tail_end:].lstrip()

    paragraph.lines = [
        line for line in paragraph.lines
        if line_text(line).strip()
    ]

def _extract_and_split_content(layout: AssetLayout, start: int, end: int):
    content = layout.content
    extracted = content[start:end]
    before = content[:start].strip()

    if before:
        if layout.title:
            layout.title = layout.title + "\n" + before
        else:
            layout.title = before

    after = content[end:].strip()
    if after:
        if layout.caption:
            layout.caption = layout.caption + "\n" + after
        else:
            layout.caption = after

    layout.content = extracted

def line_text(line: LineLayout) -> str:
    """
    从 LineLayout 中提取文本内容。
    支持处理混合的字符串和引用对象内容。
    """
    result_parts = []
    for part in line.content:
        if isinstance(part, str):
            result_parts.append(part)
        # 对于 Reference 对象，我们可以选择忽略或者转换为特定格式
        # 在 jointer 阶段，可能还需要原始文本，所以暂时转换为空字符串
        elif isinstance(part, Reference):
            # Reference 对象在这里暂时不处理，或者可以添加标记
            pass

    return "".join(result_parts)

def _is_splitted_word(text1: str, text2: str) -> bool:
    return (
        len(text1) >= 2 and text1[-1] == "-" and \
        is_latin_letter(text1[-2]) and \
        is_latin_letter(text2[0])
    )
