"""Public EPUB translation notifications, independent of internal XML models."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EpubTranslation:
    """One accepted atomic fragment, located in the original input EPUB."""

    epubcfi: str
    original: str
    translated: str
