from typing import Protocol
from xml.etree.ElementTree import Element

from pdf_craft.sequence.chapter import Chapter, decode, encode
from .xml_translator.xml_translator import SubmitKind, TranslationTask


class XMLTaskTranslator(Protocol):
    """The public XMLTranslator subset required for a Chapter task."""
    def translate_element(self, task: TranslationTask[Chapter], **kwargs) -> tuple[Element, Chapter]: ...


class ChapterXMLTransformer:
    """Adapt a format-neutral XML translator to the Chapter transformer protocol."""
    def __init__(self, translator: XMLTaskTranslator) -> None:
        self._translator = translator

    def transform(self, chapter: Chapter) -> Chapter:
        translated, _ = self._translator.translate_element(
            TranslationTask(element=encode(chapter), action=SubmitKind.REPLACE, payload=chapter)
        )
        return decode(translated)
