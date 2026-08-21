from xml.etree.ElementTree import Element

from .const import DISPLAY_ATTRIBUTE

# HTML inline-level elements
# Reference: https://developer.mozilla.org/en-US/docs/Web/HTML/Inline_elements
# Reference: https://developer.mozilla.org/en-US/docs/Glossary/Inline-level_content
# Reference: https://developer.mozilla.org/en-US/docs/MathML/Element
_HTML_INLINE_TAGS = frozenset(
    (
        # Inline text semantics
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "br",
        "cite",
        "code",
        "data",
        "dfn",
        "em",
        "i",
        "kbd",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
        # Image and multimedia
        "img",
        "svg",
        "canvas",
        "audio",
        "video",
        "map",
        "area",
        # Form elements
        "input",
        "button",
        "select",
        "textarea",
        "label",
        "output",
        "progress",
        "meter",
        # Embedded content
        "iframe",
        "embed",
        "object",
        # Other inline elements
        "script",
        "del",
        "ins",
        "slot",
        # MathML elements
        # Token elements
        "mi",  # identifier
        "mn",  # number
        "mo",  # operator
        "ms",  # string literal
        "mspace",  # space
        "mtext",  # text
        # General layout
        "menclose",  # enclosed content
        "merror",  # syntax error message
        "mfenced",  # parentheses (deprecated)
        "mfrac",  # fraction
        "mpadded",  # space around content
        "mphantom",  # invisible content
        "mroot",  # radical with index
        "mrow",  # grouped sub-expressions
        "msqrt",  # square root
        "mstyle",  # style change
        # Scripts and limits
        "mmultiscripts",  # prescripts and tensor indices
        "mover",  # overscript
        "mprescripts",  # prescripts separator
        "msub",  # subscript
        "msubsup",  # subscript-superscript pair
        "msup",  # superscript
        "munder",  # underscript
        "munderover",  # underscript-overscript pair
        # Table math
        "mtable",  # table or matrix
        "mtr",  # row in table or matrix
        "mtd",  # cell in table or matrix
        # Semantic annotations
        "annotation",  # data annotation
        "annotation-xml",  # XML annotation
        "semantics",  # semantic annotation container
        # Other
        "maction",  # bind actions to sub-expressions (deprecated)
    )
)


def is_inline_element(element: Element) -> bool:
    tag = element.tag.lower()
    if tag in _HTML_INLINE_TAGS:
        return True
    display = element.get(DISPLAY_ATTRIBUTE, None)
    if display is not None:
        display = display.lower()
        if display == "inline":
            return True
    if tag == "math" and display != "block":
        return True
    return False
