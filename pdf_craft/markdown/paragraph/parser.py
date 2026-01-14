import re
from html import escape, unescape

from .tags import is_protocol_allowed, is_tag_filtered, is_tag_ignored, tag_definition
from .types import HTMLTag, P


def parse_raw_markdown(input: str) -> list[str | P | HTMLTag[P]]:
    """
    Parse raw markdown text containing HTML tags according to CommonMark and GFM specifications.

    This function:
    1. Removes dangerous HTML constructs (comments, processing instructions, declarations, CDATA)
    2. Filters tags and attributes according to GitHub's whitelist
    3. Escapes disallowed tags while exposing their children for recursive checking
    4. Applies GFM tagfilter to specific dangerous tags

    Args:
        input: Raw markdown text potentially containing HTML

    Returns:
        List of strings and HTMLTag objects representing the parsed content
    """
    result = []
    pos = 0

    while pos < len(input):
        # Look for the next "<"
        next_tag_pos = input.find("<", pos)

        if next_tag_pos == -1:
            # No more tags, add remaining text
            if pos < len(input):
                result.append(input[pos:])
            break

        # Add text before the tag
        if next_tag_pos > pos:
            result.append(input[pos:next_tag_pos])

        # Try to parse the HTML construct starting at next_tag_pos
        parsed, new_pos = _parse_html_construct(input, next_tag_pos)

        if parsed is not None:
            # Successfully parsed something
            if isinstance(parsed, list):
                result.extend(parsed)
            elif parsed:  # Skip empty strings
                result.append(parsed)
            pos = new_pos
        else:
            # Not a valid HTML construct, treat as literal text
            result.append("<")
            pos = next_tag_pos + 1

    return result


def _parse_html_construct(
    input: str, pos: int
) -> tuple[str | HTMLTag | list[str | HTMLTag] | None, int]:
    """
    Try to parse an HTML construct starting at position pos.

    Returns:
        Tuple of (parsed_result, new_position)
        - parsed_result can be None (no match), str, HTMLTag, or list
        - new_position is the position after the parsed construct
    """
    if not input[pos:].startswith("<"):
        return None, pos

    # Try to parse HTML comment
    if input[pos:].startswith("<!--"):
        end_pos = input.find("-->", pos + 4)
        if end_pos != -1:
            # Remove comment (GitHub removes these for security)
            return "", end_pos + 3
        return None, pos

    # Try to parse processing instruction
    if input[pos:].startswith("<?"):
        end_pos = input.find("?>", pos + 2)
        if end_pos != -1:
            # Remove processing instruction
            return "", end_pos + 2
        return None, pos

    # Try to parse CDATA section
    if input[pos:].startswith("<![CDATA["):
        end_pos = input.find("]]>", pos + 9)
        if end_pos != -1:
            # Remove CDATA section
            return "", end_pos + 3
        return None, pos

    # Try to parse declaration (<!DOCTYPE, etc.)
    if input[pos:].startswith("<!"):
        # Declaration must be followed by an ASCII letter
        if len(input) > pos + 2 and input[pos + 2].isalpha():
            end_pos = input.find(">", pos + 2)
            if end_pos != -1:
                # Remove declaration
                return "", end_pos + 1
        return None, pos

    # Try to parse a regular tag (opening, closing, or self-closing)
    return _parse_tag(input, pos)


