"""
GitHub Flavored Markdown (GFM) HTML Tags and Attributes Whitelist

This module defines the HTML tags and attributes allowed by GitHub when rendering
Markdown content. The whitelist is based on GitHub's html-pipeline sanitization
filter and GFM specification.

## Generation Principle

GitHub Flavored Markdown follows the CommonMark specification but adds additional
processing for security and consistency:

1. **Tagfilter Extension (GFM Spec)**: Certain dangerous tags are filtered by
   replacing the leading '<' with '&lt;'. These tags are chosen because they
   change how HTML is interpreted in a way unique to them.

2. **Sanitization Whitelist**: GitHub uses a whitelist-based HTML sanitizer that
   only allows specific tags and attributes. Any tags or attributes not in the
   whitelist are removed during rendering.

3. **Protocol Restrictions**: URLs in href, src, and cite attributes are restricted
   to http://, https://, and relative paths.

4. **Post-processing**: GitHub.com and GitHub Enterprise perform additional
   post-processing and sanitization after GFM is converted to HTML.

## Reference URLs

- GFM Specification: https://github.github.com/gfm/
- html-pipeline (Ruby): https://github.com/gjtorikian/html-pipeline/blob/main/lib/html_pipeline/sanitization_filter.rb
- Community Documentation: https://gist.github.com/seanh/13a93686bf4c2cb16e658b3cf96807f2
- GitHub Markup Issue: https://github.com/github/markup/issues/245
- CommonMark Spec (HTML Blocks): https://spec.commonmark.org/0.30/#html-blocks
- CommonMark Spec (Raw HTML): https://spec.commonmark.org/0.30/#raw-html

## Notes

- The whitelist is not officially documented by GitHub in a single authoritative
  source. This list is compiled from the html-pipeline library source code and
  community testing.
- GitHub may update the whitelist without notice. The actual whitelist used in
  production may differ from open-source implementations.
- For security reasons, most event handlers (onclick, onerror, etc.) and style
  tags are explicitly disallowed.

Last updated: 2025-12-15
"""

from dataclasses import dataclass


@dataclass
class HTMLTagDefinition:
    """Definition of an HTML tag with its allowed attributes and type."""

    name: str
    attributes: frozenset[str]
    is_block: bool


# ============================================================================
# Filtered Tags (GFM Tagfilter Extension)
# ============================================================================

# These tags are filtered by GFM's tagfilter extension. The leading '<' is
# replaced with '&lt;' to prevent them from being rendered as HTML.
# Reference: https://github.github.com/gfm/ (Section 6.11: Disallowed Raw HTML)

_FILTERED_TAGS = frozenset(
    [
        "title",  # Changes document title
        "textarea",  # Form input that interprets content differently
        "style",  # Can inject CSS that affects the entire page
        "xmp",  # Deprecated tag that displays content as preformatted text
        "iframe",  # Can embed external content (security risk)
        "noembed",  # Fallback for embed elements
        "noframes",  # Fallback for frames
        "script",  # Can execute JavaScript (XSS risk)
        "plaintext",  # Deprecated tag that treats rest of page as plain text
    ]
)


# ============================================================================
# Ignored Tags
# ============================================================================

# These tags are completely removed from the output, but their children content
# is preserved and recursively processed. The tags themselves disappear without
# being escaped.

_IGNORE_TAGS = frozenset(
    [
        "left",
        "center",
        "right",
    ]
)


# ============================================================================
# Universal Attributes
# ============================================================================

# These attributes are allowed on most HTML elements in the whitelist.
# Based on: https://github.com/gjtorikian/html-pipeline/blob/main/lib/html_pipeline/sanitization_filter.rb

