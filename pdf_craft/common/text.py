"""Text admitted from external documents and providers."""
import re


_UNSAFE_TEXT = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def safe_text(text: str) -> str:
    """Remove code points that cannot travel through the XML-based pipeline.

    Apply when accepting external text, including text stored first as JSON.
    This preserves whitespace and valid Unicode; it does not escape markup,
    repair XML structure, or provide HTML/URL security sanitization.
    """
    return _UNSAFE_TEXT.sub("", text)
