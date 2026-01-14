def is_latin_letter(char: str) -> bool:
    return char.isalpha() and ord(char) < 0x0370


def is_chinese_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    # 中文字符的 Unicode 范围
    return (
        0x4E00 <= code <= 0x9FFF  # CJK统一汉字
        or 0x3400 <= code <= 0x4DBF  # CJK统一汉字扩展A
        or 0x20000 <= code <= 0x2A6DF  # CJK统一汉字扩展B
        or 0x2A700 <= code <= 0x2B73F  # CJK统一汉字扩展C
        or 0x2B740 <= code <= 0x2B81F  # CJK统一汉字扩展D
        or 0x2B820 <= code <= 0x2CEAF  # CJK统一汉字扩展E
        or 0x3000 <= code <= 0x303F  # CJK符号和标点
        or 0xFF00 <= code <= 0xFFEF
    )  # 全角ASCII、全角标点
