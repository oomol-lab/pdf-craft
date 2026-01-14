import re
import unicodedata

from ..language import is_latin_letter

_LINK_FLAGS = frozenset(["‐", "‑", "‒", "–", "—", "―"])

# 全面的标点符号列表，包含多语言支持
# 参考资料：
# - https://en.wikipedia.org/wiki/General_Punctuation
# - https://www.unicode.org/charts/PDF/U2000.pdf
# - https://en.wikipedia.org/wiki/CJK_Symbols_and_Punctuation
# - https://www.unicode.org/charts/PDF/U3000.pdf
# - https://en.wiktionary.org/wiki/Appendix:Unicode/Supplemental_Punctuation
# - https://symbl.cc/en/unicode/blocks/supplemental-punctuation/
# - https://www.compart.com/en/unicode/category/Po
# - https://www.fileformat.info/info/unicode/category/Po/list.htm
# - https://en.wikipedia.org/wiki/Chinese_punctuation
# - https://en.wikipedia.org/wiki/Korean_punctuation
# - https://en.wikipedia.org/wiki/Guillemet
# - https://lingoculture.com/blog/grammar/french-punctuation-marks/
_PUNCTUATIONS = frozenset(
    [
        # 基本 ASCII 标点符号
        "!",
        '"',
        "#",
        "%",
        "&",
        "'",
        "(",
        ")",
        "*",
        ",",
        "-",
        ".",
        "/",
        ":",
        ";",
        "?",
        "@",
        "[",
        "\\",
        "]",
        "^",
        "_",
        "`",
        "{",
        "|",
        "}",
        "~",
        # 拉丁文补充区标点符号
        "¡",
        "§",
        "¶",
        "·",
        "¿",
        # 通用标点符号（U+2000-206F）
        "‐",
        "‑",
        "‒",
        "–",
        "—",
        "―",  # 各种连字符和破折号
        "‖",
        "‗",  # 双竖线和下划线
        "'",
        "'",
        "‚",
        "‛",  # 单引号变体
        """, """,
        "„",
        "‟",  # 双引号变体
        "†",
        "‡",  # 剑标
        "•",
        "‣",  # 项目符号
        "․",
        "‥",
        "…",  # 省略号
        "‧",  # 连字点
        "′",
        "″",
        "‴",
        "‵",
        "‶",
        "‷",  # 角分符号
        "‹",
        "›",  # 单角引号
        "※",  # 参考标记
        "‼",
        "‽",  # 双感叹号、疑问感叹号
        "‾",
        "‿",
        "⁀",
        "⁁",
        "⁂",
        "⁃",
        "⁄",
        "⁅",
        "⁆",
        "⁇",
        "⁈",
        "⁉",
        "⁊",
        "⁋",
        "⁌",
        "⁍",
        "⁎",
        "⁏",
        "⁐",
        "⁑",
        "⁒",
        "⁓",
        "⁔",
        "⁕",
        "⁖",
        "⁗",
        "⁘",
        "⁙",
        "⁚",
        "⁛",
        "⁜",
        "⁝",
        "⁞",
        # 补充标点符号（U+2E00-2E7F）
        "⸀",
        "⸁",
        "⸂",
        "⸃",
        "⸄",
        "⸅",
        "⸆",
        "⸇",
        "⸈",
        "⸉",
        "⸊",
        "⸋",
        "⸌",
        "⸍",
        "⸎",
        "⸏",
        "⸐",
        "⸑",
        "⸒",
        "⸓",
        "⸔",
        "⸕",
        "⸖",
        "⸗",
        "⸘",
        "⸙",
        "⸚",
        "⸛",
        "⸜",
        "⸝",
        "⸞",
        "⸟",
        "⸠",
        "⸡",
        "⸢",
        "⸣",
        "⸤",
        "⸥",
        "⸦",
        "⸧",
        "⸨",
        "⸩",
        "⸪",
        "⸫",
        "⸬",
        "⸭",
        "⸮",
        "ⸯ",
        "⸰",
        "⸱",
        "⸲",
        "⸳",
        "⸴",
        "⸵",
        "⸶",
        "⸷",
        "⸸",
        "⸹",
        "⸺",
        "⸻",
        "⸼",
        "⸽",
        "⸾",
        "⸿",
        "⹀",
        "⹁",
        "⹂",
        "⹃",
        "⹄",
        "⹅",
        "⹆",
        "⹇",
        "⹈",
        "⹉",
        "⹊",
        "⹋",
        "⹌",
        "⹍",
        "⹎",
        "⹏",
        # CJK 标点符号（U+3000-303F）
        "、",
        "。",  # 顿号、句号
        "〈",
        "〉",
        "《",
        "》",  # 书名号
        "「",
        "」",
        "『",
        "』",  # 角括号
        "【",
        "】",  # 方头括号
        "〔",
        "〕",  # 龟甲括号
        "〖",
        "〗",  # 白方头括号
        "〘",
        "〙",
        "〚",
        "〛",  # 其他括号
        "〜",
        "〝",
        "〞",
        "〟",  # 波浪号和引号
        "〰",
        "〽",  # 波浪线和乐谱号
        "・",  # 中点
        # 全角 ASCII 标点符号（U+FF00-FFEF）
        "！",
        "＂",
        "＃",
        "％",
        "＆",
        "＇",
        "（",
        "）",
        "＊",
        "，",
        "．",
        "／",
        "：",
        "；",
        "？",
        "＠",
        "［",
        "＼",
        "］",
        "＾",
        "＿",
        "｀",
        "｛",
        "｜",
        "｝",
        "～",
        "｡",
        "｢",
        "｣",
        "､",
        "･",  # 半角日文标点
        # 法语和德语特定标点符号
        "«",
        "»",  # 法语书名号（guillemets）
        "„",
        "‟",  # 德语引号
        # 西班牙语标点符号
        # "¡", "¿",  # 倒置的感叹号和问号（已在拉丁补充区中）
        # 希腊语标点符号
        "·",  # 希腊文中点（已包含）
        ";",  # 希腊问号（看起来像分号，已包含）
        # 阿拉伯语标点符号
        "؉",
        "؊",
        "،",
        "؍",
        "؎",
        "؏",
        "؛",
        "؞",
        "؟",
        "٪",
        "٫",
        "٬",
        "٭",
        # 希伯来语标点符号
        "֊",
        "־",
        "׀",
        "׃",
        "׆",
        "׳",
        "״",
        # 藏文标点符号
        "།",
        "༎",
        "༏",
        "༐",
        "༑",
        "༒",
        "༔",
        "༴",
        "༶",
        "༸",
        "྅",
        "࿐",
        "࿑",
        "࿒",
        "࿓",
        "࿔",
        "࿙",
        "࿚",
        # 缅甸语标点符号
        "၊",
        "။",
        "၌",
        "၍",
        "၎",
        "၏",
        # 其他亚洲文字标点符号
        "᙮",  # 加拿大音节句号
        "។",
        "៕",
        "៖",
        "៘",
        "៙",
        "៚",  # 高棉语标点
        "᠀",
        "᠁",
        "᠂",
        "᠃",
        "᠄",
        "᠅",
        "᠆",
        "᠇",
        "᠈",
        "᠉",
        "᠊",  # 蒙古语标点
        "჻",  # 格鲁吉亚标点
        "፠",
        "፡",
        "።",
        "፣",
        "፤",
        "፥",
        "፦",
        "፧",
        "፨",  # 埃塞俄比亚标点
        # 其他特殊标点符号
        "‱",  # 千分号
        "‸",  # 插入符号
        "※",  # 参考符号（已包含）
        "⁁",  # 插入点
        "⁓",  # 波浪连字符
    ]
)


