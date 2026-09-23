import asyncio
from collections.abc import Callable
from typing import Protocol, cast
from xml.etree.ElementTree import Element

from pdf_craft.extractor.chapter.chapter import (
    Chapter, DisplayFormula, InlineExpression, SourceTextFragment, TextFlowItem,
    decode, encode,
    search_references_in_chapter,
)
from pdf_craft.markdown.paragraph import flatten
from pdf_craft.runtime import TRANSLATION_DOMAIN, callback_bridge
from pdf_craft.transformer.xml_translator.segment import search_text_segments
from pdf_craft.transformer.xml_translator.segment import ImmutableBlockElement, InlineSegment
from pdf_craft.transformer.xml_translator.xml import clone_element
from pdf_craft.transformer.xml_translator.xml.const import ID_KEY
from pdf_craft.transformer.xml_translator.utils import normalize_whitespace
from pdf_craft.transformer.events import TranslationEvent, TranslationItemKind
from .chapter_formula_interrupter import ChapterFormulaInterrupter
from .furniture_xml import FurnitureXMLTransformer, XMLTaskTranslator as FurnitureXMLTaskTranslator
from .xml_translator.xml_translator import SubmitKind, TranslationTask


class XMLTaskTranslator(Protocol):
    """The public XMLTranslator subset required for a Chapter task."""
    def translate_element(self, task: TranslationTask[Chapter], **kwargs) -> tuple[Element, Chapter]: ...


class AsyncXMLTaskTranslator(Protocol):

    async def translate_element_async(
        self, task: TranslationTask[Chapter], **kwargs
    ) -> tuple[Element, Chapter]: ...


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

    def _furniture_transformer(self) -> "FurnitureXMLTransformer":
        """Build the private furniture adapter over this XML translation runtime."""
        return FurnitureXMLTransformer(
            cast(FurnitureXMLTaskTranslator, self._translator), self._mode,
        )

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
        if not self.has_translatable_content(chapter):
            return chapter
        anchors = _NarrativeAnchorProjection(element)
        anchors.replace_assets()
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
            immutable_elements_for_inline_segments=anchors.immutable_elements_for_inline_segments,
            source_text_renderer=_render_chapter_source_text,
            canonical_text_validator=_validate_chapter_fill_canonical_text,
        )
        anchors.restore_assets(translated)
        _restore_fragment_owned_inline_expressions(translated)
        return decode(translated)

    async def transform_async(
        self,
        chapter: Chapter,
        *,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        item_id: str | int | None = None,
        completed_characters: int = 0,
        total_characters: int | None = None,
        emit_scope_events: bool = True,
        emit_item_events: bool = True,
    ) -> Chapter:
        if not hasattr(self._translator, "translate_element_async"):
            callback = callback_bridge(
                asyncio.get_running_loop(), on_translation_event,
            )
            return await TRANSLATION_DOMAIN.run(
                self.transform,
                chapter,
                on_translation_event=callback,
                item_id=item_id,
                completed_characters=completed_characters,
                total_characters=total_characters,
                emit_scope_events=emit_scope_events,
                emit_item_events=emit_item_events,
            )
        element = encode(chapter)
        if not self.has_translatable_content(chapter):
            return chapter
        anchors = _NarrativeAnchorProjection(element)
        anchors.replace_assets()
        formula_interrupter = ChapterFormulaInterrupter()
        async_translator = cast(AsyncXMLTaskTranslator, self._translator)
        translated, _ = await async_translator.translate_element_async(
            TranslationTask(
                element=element,
                action=self._mode,
                payload=chapter,
                item_kind=TranslationItemKind.CHAPTER,
                item_id=item_id if item_id is not None else chapter.id,
                character_count=sum(
                    len(segment.text) for segment in search_text_segments(element)
                ),
            ),
            on_translation_event=on_translation_event,
            completed_characters=completed_characters,
            total_characters=total_characters,
            emit_scope_events=emit_scope_events,
            emit_item_events=emit_item_events,
            interrupt_source_text_segments=formula_interrupter.interrupt_source_text_segments,
            interrupt_translated_text_segments=formula_interrupter.interrupt_translated_text_segments,
            interrupt_block_element=formula_interrupter.interrupt_block_element,
            immutable_elements_for_inline_segments=anchors.immutable_elements_for_inline_segments,
            source_text_renderer=_render_chapter_source_text,
            canonical_text_validator=_validate_chapter_fill_canonical_text,
        )
        anchors.restore_assets(translated)
        _restore_fragment_owned_inline_expressions(translated)
        return decode(translated)

    def source_character_count(self, chapter: Chapter) -> int:
        """Count the NarrativeFlow payload, excluding anchored asset text."""
        element = encode(chapter)
        anchors = _NarrativeAnchorProjection(element)
        anchors.replace_assets()
        return sum(len(segment.text) for segment in search_text_segments(element))

    @staticmethod
    def has_translatable_content(chapter: Chapter) -> bool:
        """Return whether narrative text or a display formula needs translation.

        Embedded image/table metadata deliberately does not make a chapter a
        NarrativeFlow translation task. It belongs to the independent
        AnchoredContent stage.
        """
        flows = [chapter.flow_items]
        flows.extend(reference.flow_items for reference in search_references_in_chapter(chapter))
        for flow_items in flows:
            for item in flow_items:
                if isinstance(item, TextFlowItem):
                    if any(
                        _content_has_text(child.content)
                        for child in item.children
                        if isinstance(child, SourceTextFragment)
                    ):
                        return True
                elif isinstance(item, DisplayFormula) and any(
                    _content_has_text(content)
                    for content in (item.asset.title, item.asset.content, item.asset.caption)
                ):
                    return True
        return False


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


