def is_latin_letter(char: str) -> bool:
    return char.isalpha() and ord(char) < 0x0370


def is_han_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2CEAF
    )


def is_chinese_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        is_han_char(char)
        or 0x3000 <= code <= 0x303F  # CJK符号和标点
        or 0xFF00 <= code <= 0xFFEF
    )  # 全角ASCII、全角标点
