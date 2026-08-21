from typing import Protocol
from xml.etree.ElementTree import Element

from pdf_craft.sequence.chapter import Chapter, decode, encode
from .xml_translator.xml_translator import SubmitKind, TranslationTask


class XMLTaskTranslator(Protocol):
    """The public XMLTranslator subset required for a Chapter task."""
    def translate_element(self, task: TranslationTask[Chapter], **kwargs) -> tuple[Element, Chapter]: ...


class ChapterXMLTransformer:
    """Adapt a format-neutral XML translator to the Chapter transformer protocol."""
    def __init__(self, translator: XMLTaskTranslator, mode: SubmitKind = SubmitKind.REPLACE) -> None:
        self._translator = translator
        self._mode = mode

    @property
    def mode(self) -> SubmitKind:
        return self._mode

    def with_mode(self, mode: SubmitKind) -> "ChapterXMLTransformer":
        """Return a transformer using the requested XML submission mode."""
        return ChapterXMLTransformer(self._translator, mode)

    def transform(self, chapter: Chapter) -> Chapter:
        translated, _ = self._translator.translate_element(
            TranslationTask(element=encode(chapter), action=self._mode, payload=chapter)
        )
        return decode(translated)