def _render_chapter_source_text(inline_segments) -> str:
    """Render PCEX ``<text>`` nodes as contiguous source-language units.

    XMLTranslator intentionally remains format-neutral, so its default view
    separates independent inline segments.  A PCEX fragment is not an author
    paragraph, however: its owner ``<text>`` is.  Joining only segments that
    share that owner gives the translation model continuous NarrativeFlow text
    while the original fragment/anchor XML remains available for strict fill.
    """
    parts: list[str] = []
    previous_owner: Element | None = None
    has_previous = False
    for inline_segment in inline_segments:
        owner = _source_unit_owner(inline_segment)
        if has_previous and owner is not previous_owner:
            parts.append("\n\n")
        parts.extend(segment.text for segment in inline_segment)
        previous_owner = owner
        has_previous = True
    return "".join(parts)


def _source_unit_owner(inline_segment) -> Element:
    """Return a PCEX TextFlowItem wrapper, or a safe independent fallback."""
    for element in inline_segment.head.parent_stack:
        if element.tag == "text":
            return element
    # Display formulas and other non-text XML content remain hard boundaries.
    return inline_segment.parent


def _validate_chapter_fill_canonical_text(
    inline_segments: list[InlineSegment],
    translated_text: str,
    response: Element,
) -> str | None:
    """Reject a PCEX fill response that changes text at fragment boundaries.

    ``translated_text`` is the canonical result of the first LLM request.  A
    narrative ``<text>`` is one TextFlowItem, while its fragments and opaque
    anchors are only the mapping structure required for later PDF geometry.
    The fill model may choose which fragment owns a character, but concatenating
    visible content from all slots of that TextFlowItem must reproduce its
    canonical translation exactly.  This check runs before HillClimbing admits
    a candidate, so a structurally valid ``How / ever`` attempt cannot become
    the immutable baseline for a later repair.

    XMLTranslator invokes this validator once per TextFlowItem, so canonical
    ownership never has to be inferred from a translated blank line.  The XML
    segment layer canonicalizes runs of whitespace to one space; comparison
    uses that same representation while still catching a space independently
    introduced on both sides of a fragment boundary.
    """
    owners = _ordered_source_unit_owners(inline_segments)
    if len(owners) != 1:
        return (
            "PCEX canonical fill validation received multiple TextFlowItems; "
            "each canonical fill request must contain exactly one <text> unit."
        )

    response_by_id = {
        int(child.get(ID_KEY, "")): child
        for child in response
        if child.get(ID_KEY, "").isdigit()
    }
    actual_by_owner: dict[int, list[str]] = {id(owner): [] for owner in owners}
    owner_by_segment = {
        segment.id: _source_unit_owner(segment)
        for segment in inline_segments
        if segment.id is not None
    }
    for segment_id, owner in owner_by_segment.items():
        filled = response_by_id.get(segment_id)
        if filled is None:
            # Structural validation supplies the useful diagnostic first.
            return None
        actual_by_owner[id(owner)].extend(
            text_segment.text for text_segment in search_text_segments(filled)
        )

    owner = owners[0]
    # Display formulas and temporary formula-context nodes are not
    # TextFlowItems. Their dedicated interruption protocol owns fidelity, so
    # this text-only invariant must not reinterpret their token stream.
    if owner.tag != "text" or _text_owner_has_formula(owner):
        return None
    expected = normalize_whitespace(translated_text)
    actual = "".join(actual_by_owner[id(owner)])
    if actual != expected:
        return (
            "Visible text for one PCEX <text> differs from the canonical "
            "translation. Anchors are zero-width and must not add spaces "
            f"or characters. Expected {expected!r}, got {actual!r}."
        )
    return None


def _ordered_source_unit_owners(inline_segments: list[InlineSegment]) -> list[Element]:
    owners: list[Element] = []
    seen: set[int] = set()
    for segment in inline_segments:
        owner = _source_unit_owner(segment)
        if id(owner) in seen:
            continue
        seen.add(id(owner))
        owners.append(owner)
    return owners


def _text_owner_has_formula(owner: Element) -> bool:
    return any(
        element.tag == "inline_expr" and element.get("kind") != "text"
        for element in owner.iter()
    )


