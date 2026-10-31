"""Single-pass XML entity decoding shared by text and attributes."""
import re

_ENTITIES = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}
_ENTITY = re.compile(r"&(?P<name>lt|gt|amp|quot|apos|#[0-9]+|#x[0-9a-fA-F]+);")


def unescape_text(text: str) -> str:
    """Decode XML entities once, after identifying structural tags.

    Unknown or invalid references stay literal. Unlike HTML unescaping, XML
    numeric references do not remap code points using legacy character sets.
    """
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name in _ENTITIES:
            return _ENTITIES[name]
        try:
            code = int(name[2:], 16) if name.startswith("#x") else int(name[1:])
        except ValueError:
            return match.group(0)
        if (code in (9, 10, 13) or 0x20 <= code <= 0xD7FF
                or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF):
            return chr(code)
        return match.group(0)

    return _ENTITY.sub(replace, text)