def _parse_tag(
    input: str, pos: int
) -> tuple[str | HTMLTag[P] | list[str | P | HTMLTag[P]] | None, int]:
    """
    Parse an HTML tag (opening, closing, or self-closing).

    According to CommonMark spec:
    - Opening tag: <tagname attribute="value">
    - Closing tag: </tagname>
    - Self-closing tag: <tagname />
    """
    if not input[pos:].startswith("<"):
        return None, pos

    # Check if it's a closing tag
    is_closing = input[pos:].startswith("</")
    start_pos = pos + 2 if is_closing else pos + 1

    # Parse tag name
    # According to CommonMark: tag name consists of ASCII letters, digits, and hyphens
    tag_name_match = re.match(r"([a-zA-Z][a-zA-Z0-9-]*)", input[start_pos:])
    if not tag_name_match:
        return None, pos

    tag_name = tag_name_match.group(1).lower()
    pos_after_name = start_pos + len(tag_name_match.group(1))

    # For closing tags, just look for ">"
    if is_closing:
        # Skip optional whitespace
        ws_match = re.match(r"[ \t\n\r]*>", input[pos_after_name:])
        if ws_match:
            # Closing tags are just returned as escaped text for now
            # The actual tag matching logic would be handled by a higher-level parser
            end_pos = pos_after_name + len(ws_match.group(0))

            # Check if this tag should be filtered by GFM tagfilter
            if is_tag_filtered(tag_name):
                # Replace the leading "<" with "&lt;" to break the tag
                return "&lt;" + input[pos + 1 : end_pos], end_pos

            # Check if tag is in whitelist
            tag_def = tag_definition(tag_name)
            if tag_def:
                # Return the closing tag as-is (as text)
                return input[pos:end_pos], end_pos
            else:
                # Escape the closing tag
                return escape(input[pos:end_pos]), end_pos
        return None, pos

    # For opening tags, parse attributes
    attributes, pos_after_attrs, is_self_closing = _parse_attributes(
        input, pos_after_name
    )

    if pos_after_attrs is None:
        return None, pos

    # Check if this tag should be filtered by GFM tagfilter
    if is_tag_filtered(tag_name):
        # Replace the leading "<" with "&lt;" to break the tag
        return "&lt;" + input[pos + 1 : pos_after_attrs], pos_after_attrs

    # Check if this tag should be ignored (removed but children preserved)
    if is_tag_ignored(tag_name):
        # If self-closing, just remove it entirely
        if is_self_closing:
            return "", pos_after_attrs

        # For opening tags, find content and closing tag
        content, closing_tag_end = _parse_tag_content_and_closing(
            input, pos_after_attrs, tag_name
        )

        if content is not None:
            # Found closing tag, recursively parse content only (tags disappear)
            children = parse_raw_markdown(content) if content else []
            return children, closing_tag_end
        else:
            # No closing tag found, just remove the opening tag
            return "", closing_tag_end

    # Check if tag is in whitelist
    tag_def = tag_definition(tag_name)

    if tag_def:
        # Tag is allowed, filter attributes
        filtered_attrs = _filter_attributes(tag_def, attributes)

        # For self-closing tags, return HTMLTag with no children
        if is_self_closing:
            return HTMLTag(
                definition=tag_def, attributes=filtered_attrs, children=[]
            ), pos_after_attrs

        # For opening tags, find the closing tag and parse content
        content, closing_tag_end = _parse_tag_content_and_closing(
            input, pos_after_attrs, tag_name
        )

        if content is not None:
            # Found closing tag, recursively parse the content
            children: list[str | P | HTMLTag[P]] = (
                parse_raw_markdown(content) if content else []
            )
            return HTMLTag(
                definition=tag_def, attributes=filtered_attrs, children=children
            ), closing_tag_end
        else:
            # No closing tag found, treat as self-closing
            return HTMLTag(
                definition=tag_def, attributes=filtered_attrs, children=[]
            ), closing_tag_end
    else:
        # Tag is not allowed, escape the tag but expose children
        # Get the full tag text
        tag_text = input[pos:pos_after_attrs]

        # If it's a self-closing tag, just escape it
        if is_self_closing:
            return escape(tag_text), pos_after_attrs

        # For opening tags, we need to find the content and closing tag
        # Then escape the opening tag, recursively parse content, and escape closing tag
        content, closing_tag_end = _parse_tag_content_and_closing(
            input, pos_after_attrs, tag_name
        )

        if content is not None:
            # Found closing tag - escape opening and closing tags, but recursively parse content
            closing_tag = f"</{tag_name}>"
            result: list[str | P | HTMLTag[P]] = [escape(tag_text)]
            if content:
                result.extend(parse_raw_markdown(content))
            result.append(escape(closing_tag))
            return result, closing_tag_end
        else:
            # No closing tag found, just escape the opening tag
            return escape(tag_text), closing_tag_end


def _parse_tag_content_and_closing(
    input: str, content_start: int, tag_name: str
) -> tuple[str, int] | tuple[None, int]:
    """
    Find and extract content between opening and closing tags.

    Returns:
        Tuple of (content, closing_tag_end) if closing tag found
        Tuple of (None, content_start) if closing tag not found
    """
    closing_pos = _find_closing_tag(input, content_start, tag_name)

    if closing_pos != -1:
        # Found closing tag
        content = input[content_start:closing_pos]
        # Calculate the end position (skip past the closing tag)
        closing_tag_end = input.find(">", closing_pos)
        if closing_tag_end == -1:
            closing_tag_end = closing_pos + len(f"</{tag_name}>")
        else:
            closing_tag_end += 1
        return content, closing_tag_end
    else:
        # No closing tag found
        return None, content_start