_ANCHOR_TAG = "anchor"
_ANCHOR_KEY = "anchor_key"


class _NarrativeAnchorProjection:
    """Temporarily substitute embedded assets with opaque translation anchors.

    XMLTranslator's existing ID/template validation keeps these marker nodes
    structurally stable through its repair loop.  The source asset is restored
    only after that validated round trip, so no image/table field can leak into
    the NarrativeFlow prompt or be overwritten by its result.
    """

    def __init__(self, root: Element) -> None:
        self._root = root
        self._assets: list[tuple[str, Element, Element]] = []
        self._standalone_assets: list[Element] = []

    def replace_assets(self) -> None:
        for parent in self._root.iter():
            for child in list(parent):
                if (
                    parent.tag != "text"
                    or child.tag != "asset"
                    or child.get("ref") not in {"image", "table"}
                ):
                    continue
                key = str(len(self._assets))
                anchor = Element(_ANCHOR_TAG, {_ANCHOR_KEY: key})
                anchor.tail = child.tail
                index = list(parent).index(child)
                parent.remove(child)
                parent.insert(index, anchor)
                self._assets.append((key, clone_element(child), parent))

        # Standalone assets are not paragraph positions.  Keep their wrapper
        # in the flow but detach their textual fields from NarrativeFlow.
        # Unlike a nested asset, no <anchor> is fabricated for them.
        for wrapper in self._root.iter("standalone-asset"):
            asset = wrapper.find("asset")
            if asset is None or asset.get("ref") not in {"image", "table"}:
                continue
            self._standalone_assets.append(clone_element(asset))
            wrapper.remove(asset)
            wrapper.append(Element("asset", asset.attrib))

    def restore_assets(self, translated: Element) -> None:
        expected = [key for key, _, _ in self._assets]
        found: list[tuple[Element, Element, str]] = []
        for parent in translated.iter():
            for child in parent:
                if child.tag == _ANCHOR_TAG:
                    key = child.get(_ANCHOR_KEY)
                    if key is not None:
                        found.append((parent, child, key))

        found_keys = [key for _, _, key in found]
        if found_keys != expected:
            raise ValueError(
                "Narrative translation changed anchored-content structure; "
                "expected immutable anchor order "
                f"{expected}, got {found_keys}"
            )

        for (parent, anchor, _), (_, asset, _) in zip(found, self._assets, strict=True):
            index = list(parent).index(anchor)
            restored = clone_element(asset)
            restored.tail = anchor.tail
            parent.remove(anchor)
            parent.insert(index, restored)

        standalone_wrappers = list(translated.iter("standalone-asset"))
        if len(standalone_wrappers) != len(self._standalone_assets):
            raise ValueError("Narrative translation changed standalone asset structure")
        for wrapper, asset in zip(standalone_wrappers, self._standalone_assets, strict=True):
            placeholder = wrapper.find("asset")
            if placeholder is None:
                raise ValueError("Narrative translation removed standalone asset placeholder")
            index = list(wrapper).index(placeholder)
            wrapper.remove(placeholder)
            wrapper.insert(index, clone_element(asset))

    def immutable_elements_for_inline_segments(
        self, inline_segments: list[InlineSegment],
    ) -> list[ImmutableBlockElement]:
        """Expose only relevant self-closing anchors to XML fill validation.

        The XML stream mapper may split a chapter into token-sized groups.  An
        anchor is therefore included in every group touching its owning text
        flow.  This keeps it opaque while letting the standard repair loop
        reject a missing, renamed, duplicated, or reordered token.
        """
        result: list[ImmutableBlockElement] = []
        for key, anchor, parent in self._assets:
            assert anchor.tag == "asset"  # the stored clone is the source asset
            source_anchor = next(
                (
                    child for child in parent
                    if child.tag == _ANCHOR_TAG and child.get(_ANCHOR_KEY) == key
                ),
                None,
            )
            if source_anchor is None:
                continue
            source_anchor_index = list(parent).index(source_anchor)
            segment_children: list[tuple[int, int]] = []
            for segment_index, segment in enumerate(inline_segments):
                direct_child = _direct_child_of(parent, segment.parent_stack)
                if direct_child is not None:
                    segment_children.append((segment_index, list(parent).index(direct_child)))
            if not segment_children:
                continue
            before = next(
                (segment_index for segment_index, child_index in segment_children
                 if child_index > source_anchor_index),
                len(inline_segments),
            )
            result.append(ImmutableBlockElement(source_anchor, before))
        return result


def _direct_child_of(parent: Element, stack: list[Element]) -> Element | None:
    """Find the immediate member of ``parent`` containing a text segment."""
    for index, element in enumerate(stack):
        if element is parent and index + 1 < len(stack):
            return stack[index + 1]
    return None


def _content_has_text(content) -> bool:
    """Return whether a structured text field has visible source content."""
    return any(
        (isinstance(part, str) and part.strip())
        or (isinstance(part, InlineExpression) and part.content.strip())
        for part in flatten(content)
    )
