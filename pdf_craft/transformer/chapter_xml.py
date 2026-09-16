from collections.abc import Callable
from typing import Protocol
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.chapter import Chapter, decode, encode
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.events import TranslationEvent, TranslationItemKind
from .chapter_formula_interrupter import ChapterFormulaInterrupter
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

    def transform(
        self,
        chapter: Chapter,
        *,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
        item_id: str | int | None = None,
        completed_characters: int = 0,
        total_characters: int | None = None,
        emit_scope_events: bool = True,
        emit_item_events: bool = True,
    ) -> Chapter:
        element = encode(chapter)
        # OCR can produce chapters for pages that contain no translatable text.
        # Keep those chapters intact instead of asking XMLTranslator to process
        # an empty stream, which otherwise raises "Translation failed unexpectedly".
        if not any(segment.text.strip() for segment in search_text_segments(element)):
            return chapter
        formula_interrupter = ChapterFormulaInterrupter()
        translated, _ = self._translator.translate_element(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=chapter,
                item_kind=TranslationItemKind.CHAPTER,
                item_id=item_id if item_id is not None else chapter.id,
                character_count=sum(len(segment.text) for segment in search_text_segments(element)),
            ),
            on_translation_event=on_translation_event,
            completed_characters=completed_characters,
            total_characters=total_characters,
            emit_scope_events=emit_scope_events,
            emit_item_events=emit_item_events,
            interrupt_source_text_segments=formula_interrupter.interrupt_source_text_segments,
            interrupt_translated_text_segments=formula_interrupter.interrupt_translated_text_segments,
            interrupt_block_element=formula_interrupter.interrupt_block_element,
        )
        _restore_fragment_owned_inline_expressions(translated)
        return decode(translated)


def _restore_fragment_owned_inline_expressions(chapter: Element) -> None:
    """Keep translator-restored inline formulas inside their source fragment.

    XMLTranslator may return an interrupted inline token as a sibling of its
    owning ``fragment``.  That is a transport shape, not PCEX v3: a ``text``
    node can contain only fragments and anchored image/table assets.  Restore
    the token before strict chapter decoding, preserving the formula's tail.
    """
    for text in chapter.findall(".//text"):
        owner: Element | None = None
        for child in list(text):
            if child.tag == "fragment":
                owner = child
            elif child.tag == "inline_expr":
                if owner is None:
                    raise ValueError("translator returned inline_expr without a preceding fragment")
                text.remove(child)
                duplicate = any(
                    existing.tag == "inline_expr"
                    and existing.get("kind") == child.get("kind")
                    and (existing.text or "") == (child.text or "")
                    for existing in owner.iter("inline_expr")
                )
                if not duplicate:
                    owner.append(child)
