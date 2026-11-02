import re

from ...language import is_latin_letter
from ...expression import ExpressionKind
from ...markdown.paragraph import HTMLTag
from .chapter import BlockMember, InlineExpression
from .content import first, last

# 句尾标识符号
# to see https://github.com/opendatalab/MinerU/blob/fa1149cd4abf9db5e0f13e4e074cdb568be189f4/mineru/utils/span_pre_proc.py#L247
_LINE_STOP_FLAGS = (
    ".",
    "!",
    "?",
    "。",
    "！",
    "？",
    ")",
    "）",
    ";",
    "；",
    "]",
    "】",
    "}",
    ">",
    "》",
)

# A closing quote alone may end an inline term or precede attribution. Only
# treat a completed quotation followed by a new quotation as a boundary.
_QUOTE_OPENINGS = ('"', "'", "“", "‘", "「", "『", "«")
_QUOTE_CLOSINGS = '\"\'”’」』»'
_QUOTED_SENTENCE_ENDINGS = (".", "!", "?", "。", "！", "？", ";", "；")

# 续行标识符号
_LINE_CONTINUE_FLAGS = (
    "[",
    "【",
    "{",
    "<",
    "《",
    "、",
    ",",
    "，",
)

# 连接符号（用于跨行单词拼接）
LINK_FLAGS = (
    "‐",
    "‑",
    "‒",
    "–",
    "—",
    "―",
)

# 维度一：编号形式
_NUMBER_FORMS = (
    r"\d+",  # 阿拉伯数字: 1, 2, 10, 100
    r"[IVXLC]+",  # 大写罗马数字: I, II, III, IV, V, ..., XXX
    r"[ivxlc]+",  # 小写罗马数字: i, ii, iii, iv, v, ..., xxx
    r"[〇零一二三四五六七八九十百]+",  # 中文数字（不校验语法）
)

# 维度二：包裹/分隔符形式 (左边界, 右边界)
_NUMBER_WRAPPERS = (
    (r"\(", r"\)"),  # 半角圆括号: (1), (I), (一)
    (r"（", r"）"),  # 全角圆括号: （1）, （I）, （一）
    (r"\[", r"\]"),  # 方括号: [1], [I], [一]
    ("〔", "〕"),  # 六角括号: 〔一〕、〔一〇〕
    (r"<", r">"),  # 尖括号: <1>, <I>, <一>
    ("", r"\."),  # 点号: 1., I., 一.
    ("", r"\)"),  # 右括号: 1), I), 一)
    ("", r"、"),  # 中文顿号: 1、, I、, 一、
)

_NUMBERING_PATTERNS = tuple(
    re.compile(f"^{left}{form}{right}")
    for form in _NUMBER_FORMS
    for left, right in _NUMBER_WRAPPERS
)
_QUOTED_NUMBERING_PATTERNS = tuple(
    re.compile(f"^{left}{form}{right}")
    for form in _NUMBER_FORMS
    for left, right in (("「", "」"), ("『", "』"))
)


def has_numbered_prefix(content: list[str | BlockMember | HTMLTag[BlockMember]],
                        *, include_quoted: bool = False) -> bool:
    """Quoted numerals need an established numbered-entry context."""
    text = first(content)
    if not isinstance(text, str):
        return False
    text = text.lstrip()
    patterns = _NUMBERING_PATTERNS
    if include_quoted:
        patterns += _QUOTED_NUMBERING_PATTERNS
    return any((match := pattern.match(text))
               and (len(content) > 1 or bool(text[match.end():].strip()))
               for pattern in patterns)


# too see https://github.com/opendatalab/MinerU/blob/fa1149cd4abf9db5e0f13e4e074cdb568be189f4/mineru/backend/pipeline/para_split.py#L253
def check_mergeable(
    content1: list[str | BlockMember | HTMLTag[BlockMember]],
    content2: list[str | BlockMember | HTMLTag[BlockMember]],
    *, continued_page: bool = False,
) -> bool:
    # 从 content 中提取文本
    text1 = last(content1)
    text2 = first(content2)
    if (continued_page and isinstance(text2, InlineExpression)
            and text2.kind in (ExpressionKind.INLINE_PAREN, ExpressionKind.INLINE_DOLLAR)
            and text2.content.strip()
            and not re.fullmatch(r'\s*[({\[]?\s*[0-9]+\s*[)}\]]?\s*', text2.content)):
        # Inline math can be the next token of an unfinished page-tail clause.
        # Do not infer same-page paragraphs or reinterpret numeric item labels.
        # This local sentinel is never written into source text or output.
        text2 = '\ufffc'

    # 必须都是字符串才能判断
    if not isinstance(text1, str) or not isinstance(text2, str):
        return False

    text1_stripped = text1.rstrip()
    text2_stripped = text2.lstrip()

    if not text1_stripped or not text2_stripped:
        return False

    # 条件1：前一个段落如果以句尾符号结尾，说明是完整段落，不应合并
    if text1_stripped.endswith(_LINE_STOP_FLAGS):
        return False

    unquoted_tail = text1_stripped.rstrip(_QUOTE_CLOSINGS)
    if (
        len(unquoted_tail) < len(text1_stripped)
        and unquoted_tail.endswith(_QUOTED_SENTENCE_ENDINGS)
        and text2_stripped.startswith(_QUOTE_OPENINGS)
    ):
        return False

    # Explicit numbered paragraphs take precedence over comma/hyphen hints.
    if has_numbered_prefix(content2):
        return False

    # 条件2：前一个段落结束的符号明显表明句子未结束，则必须合并
    if text1_stripped.endswith(_LINE_CONTINUE_FLAGS):
        return True

    # 条件3：如果 text1 结尾是拉丁字母 + `-`，text2 开头是拉丁字母，则允许合并（跨段单词拼接）
    if (
        is_latin_letter(text2[0])
        and len(text1) >= 2
        and text1[-1] in LINK_FLAGS
        and is_latin_letter(text1[-2])
    ):
        return True


    return True


_REPORTED_OPENING = re.compile(
    r'^[\u4e00-\u9fff]{1,16}(?:说|說|问|問|喊|答|回答|回了[\u4e00-\u9fff]{0,3}句)[：:,，\s]*[“「『"]')
_OUTER_DEPENDENCY = re.compile(
    r'(?:如果|若是|倘若|假如|假使|要是|即使|即便|虽然|雖然|尽管|儘管|除非|只要|'
    r'每当|每當|当|當|若|因为|因為|既然|无论|無論|一旦|纵使|縱使|只有|直到|哪怕|即令|不管|不论|不論|不但|不仅|不僅)')


def reported_turn_boundary(origin, ending, opening):
    """Do not attach a new reporting turn to a visibly completed utterance.

    A quote can still be inside an unfinished outer conditional. Preserve that
    origin even when the quote spans fragments. This is a narrow textual cue,
    not a general speaker classifier or a rule for postpositive attribution.
    """
    if not all(isinstance(value, str) for value in (origin, ending, opening)):
        return False
    tail = ending.rstrip()
    closed = tail.endswith(('…', '...')) or any(
        tail.endswith(stop + quote) for stop in '.!?。！？' for quote in '\"”」』')
    if not closed:
        return False
    if not _REPORTED_OPENING.match(opening.lstrip()):
        return False
    outside = re.split('[“「『"]', origin, maxsplit=1)[0]
    return bool(outside) and _OUTER_DEPENDENCY.search(outside) is None