def normalize_text(text: str) -> str:
    """
    扫描件中的文字杂乱，此方法尽可能规范化文字，以让相同语义的文字在字符串上也尽可能完全一致
    """
    text = re.sub(r"\s+", " ", text).strip()
    chars = _process_spaces_and_hyphens(text)
    return "".join(_remove_punctuation_and_normalize_latin(chars))


def _process_spaces_and_hyphens(text: str) -> list[str]:
    """
    针对拉丁字母语言相关的处理。检查连字符以拼回单词（连字符用于换行时被截断的单词）。
    然后删除非拉丁语字母语言之间的空格，对于汉语而言，删光字之间的空格不影响阅读。
    """
    chars: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == " ":
            # 规则1：检查是否是 拉丁字母 + 连字符 + 空格 + 拉丁字母
            if (
                len(chars) >= 2
                and chars[-1] in _LINK_FLAGS
                and is_latin_letter(chars[-2])
                and i < len(text) - 1
                and is_latin_letter(text[i + 1])
            ):
                # 删除连字符，跳过空格，直接连接单词
                chars.pop()
                i += 1
                continue

            # 规则2：只保留拉丁字母之间的空格
            keep_space = False
            if len(chars) > 0 and i < len(text) - 1:
                prev_is_latin = is_latin_letter(chars[-1])
                next_is_latin = is_latin_letter(text[i + 1])
                keep_space = prev_is_latin and next_is_latin

            if keep_space:
                chars.append(char)
            i += 1
        else:
            chars.append(char)
            i += 1

    return chars


def _remove_punctuation_and_normalize_latin(chars: list[str]):
    """
    进一步去除所有干扰因素。先删光标点符号，然后将拉丁字全传小写，最后删除重音符号。
    """
    for char in chars:
        if char in _PUNCTUATIONS:
            continue
        if not is_latin_letter(char):
            yield char
            continue
        char = char.lower()
        for d_char in unicodedata.normalize("NFD", char):
            # NFD 拆解以过滤重音符号
            if unicodedata.category(d_char) != "Mn":
                yield d_char