UNIVERSAL_ATTRIBUTES = frozenset(
    [
        # Standard HTML attributes
        "abbr",
        "accept",
        "accept-charset",
        "accesskey",
        "action",
        "align",
        "alt",
        "axis",
        "border",
        "cellpadding",
        "cellspacing",
        "char",
        "charoff",
        "charset",
        "checked",
        "clear",
        "cols",
        "colspan",
        "color",
        "compact",
        "coords",
        "datetime",
        "dir",
        "disabled",
        "enctype",
        "for",
        "frame",
        "headers",
        "height",
        "hreflang",
        "hspace",
        "id",
        "ismap",
        "label",
        "lang",
        "longdesc",
        "maxlength",
        "media",
        "method",
        "multiple",
        "name",
        "nohref",
        "noshade",
        "nowrap",
        "open",
        "prompt",
        "readonly",
        "rel",
        "rev",
        "rows",
        "rowspan",
        "rules",
        "scope",
        "selected",
        "shape",
        "size",
        "span",
        "start",
        "summary",
        "tabindex",
        "target",
        "title",
        "type",
        "usemap",
        "valign",
        "value",
        "vspace",
        "width",
        # ARIA attributes for accessibility
        "aria-describedby",
        "aria-hidden",
        "aria-label",
        "aria-labelledby",
        "role",
        # Microdata attributes
        "itemprop",
        "itemscope",
        "itemtype",
    ]
)


# ============================================================================
# Element-Specific Attributes
# ============================================================================

# Additional attributes allowed for specific elements only

ELEMENT_SPECIFIC_ATTRIBUTES = {
    "a": frozenset(["href", "hreflang"]),
    "img": frozenset(["src", "longdesc", "loading", "alt"]),
    "source": frozenset(["src", "srcset", "type", "media"]),
    "picture": frozenset([]),
    "div": frozenset(["itemscope", "itemtype"]),
    "blockquote": frozenset(["cite"]),
    "del": frozenset(["cite", "datetime"]),
    "ins": frozenset(["cite", "datetime"]),
    "q": frozenset(["cite"]),
    "time": frozenset(["datetime"]),
    "video": frozenset(["src", "poster", "controls", "width", "height"]),
}


# ============================================================================
# Allowed Protocol Schemes
# ============================================================================

# URL schemes allowed in href, src, and cite attributes
ALLOWED_PROTOCOLS = frozenset(
    [
        "http",
        "https",
        "mailto",
        # Relative URLs are also allowed (no protocol)
    ]
)


# ============================================================================
# HTML Tag Definitions
# ============================================================================


# Structural elements
HTML_DIV = HTMLTagDefinition(
    name="div",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["div"],
    is_block=True,
)

HTML_P = HTMLTagDefinition(
    name="p",
    attributes=UNIVERSAL_ATTRIBUTES,
    is_block=True,
)

HTML_BLOCKQUOTE = HTMLTagDefinition(
    name="blockquote",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["blockquote"],
    is_block=True,
)

HTML_DETAILS = HTMLTagDefinition(
    name="details",
    attributes=UNIVERSAL_ATTRIBUTES | {"open"},
    is_block=True,
)

HTML_SUMMARY = HTMLTagDefinition(
    name="summary",
    attributes=UNIVERSAL_ATTRIBUTES,
    is_block=True,
)

HTML_FIGURE = HTMLTagDefinition(
    name="figure",
    attributes=UNIVERSAL_ATTRIBUTES,
    is_block=True,
)

HTML_FIGCAPTION = HTMLTagDefinition(
    name="figcaption",
    attributes=UNIVERSAL_ATTRIBUTES,
    is_block=True,
)


