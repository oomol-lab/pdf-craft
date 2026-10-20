"""Public EPUB translation notifications, independent of internal XML models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EpubFragmentContent:
    """One accepted atomic fragment, located in the original input EPUB."""

    epubcfi: str
    original: str
    translated: str


# Compatibility alias: both names denote the same value type.
EpubTranslation = EpubFragmentContent
