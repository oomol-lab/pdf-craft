import re

# Some non-standard EPUB generators use HTML-style tags without self-closing syntax
# We need to convert them to XML-compatible format before parsing
# These are HTML5 void elements that must be self-closing in XHTML
# Note: "meta" is excluded because OPF files have <meta property="...">content</meta>
# which is NOT a void element (different namespace, different rules)
_VOID_TAGS = (
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "param",
    "source",
    "track",
    "wbr",
)


def self_close_void_elements(xml_content: str) -> str:
    """
    Convert void HTML elements to self-closing format for XML parsing.

    This function handles non-standard HTML where void elements are not self-closed.
    Note: "meta" is excluded from processing because EPUB OPF files have
    <meta property="...">content</meta> which is NOT a void element.

    Args:
        xml_content: HTML/XHTML content string

    Returns:
        Content with void elements in self-closing format

    Example:
        <br> → <br />
        <link rel="stylesheet" href="style.css"> → <link rel="stylesheet" href="style.css" />
    """
    for tag in _VOID_TAGS:
        xml_content = _fix_void_element(xml_content, tag)
    return xml_content


def _fix_void_element(content: str, tag_name: str) -> str:
    """
    Fix a specific void element in the content.

    Strategy:
    1. Find <tag ...> (not already self-closed)
    2. Check if there's a matching </tag>
    3. If yes, remove everything between them and make it self-closing
    4. If no, just make the opening tag self-closing
    """
    result = []
    pos = 0

    while pos < len(content):
        tag_start = content.find(f"<{tag_name}", pos)
        if tag_start == -1:
            result.append(content[pos:])
            break

        # Verify it's a complete tag match (not a prefix like <br matching <brain>)
        # The character after tag_name must be >, /, or whitespace
        check_pos = tag_start + len(f"<{tag_name}")
        if check_pos < len(content):
            next_char = content[check_pos]
            if next_char not in (">", "/", " ", "\t", "\n", "\r"):
                result.append(content[pos:check_pos])
                pos = check_pos
                continue

        result.append(content[pos:tag_start])
        tag_end = _find_tag_end(content, tag_start)
        if tag_end == -1:
            result.append(content[tag_start:])
            break

        opening_tag = content[tag_start : tag_end + 1]

        if opening_tag.rstrip().endswith("/>"):
            result.append(opening_tag)
            pos = tag_end + 1
            continue

        if not opening_tag.endswith(">"):
            result.append(opening_tag)
            pos = tag_end + 1
            continue

        closing_tag = f"</{tag_name}>"
        closing_pos = content.find(closing_tag, tag_end + 1)

        if closing_pos != -1:
            attrs_part = opening_tag[len(f"<{tag_name}") : -1].rstrip()
            if attrs_part:
                result.append(f"<{tag_name}{attrs_part} />")
            else:
                result.append(f"<{tag_name} />")
            pos = closing_pos + len(closing_tag)
        else:
            attrs_part = opening_tag[len(f"<{tag_name}") : -1].rstrip()
            if attrs_part:
                result.append(f"<{tag_name}{attrs_part} />")
            else:
                result.append(f"<{tag_name} />")
            pos = tag_end + 1

    return "".join(result)


def _find_tag_end(content: str, start_pos: int) -> int:
    """
    Find the end of an HTML tag (the position of >).

    Handles quotes: ignores > inside quoted attribute values.
    """
    pos = start_pos
    in_quote = None  # None, '"', or "'"

    while pos < len(content):
        char = content[pos]

        if in_quote:
            if char == in_quote:
                if pos > 0 and content[pos - 1] == "\\":
                    pos += 1
                    continue
                else:
                    in_quote = None
        else:
            if char in ('"', "'"):
                in_quote = char
            elif char == ">":
                return pos

        pos += 1

    return -1  # Not found


# For saving: match self-closing tags like <br /> or <br/>
# Capture tag name and everything between tag name and />
_VOID_TAG_CLOSE_PATTERN = re.compile(r"<(" + "|".join(_VOID_TAGS) + r")([^>]*?)\s*/>")


def unclose_void_elements(xml_content: str) -> str:
    """
    Convert void elements from self-closing to unclosed format for HTML compatibility.

    Transforms self-closed void elements like <br /> back to <br> for
    compatibility with HTML parsers that don't support XHTML syntax.
    Used only for text/html media type files.

    Args:
        xml_content: HTML/XHTML content string

    Returns:
        Content with void elements in unclosed format

    Example:
        <meta charset="utf-8" /> → <meta charset="utf-8">
        <br /> → <br>
        <img src="test.png" /> → <img src="test.png">
    """

    def replacer(m: re.Match):
        tag_name = m.group(1)
        attrs = m.group(2).rstrip()  # Remove trailing whitespace
        if attrs:
            return f"<{tag_name}{attrs}>"
        else:
            return f"<{tag_name}>"

    return re.sub(
        pattern=_VOID_TAG_CLOSE_PATTERN,
        repl=replacer,
        string=xml_content,
    )
