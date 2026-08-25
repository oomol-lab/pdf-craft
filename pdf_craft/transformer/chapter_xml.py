from typing import Protocol
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.chapter import Chapter, decode, encode
from pdf_craft.transformer.xml_translator.segment import search_text_segments
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
        element = encode(chapter)
        # OCR can produce chapters for pages that contain no translatable text.
        # Keep those chapters intact instead of asking XMLTranslator to process
        # an empty stream, which otherwise raises "Translation failed unexpectedly".
        if not any(segment.text.strip() for segment in search_text_segments(element)):
            return chapter
        translated, _ = self._translator.translate_element(
            TranslationTask(element=element, action=self._mode, payload=chapter)
        )
        return decode(translated)
