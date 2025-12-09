from dataclasses import dataclass
from enum import auto, Enum
from typing import Generator


class ParsedItemKind(Enum):
    TEXT = auto()
    INLINE_DOLLAR = auto()  # $ ... $
    DISPLAY_DOUBLE_DOLLAR = auto()  # $$ ... $$
    INLINE_PAREN = auto()  # \( ... \)
    DISPLAY_BRACKET = auto()  # \[ ... \]


@dataclass
class ParsedItem:
    kind: ParsedItemKind
    content: str

    def to_latex_string(self) -> str:
        """
        Convert the parsed item to a LaTeX string with delimiters.

        For TEXT items, returns the content as-is.
        For formula items, returns the content with appropriate delimiters.

        Returns:
            str: The LaTeX string with delimiters
        """
        if self.kind == ParsedItemKind.TEXT:
            return self.content
        elif self.kind == ParsedItemKind.INLINE_DOLLAR:
            return "$" + self.content + "$"
        elif self.kind == ParsedItemKind.DISPLAY_DOUBLE_DOLLAR:
            return "$$" + self.content + "$$"
        elif self.kind == ParsedItemKind.INLINE_PAREN:
            return "\\(" + self.content + "\\)"
        elif self.kind == ParsedItemKind.DISPLAY_BRACKET:
            return "\\[" + self.content + "\\]"
        else:
            # Should never reach here
            return self.content


def parse_latex_expressions(text: str) -> Generator[ParsedItem, None, None]:
    """
    Parse Markdown LaTeX formulas from a string, expanding to plain text and InlineExpression.

    Supported delimiters:
    - Inline formulas: $ ... $, \( ... \)
    - Display formulas: $$ ... $$, \[ ... \]

    Edge cases handled:
    - Escaped characters: \$ and \\( are treated as literals
    - Delimiters inside formulas: correctly match paired delimiters
    - Spaces and newlines: inline formulas cannot contain newlines, display formulas can
    - No nesting or crossing allowed

    Args:
        text: The string to parse

    Yields:
        str: Plain text fragments
        InlineExpression: LaTeX formula expressions
    """
    if not text:
        return

    i = 0
    n = len(text)
    buffer = []

    while i < n:
        # Check for backslash
        if text[i] == "\\" and i + 1 < n:
            next_char = text[i + 1]

            # Count consecutive backslashes
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
            if i + 1 < n and text[i:i+2] == "\\[":
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
                            yield ParsedItem(kind=ParsedItemKind.TEXT, content="".join(buffer))
                            buffer = []
                        yield ParsedItem(kind=ParsedItemKind.DISPLAY_BRACKET, content=latex_content)
                        i = end_pos
                        continue

            # Try to match \( ... \) (inline formula)
            if i + 1 < n and text[i:i+2] == "\\(":
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
                            yield ParsedItem(kind=ParsedItemKind.TEXT, content="".join(buffer))
                            buffer = []
                        yield ParsedItem(kind=ParsedItemKind.INLINE_PAREN, content=latex_content)
                        i = end_pos
                        continue

        # Check for $$ ... $$ (display formula, higher priority than single $)
        if i + 1 < n and text[i:i+2] == "$$":
            if not _is_escaped(text, i):
                result = _find_latex_end(text, i + 2, "$$", allow_newline=True)
                if result is not None:
                    end_pos, latex_content = result
                    if buffer:
                        yield ParsedItem(kind=ParsedItemKind.TEXT, content="".join(buffer))
                        buffer = []
                    yield ParsedItem(kind=ParsedItemKind.DISPLAY_DOUBLE_DOLLAR, content=latex_content)
                    i = end_pos
                    continue

        # Check for $ ... $ (inline formula)
        if text[i] == "$":
            if not _is_escaped(text, i):
                result = _find_latex_end(text, i + 1, "$", allow_newline=False)
                if result is not None:
                    end_pos, latex_content = result
                    if buffer:
                        yield ParsedItem(kind=ParsedItemKind.TEXT, content="".join(buffer))
                        buffer = []
                    yield ParsedItem(kind=ParsedItemKind.INLINE_DOLLAR, content=latex_content)
                    i = end_pos
                    continue

        # Regular character
        buffer.append(text[i])
        i += 1

    # Output remaining text
    if buffer:
        yield ParsedItem(kind=ParsedItemKind.TEXT, content="".join(buffer))


def _is_escaped(text: str, pos: int) -> bool:
    """
    Check if the character at position pos is escaped.
    Escaped condition: preceded by an odd number of consecutive backslashes.

    Args:
        text: The text string
        pos: The position to check

    Returns:
        bool: True if escaped, False otherwise
    """
    backslash_count = 0
    i = pos - 1
    while i >= 0 and text[i] == "\\":
        backslash_count += 1
        i -= 1
    return backslash_count % 2 == 1


def _find_latex_end(
    text: str,
    start: int,
    end_delimiter: str,
    allow_newline: bool
) -> tuple[int, str] | None:
    """
    Find the end delimiter of a LaTeX formula starting from position start.

    Args:
        text: The text string
        start: The position to start searching
        end_delimiter: The end delimiter (e.g., "$", "$$", "\)", "\]")
        allow_newline: Whether to allow newlines inside the formula

    Returns:
        If found, return (end position, LaTeX content); otherwise return None
    """
    n = len(text)
    i = start
    delimiter_len = len(end_delimiter)

    while i < n:
        # Check for newline
        if not allow_newline and text[i] == "\n":
            return None

        # Check if end delimiter matches
        if i + delimiter_len <= n and text[i:i+delimiter_len] == end_delimiter:
            if not _is_escaped(text, i):
                latex_content = text[start:i]
                return (i + delimiter_len, latex_content)

        i += 1

    return None
