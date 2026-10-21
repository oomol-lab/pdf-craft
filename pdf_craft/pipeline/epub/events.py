"""Public EPUB translation notifications, independent of internal XML models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EpubFragmentContent:
    """One accepted atomic fragment; epubcfi is None without reliable source location."""

    epubcfi: str | None
    original: str
    translated: str


# Compatibility alias: both names denote the same value type.
EpubTranslation = EpubFragmentContent