# Heading elements
HTML_H1 = HTMLTagDefinition(name="h1", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_H2 = HTMLTagDefinition(name="h2", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_H3 = HTMLTagDefinition(name="h3", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_H4 = HTMLTagDefinition(name="h4", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_H5 = HTMLTagDefinition(name="h5", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_H6 = HTMLTagDefinition(name="h6", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)


# Text formatting elements (inline)
HTML_B = HTMLTagDefinition(name="b", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_I = HTMLTagDefinition(name="i", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_STRONG = HTMLTagDefinition(
    name="strong", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_EM = HTMLTagDefinition(name="em", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_SMALL = HTMLTagDefinition(
    name="small", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_MARK = HTMLTagDefinition(
    name="mark", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_S = HTMLTagDefinition(name="s", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_STRIKE = HTMLTagDefinition(
    name="strike", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_ABBR = HTMLTagDefinition(
    name="abbr", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_CITE = HTMLTagDefinition(
    name="cite", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_DFN = HTMLTagDefinition(
    name="dfn", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_KBD = HTMLTagDefinition(
    name="kbd", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_SAMP = HTMLTagDefinition(
    name="samp", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_VAR = HTMLTagDefinition(
    name="var", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_CODE = HTMLTagDefinition(
    name="code", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_PRE = HTMLTagDefinition(name="pre", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_TT = HTMLTagDefinition(name="tt", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_Q = HTMLTagDefinition(
    name="q",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["q"],
    is_block=False,
)
HTML_BDO = HTMLTagDefinition(
    name="bdo", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_INS = HTMLTagDefinition(
    name="ins",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["ins"],
    is_block=False,
)
HTML_DEL = HTMLTagDefinition(
    name="del",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["del"],
    is_block=False,
)
HTML_SUP = HTMLTagDefinition(
    name="sup", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_SUB = HTMLTagDefinition(
    name="sub", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_SPAN = HTMLTagDefinition(
    name="span", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)


# List elements
HTML_OL = HTMLTagDefinition(
    name="ol",
    attributes=UNIVERSAL_ATTRIBUTES | {"start", "reversed", "type"},
    is_block=True,
)
HTML_UL = HTMLTagDefinition(name="ul", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_LI = HTMLTagDefinition(
    name="li",
    attributes=UNIVERSAL_ATTRIBUTES | {"value"},
    is_block=True,
)
HTML_DL = HTMLTagDefinition(name="dl", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_DT = HTMLTagDefinition(name="dt", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_DD = HTMLTagDefinition(name="dd", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)


# Table elements
HTML_TABLE = HTMLTagDefinition(
    name="table",
    attributes=UNIVERSAL_ATTRIBUTES | {"summary"},
    is_block=True,
)
HTML_THEAD = HTMLTagDefinition(
    name="thead", attributes=UNIVERSAL_ATTRIBUTES, is_block=True
)
HTML_TBODY = HTMLTagDefinition(
    name="tbody", attributes=UNIVERSAL_ATTRIBUTES, is_block=True
)
HTML_TFOOT = HTMLTagDefinition(
    name="tfoot", attributes=UNIVERSAL_ATTRIBUTES, is_block=True
)
HTML_TR = HTMLTagDefinition(name="tr", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_TD = HTMLTagDefinition(
    name="td",
    attributes=UNIVERSAL_ATTRIBUTES | {"colspan", "rowspan", "headers"},
    is_block=True,
)
HTML_TH = HTMLTagDefinition(
    name="th",
    attributes=UNIVERSAL_ATTRIBUTES | {"colspan", "rowspan", "headers", "scope"},
    is_block=True,
)
HTML_CAPTION = HTMLTagDefinition(
    name="caption", attributes=UNIVERSAL_ATTRIBUTES, is_block=True
)


# Media elements
HTML_IMG = HTMLTagDefinition(
    name="img",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["img"],
    is_block=False,
)
HTML_PICTURE = HTMLTagDefinition(
    name="picture",
    attributes=UNIVERSAL_ATTRIBUTES,
    is_block=False,
)
HTML_SOURCE = HTMLTagDefinition(
    name="source",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["source"],
    is_block=False,
)
HTML_VIDEO = HTMLTagDefinition(
    name="video",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["video"],
    is_block=True,
)


# Link and semantic elements
HTML_A = HTMLTagDefinition(
    name="a",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["a"],
    is_block=False,
)
HTML_BR = HTMLTagDefinition(name="br", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_HR = HTMLTagDefinition(name="hr", attributes=UNIVERSAL_ATTRIBUTES, is_block=True)
HTML_TIME = HTMLTagDefinition(
    name="time",
    attributes=UNIVERSAL_ATTRIBUTES | ELEMENT_SPECIFIC_ATTRIBUTES["time"],
    is_block=False,
)
HTML_WBR = HTMLTagDefinition(
    name="wbr", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)


# Ruby annotation elements (for East Asian typography)
HTML_RUBY = HTMLTagDefinition(
    name="ruby", attributes=UNIVERSAL_ATTRIBUTES, is_block=False
)
HTML_RT = HTMLTagDefinition(name="rt", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)
HTML_RP = HTMLTagDefinition(name="rp", attributes=UNIVERSAL_ATTRIBUTES, is_block=False)


# ============================================================================
# Collections
# ============================================================================

# Map of tag names to their definitions
# This is the single source of truth for allowed HTML tags
_TAG_DEFINITIONS = {
    "div": HTML_DIV,
    "p": HTML_P,
    "blockquote": HTML_BLOCKQUOTE,
    "details": HTML_DETAILS,
    "summary": HTML_SUMMARY,
    "figure": HTML_FIGURE,
    "figcaption": HTML_FIGCAPTION,
    "h1": HTML_H1,
    "h2": HTML_H2,
    "h3": HTML_H3,
    "h4": HTML_H4,
    "h5": HTML_H5,
    "h6": HTML_H6,
    "b": HTML_B,
    "i": HTML_I,
    "strong": HTML_STRONG,
    "em": HTML_EM,
    "small": HTML_SMALL,
    "mark": HTML_MARK,
    "s": HTML_S,
    "strike": HTML_STRIKE,
    "abbr": HTML_ABBR,
    "cite": HTML_CITE,
    "dfn": HTML_DFN,
    "kbd": HTML_KBD,
    "samp": HTML_SAMP,
    "var": HTML_VAR,
    "code": HTML_CODE,
    "pre": HTML_PRE,
    "tt": HTML_TT,
    "q": HTML_Q,
    "bdo": HTML_BDO,
    "ins": HTML_INS,
    "del": HTML_DEL,
    "sup": HTML_SUP,
    "sub": HTML_SUB,
    "span": HTML_SPAN,
    "ol": HTML_OL,
    "ul": HTML_UL,
    "li": HTML_LI,
    "dl": HTML_DL,
    "dt": HTML_DT,
    "dd": HTML_DD,
    "table": HTML_TABLE,
    "thead": HTML_THEAD,
    "tbody": HTML_TBODY,
    "tfoot": HTML_TFOOT,
    "tr": HTML_TR,
    "td": HTML_TD,
    "th": HTML_TH,
    "caption": HTML_CAPTION,
    "img": HTML_IMG,
    "picture": HTML_PICTURE,
    "source": HTML_SOURCE,
    "video": HTML_VIDEO,
    "a": HTML_A,
    "br": HTML_BR,
    "hr": HTML_HR,
    "time": HTML_TIME,
    "wbr": HTML_WBR,
    "ruby": HTML_RUBY,
    "rt": HTML_RT,
    "rp": HTML_RP,
}

# All allowed HTML tags (tag names only)
# Derived from TAG_DEFINITIONS to avoid duplication
ALLOWED_TAGS = frozenset(_TAG_DEFINITIONS.keys())


# ============================================================================
# Helper Functions
# ============================================================================


def tag_definition(tag_name: str) -> HTMLTagDefinition | None:
    return _TAG_DEFINITIONS.get(tag_name.lower(), None)


def is_tag_filtered(tag_name: str) -> bool:
    return tag_name.lower() in _FILTERED_TAGS


def is_tag_ignored(tag_name: str) -> bool:
    return tag_name.lower() in _IGNORE_TAGS


def is_protocol_allowed(url: str) -> bool:
    if not url:
        return True

    # Relative URLs are allowed
    if url.startswith("/") or url.startswith("./") or url.startswith("../"):
        return True

    # Check against allowed protocols
    url_lower = url.lower()
    for protocol in ALLOWED_PROTOCOLS:
        if url_lower.startswith(f"{protocol}:"):
            return True

    return False
