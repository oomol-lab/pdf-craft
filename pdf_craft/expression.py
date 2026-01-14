from dataclasses import dataclass
from enum import Enum, auto
from typing import Generator


class ExpressionKind(Enum):
    TEXT = auto()
    INLINE_DOLLAR = auto()  # $ ... $
    DISPLAY_DOUBLE_DOLLAR = auto()  # $$ ... $$
    INLINE_PAREN = auto()  # \( ... \)
    DISPLAY_BRACKET = auto()  # \[ ... \]


@dataclass
class ParsedItem:
    kind: ExpressionKind
    content: str

    def reverse(self) -> str:
        return to_markdown_string(self.kind, self.content)


def encode_expression_kind(kind: ExpressionKind) -> str:
    if kind == ExpressionKind.INLINE_DOLLAR:
        return "$"
    elif kind == ExpressionKind.DISPLAY_DOUBLE_DOLLAR:
        return "$$"
    elif kind == ExpressionKind.INLINE_PAREN:
        return "\\("
    elif kind == ExpressionKind.DISPLAY_BRACKET:
        return "\\["
    else:  # TEXT
        return "text"


def decode_expression_kind(kind_str: str) -> ExpressionKind:
    if kind_str == "$":
        return ExpressionKind.INLINE_DOLLAR
    elif kind_str == "$$":
        return ExpressionKind.DISPLAY_DOUBLE_DOLLAR
    elif kind_str == "\\(":
        return ExpressionKind.INLINE_PAREN
    elif kind_str == "\\[":
        return ExpressionKind.DISPLAY_BRACKET
    elif kind_str == "text":
        return ExpressionKind.TEXT
    else:
        raise ValueError(f"Unknown expression kind: {kind_str}")


def to_markdown_string(kind: ExpressionKind, content: str) -> str:
    # LaTEX 基于状态机，故它本身需要转义 `\$` 和 `\(` 等。content 本身包含转义符是最自然的。
    if kind == ExpressionKind.INLINE_DOLLAR:
        return "$" + content + "$"
    elif kind == ExpressionKind.DISPLAY_DOUBLE_DOLLAR:
        return "$$" + content + "$$"
    elif kind == ExpressionKind.INLINE_PAREN:
        return "\\(" + content + "\\)"
    elif kind == ExpressionKind.DISPLAY_BRACKET:
        return "\\[" + content + "\\]"
    else:
        # 反而是 Markdown 文档本身，需要区分 `$` 和 `(` 不是 LaTEX 语法时，才需要转义
        content = content.replace("\\", "\\\\")
        content = content.replace("$", "\\$")
        return content


def parse_latex_expressions(text: str) -> Generator[ParsedItem, None, None]:
    if not text:
        return

    i = 0
    n = len(text)
    buffer = []

    while i < n:
        # Check for backslash
        if text[i] == "\\" and i + 1 < n:
            backslash_count = 0
            j = i
            while j < n and text[j] == "\\":
                backslash_count += 1
                j += 1

            # Handle escaped dollar sign: \$
            # Note: \( and \[ are LaTeX delimiters, not escape sequences
            if backslash_count % 2 == 1 and j < n and text[j] in "$":
                # Odd number of backslashes before $: escaped dollar sign
                # Output pairs of backslashes and the escaped character
                buffer.append("\\" * (backslash_count // 2))
                buffer.append(text[j])
                i = j + 1
                continue

            # Handle pairs of backslashes: \\ -> \
            if backslash_count >= 2:
                # Output pairs of backslashes
                pairs = backslash_count // 2
                buffer.append("\\" * pairs)
                i += pairs * 2
                # If there's an odd backslash left, continue to process it
                if backslash_count % 2 == 1:
                    # Don't increment i, let the next iteration handle the remaining backslash
                    pass
                continue

            # Try to match \[ ... \] (display formula)
            if i + 1 < n and text[i : i + 2] == "\\[":
                # Check backslashes before \[
                backslash_count = 0
                j = i - 1
                while j >= 0 and text[j] == "\\":
                    backslash_count += 1
                    j -= 1

                # If even number of backslashes (including 0), it's a delimiter
                if backslash_count % 2 == 0:
                    result = _find_latex_end(text, i + 2, "\\]", allow_newline=True)
                    if result is not None:
                        end_pos, latex_content = result
                        if buffer:
                            yield ParsedItem(
                                kind=ExpressionKind.TEXT, content="".join(buffer)
                            )
                            buffer = []
                        yield ParsedItem(
                            kind=ExpressionKind.DISPLAY_BRACKET, content=latex_content
                        )
                        i = end_pos
                        continue

            # Try to match \( ... \) (inline formula)
            if i + 1 < n and text[i : i + 2] == "\\(":
                backslash_count = 0
                j = i - 1
                while j >= 0 and text[j] == "\\":
                    backslash_count += 1
                    j -= 1

                if backslash_count % 2 == 0:
                    result = _find_latex_end(text, i + 2, "\\)", allow_newline=False)
                    if result is not None:
                        end_pos, latex_content = result
                        if buffer:
                            yield ParsedItem(
                                kind=ExpressionKind.TEXT, content="".join(buffer)
                            )
                            buffer = []
                        yield ParsedItem(
                            kind=ExpressionKind.INLINE_PAREN, content=latex_content
                        )
                        i = end_pos
                        continue

        # Check for $$ ... $$ (display formula, higher priority than single $)
        if i + 1 < n and text[i : i + 2] == "$$":
            if not _is_escaped(text, i):
                result = _find_latex_end(text, i + 2, "$$", allow_newline=True)
                if result is not None:
                    end_pos, latex_content = result
                    if buffer:
                        yield ParsedItem(
                            kind=ExpressionKind.TEXT, content="".join(buffer)
                        )
                        buffer = []
                    yield ParsedItem(
                        kind=ExpressionKind.DISPLAY_DOUBLE_DOLLAR, content=latex_content
                    )
                    i = end_pos
                    continue

        # Check for $ ... $ (inline formula)
        if text[i] == "$":
            if not _is_escaped(text, i):
                result = _find_latex_end(text, i + 1, "$", allow_newline=False)
                if result is not None:
                    end_pos, latex_content = result
                    if buffer:
                        yield ParsedItem(
                            kind=ExpressionKind.TEXT, content="".join(buffer)
                        )
                        buffer = []
                    yield ParsedItem(
                        kind=ExpressionKind.INLINE_DOLLAR, content=latex_content
                    )
                    i = end_pos
                    continue

        buffer.append(text[i])
        i += 1

    if buffer:
        yield ParsedItem(kind=ExpressionKind.TEXT, content="".join(buffer))


def _is_escaped(text: str, pos: int) -> bool:
    backslash_count = 0
    i = pos - 1
    while i >= 0 and text[i] == "\\":
        backslash_count += 1
        i -= 1
    return backslash_count % 2 == 1


def _find_latex_end(
    text: str, start: int, end_delimiter: str, allow_newline: bool
) -> tuple[int, str] | None:
    n = len(text)
    i = start
    delimiter_len = len(end_delimiter)

    while i < n:
        if not allow_newline and text[i] == "\n":
            return None
        if i + delimiter_len <= n and text[i : i + delimiter_len] == end_delimiter:
            if not _is_escaped(text, i):
                latex_content = text[start:i]
                return (i + delimiter_len, latex_content)
        i += 1
    return None