def _parse_attributes(
    input: str, pos: int
) -> tuple[list[tuple[str, str]], int | None, bool]:
    """
    Parse HTML attributes from an opening tag.

    Returns:
        Tuple of (attributes, end_position, is_self_closing)
        - attributes: list of (name, value) tuples
        - end_position: position after the ">" or "/>", or None if parsing failed
        - is_self_closing: True if tag ends with "/>"
    """
    attributes = []
    current_pos = pos

    while current_pos < len(input):
        # Skip whitespace
        ws_match = re.match(r"[ \t\n\r]+", input[current_pos:])
        if ws_match:
            current_pos += len(ws_match.group(0))

        # Check for end of tag
        if input[current_pos:].startswith("/>"):
            return attributes, current_pos + 2, True
        if input[current_pos:].startswith(">"):
            return attributes, current_pos + 1, False

        # Parse attribute name
        # According to CommonMark: attribute name is [a-zA-Z_:][a-zA-Z0-9_.:-]*
        name_match = re.match(r"([a-zA-Z_:][a-zA-Z0-9_.:-]*)", input[current_pos:])
        if not name_match:
            # Invalid attribute, stop parsing
            break

        attr_name = name_match.group(1).lower()
        current_pos += len(name_match.group(1))

        # Skip whitespace after name
        ws_match = re.match(r"[ \t\n\r]*", input[current_pos:])
        if ws_match:
            current_pos += len(ws_match.group(0))

        # Check for "="
        if not input[current_pos:].startswith("="):
            # Attribute without value (boolean attribute)
            attributes.append((attr_name, ""))
            continue

        current_pos += 1

        # Skip whitespace after "="
        ws_match = re.match(r"[ \t\n\r]*", input[current_pos:])
        if ws_match:
            current_pos += len(ws_match.group(0))

        # Parse attribute value
        attr_value = ""
        if input[current_pos:].startswith('"'):
            # Double-quoted value
            current_pos += 1
            end_quote = input.find('"', current_pos)
            if end_quote == -1:
                # Unclosed quote, stop parsing
                break
            attr_value = input[current_pos:end_quote]
            current_pos = end_quote + 1
        elif input[current_pos:].startswith("'"):
            # Single-quoted value
            current_pos += 1
            end_quote = input.find("'", current_pos)
            if end_quote == -1:
                # Unclosed quote, stop parsing
                break
            attr_value = input[current_pos:end_quote]
            current_pos = end_quote + 1
        else:
            # Unquoted value
            # According to CommonMark: unquoted value is [^ \t\n\r"'=<>`]+
            value_match = re.match(r"([^ \t\n\r\"'=<>`]+)", input[current_pos:])
            if value_match:
                attr_value = value_match.group(1)
                current_pos += len(value_match.group(1))

        # Unescape HTML entities in attribute value
        attr_value = unescape(attr_value)

        attributes.append((attr_name, attr_value))

    # If we get here, we didn't find a proper tag ending
    return attributes, None, False


def _filter_attributes(
    tag_def, attributes: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """
    Filter attributes according to the tag's whitelist.

    Also validates URL protocols for href, src, and cite attributes.
    """
    filtered = []

    for name, value in attributes:
        # Check if attribute is allowed for this tag
        if name in tag_def.attributes:
            # For URL attributes, check protocol
            if name in ("href", "src", "cite"):
                if is_protocol_allowed(value):
                    filtered.append((name, value))
                # If protocol is not allowed, skip this attribute
            else:
                filtered.append((name, value))

    return filtered


def _find_closing_tag(input: str, start_pos: int, tag_name: str) -> int:
    """
    Find the matching closing tag for a given tag name.

    Handles nested tags of the same name and case-insensitive matching.
    """
    closing_tag_lower = f"</{tag_name.lower()}"
    opening_tag_lower = f"<{tag_name.lower()}"
    pos = start_pos
    depth = 1

    while pos < len(input):
        # Look for opening or closing tags of the same name (case-insensitive)
        next_open = -1
        next_close = -1

        # Search for next opening tag (case-insensitive)
        search_pos = pos
        while search_pos < len(input):
            found = input[search_pos:].lower().find(opening_tag_lower)
            if found == -1:
                break
            candidate_pos = search_pos + found
            # Verify it's a complete opening tag
            after_tag = candidate_pos + len(opening_tag_lower)
            if after_tag < len(input):
                next_char = input[after_tag]
                if next_char in " \t\n\r>/":
                    next_open = candidate_pos
                    break
            search_pos = candidate_pos + 1

        # Search for next closing tag (case-insensitive)
        search_pos = pos
        while search_pos < len(input):
            found = input[search_pos:].lower().find(closing_tag_lower)
            if found == -1:
                break
            candidate_pos = search_pos + found
            # Verify it's a complete closing tag
            after_tag = candidate_pos + len(closing_tag_lower)
            if after_tag < len(input):
                next_char = input[after_tag]
                if next_char in " \t\n\r>":
                    next_close = candidate_pos
                    break
            elif after_tag == len(input):
                # End of input, assume it needs '>'
                next_close = -1
                break
            search_pos = candidate_pos + 1

        # Process the nearest tag
        if next_close != -1 and (next_open == -1 or next_close < next_open):
            # Found closing tag
            depth -= 1
            if depth == 0:
                return next_close
            pos = next_close + len(closing_tag_lower)
        elif next_open != -1:
            # Found opening tag
            depth += 1
            pos = next_open + len(opening_tag_lower)
        else:
            # No more tags found
            break

    return -1
