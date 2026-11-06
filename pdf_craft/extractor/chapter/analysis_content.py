"""Lossless projection of structured text and editable reference positions."""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Iterable

from ...markdown.paragraph import HTMLTag, tag_definition
from .chapter import InlineExpression, Reference, SourceTextFragment
from .mark import Mark
from .analysis_types import (
    LayoutOwnership,
    _ReferencePlaceholder,
    _AnalysedInlineExpression,
    _AnalysedHTMLTag,
    _AnalysedContentPart,
    _AnalysedContent,
    _AnalysedTextSource,
    ReferenceLocation,
    ReferenceSpan,
    _AnalysedMark,
    _CitationMark,
    AnalysedLayout,
)


def layout_reference_text(layout: AnalysedLayout) -> str:
    """Return the exact visible text surface used to locate paragraph refs."""

    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        raise ValueError("References can only be located in text layouts")
    return _visible_content_text(source.content)


def layout_reference_spans(layout: AnalysedLayout) -> list[ReferenceSpan]:
    """Return existing ref locations on the same visible-text surface."""

    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        return []
    spans: list[ReferenceSpan] = []
    offset = 0

    def visit(parts: _AnalysedContent) -> None:
        nonlocal offset
        for part in parts:
            if isinstance(part, str):
                offset += len(part)
            elif isinstance(part, _ReferencePlaceholder):
                start = offset
                offset += len(part.text)
                spans.append(ReferenceSpan(
                    start=start,
                    end=offset,
                    citation_page_index=part.citation_page_index,
                    citation_index=part.citation_index,
                    mark_text=part.text,
                ))
            elif isinstance(part, _AnalysedInlineExpression):
                offset += len(part.content)
            else:
                visit(part.children)

    visit(source.content)
    return spans


def replace_layout_references(
    layout: AnalysedLayout,
    spans: Iterable[ReferenceSpan],
) -> AnalysedLayout:
    """Compile validated visible ranges back into immutable content paths."""

    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        raise ValueError("References can only be placed in text layouts")

    materialized = _materialize_reference_text(source.content)
    visible_text = _visible_content_text(materialized)
    ordered = sorted(spans, key=lambda span: (span.start, span.end))
    previous_end = -1
    for span in ordered:
        if span.start < 0 or span.end < span.start or span.end > len(visible_text):
            raise ValueError(
                f"Reference span is outside layout text: {span.start}..{span.end}"
            )
        if span.start < previous_end:
            raise ValueError("Reference spans cannot overlap")
        if visible_text[span.start:span.end] != span.mark_text:
            raise ValueError(
                "Reference span mark does not match layout text: "
                f"{visible_text[span.start:span.end]!r} != {span.mark_text!r}"
            )
        previous_end = span.end

    leaves = _content_text_leaves(materialized)
    replacements: dict[tuple[int, ...], list[tuple[int, int, ReferenceSpan]]] = {}
    for span in ordered:
        matched = _leaf_for_span(leaves, span.start, span.end)
        if matched is None:
            raise ValueError(
                "Reference span crosses a structured content boundary: "
                f"{span.start}..{span.end}"
            )
        path, leaf_start, _leaf_end = matched
        replacements.setdefault(path, []).append(
            (span.start - leaf_start, span.end - leaf_start, span)
        )

    rebuilt = _replace_content_text_leaves(materialized, replacements)
    references: list[ReferenceLocation] = []
    _collect_reference_locations(rebuilt, references)
    return replace(
        layout,
        source=replace(source, content=rebuilt),
        annotations=replace(layout.annotations, references=references),
    )


def _analyse_content(
    content: list,
    references: list[ReferenceLocation],
    prefix: tuple[int, ...] = (),
) -> _AnalysedContent:
    result: list[_AnalysedContentPart] = []
    for index, part in enumerate(content):
        path = (*prefix, index)
        if isinstance(part, Reference):
            references.append(
                ReferenceLocation(
                    path=path,
                    citation_page_index=part.page_index,
                    citation_index=part.order,
                )
            )
            result.append(_ReferencePlaceholder(
                text=str(getattr(part.mark, "char", part.mark)),
                citation_page_index=part.page_index,
                citation_index=part.order,
            ))
        elif isinstance(part, InlineExpression):
            result.append(_AnalysedInlineExpression(part.kind, part.content))
        elif isinstance(part, HTMLTag):
            result.append(
                _AnalysedHTMLTag(
                    name=part.definition.name,
                    attributes=tuple(part.attributes),
                    children=_analyse_content(part.children, references, path),
                )
            )
        elif isinstance(part, str):
            result.append(part)
        else:
            raise TypeError(f"Unsupported source content member: {type(part).__name__}")
    return tuple(result)


def _visible_content_text(content: _AnalysedContent) -> str:
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, _ReferencePlaceholder):
            parts.append(part.text)
        elif isinstance(part, _AnalysedInlineExpression):
            parts.append(part.content)
        else:
            parts.append(_visible_content_text(part.children))
    return "".join(parts)


def _materialize_reference_text(content: _AnalysedContent) -> _AnalysedContent:
    result: list[_AnalysedContentPart] = []
    for part in content:
        if isinstance(part, _ReferencePlaceholder):
            result.append(part.text)
        elif isinstance(part, _AnalysedHTMLTag):
            result.append(replace(
                part,
                children=_materialize_reference_text(part.children),
            ))
        else:
            result.append(part)
    return tuple(result)


def _content_text_leaves(
    content: _AnalysedContent,
) -> list[tuple[tuple[int, ...], int, int]]:
    leaves: list[tuple[tuple[int, ...], int, int]] = []
    offset = 0

    def visit(parts: _AnalysedContent, prefix: tuple[int, ...]) -> None:
        nonlocal offset
        for index, part in enumerate(parts):
            path = (*prefix, index)
            if isinstance(part, str):
                leaves.append((path, offset, offset + len(part)))
                offset += len(part)
            elif isinstance(part, _ReferencePlaceholder):
                offset += len(part.text)
            elif isinstance(part, _AnalysedInlineExpression):
                leaves.append((path, offset, offset + len(part.content)))
                offset += len(part.content)
            else:
                visit(part.children, path)

    visit(content, ())
    return leaves


def _leaf_for_span(
    leaves: list[tuple[tuple[int, ...], int, int]],
    start: int,
    end: int,
) -> tuple[tuple[int, ...], int, int] | None:
    if start != end:
        return next((leaf for leaf in leaves if leaf[1] <= start and end <= leaf[2]), None)
    candidates = [leaf for leaf in leaves if leaf[1] <= start <= leaf[2]]
    if not candidates:
        return None
    # At a structured boundary, attach a zero-width ref to the text on its
    # left. This preserves the natural reading position without crossing tags.
    return candidates[-1]


def _replace_content_text_leaves(
    content: _AnalysedContent,
    replacements: dict[tuple[int, ...], list[tuple[int, int, ReferenceSpan]]],
    prefix: tuple[int, ...] = (),
) -> _AnalysedContent:
    result: list[_AnalysedContentPart] = []
    for index, part in enumerate(content):
        path = (*prefix, index)
        if isinstance(part, str) and path in replacements:
            cursor = 0
            for start, end, span in sorted(
                replacements[path], key=lambda item: (item[0], item[1])
            ):
                if start > cursor:
                    result.append(part[cursor:start])
                result.append(_ReferencePlaceholder(
                    text=span.mark_text,
                    citation_page_index=span.citation_page_index,
                    citation_index=span.citation_index,
                ))
                cursor = end
            if cursor < len(part):
                result.append(part[cursor:])
        elif isinstance(part, _AnalysedInlineExpression) and path in replacements:
            spans = replacements[path]
            if len(spans) != 1:
                raise ValueError('Reference must cover the complete atomic OCR expression')
            start, end, span = spans[0]
            placeholder = _ReferencePlaceholder(span.mark_text, span.citation_page_index, span.citation_index)
            if (start, end) == (0, len(part.content)):
                result.append(placeholder)
            elif (span.extract_from_expression and end == len(part.content)
                  and re.fullmatch(r'\^\{[^{}]+\}', part.content[start:end])
                  and start > 0 and _balanced_expression(part.content[:start])):
                # Only remove a complete terminal annotation. The remaining
                # expression is copied from source, never regenerated by a model.
                result.extend((replace(part, content=part.content[:start]), placeholder))
            elif start == end == 0:
                result.extend((placeholder, part))
            elif start == end == len(part.content):
                result.extend((part, placeholder))
            else:
                raise ValueError('Reference must cover the complete atomic OCR expression')
        elif isinstance(part, _AnalysedHTMLTag):
            result.append(replace(
                part,
                children=_replace_content_text_leaves(
                    part.children, replacements, path
                ),
            ))
        else:
            result.append(part)
    return tuple(result)


def _balanced_expression(text: str) -> bool:
    depth = 0
    escaped = False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not escaped and not text.endswith(('^', '_'))


def _collect_reference_locations(
    content: _AnalysedContent,
    references: list[ReferenceLocation],
    prefix: tuple[int, ...] = (),
) -> None:
    for index, part in enumerate(content):
        path = (*prefix, index)
        if isinstance(part, _ReferencePlaceholder):
            references.append(ReferenceLocation(
                path=path,
                citation_page_index=part.citation_page_index,
                citation_index=part.citation_index,
            ))
        elif isinstance(part, _AnalysedHTMLTag):
            _collect_reference_locations(part.children, references, path)


def project_definition_mark(layout: AnalysedLayout, mark: str | None) -> AnalysedLayout:
    """Remove a printed prefix only when projecting a definition, reversibly."""
    source = layout.source
    if not isinstance(source, _AnalysedTextSource):
        return layout
    prefix = 0
    parts = 0
    if mark and source.content and isinstance(source.content[0], _AnalysedInlineExpression) and source.content[0].content.strip() in {mark, "^{" + mark + "}"}:
        parts = 1
    remaining = source.content[parts:]
    if mark and remaining and isinstance(remaining[0], str):
        first = remaining[0]
        start = len(first) - len(first.lstrip())
        if parts:
            prefix = start
        elif first[start:].startswith(mark):
            end = start + len(mark)
            prefix = end + len(first[end:]) - len(first[end:].lstrip())
    return replace(layout, source=replace(source, citation_prefix=prefix, citation_prefix_parts=parts))


def _citation_prefix(fragment: SourceTextFragment) -> int:
    if fragment.source is None:
        return 0
    original = fragment.source.content
    current = fragment.content
    if original and current and isinstance(original[0], str) and isinstance(current[0], str):
        if original[0].endswith(current[0]):
            return len(original[0]) - len(current[0])
    return 0


def _project_fragment_content(source, layout, references) -> list:
    content = _restore_content(source.content, layout.references, references)
    if layout.ownership == 'citation' and source.citation_prefix_parts:
        content = content[source.citation_prefix_parts:]
    if layout.ownership == "citation" and source.citation_prefix and content:
        content[0] = content[0][source.citation_prefix:]
        if not content[0]:
            content.pop(0)
    if layout.ownership == 'citation' and content and isinstance(content[0], str):
        # Publication cues locate printed continuation, not author prose. Once
        # note identity is restored they must not migrate through translation
        # into an earlier physical page of the same paragraph.
        content[0] = re.sub(r'^\s*[（(]\s*(?:接上[页頁]|续上[页頁])\s*[）)]\s*', '', content[0])
    return content


def _restore_content(
    content: _AnalysedContent,
    annotations: list[ReferenceLocation],
    references: dict[tuple[int, int], Reference],
    prefix: tuple[int, ...] = (),
) -> list:
    annotations_by_path: dict[tuple[int, ...], ReferenceLocation] = {}
    for annotation in annotations:
        if annotation.path in annotations_by_path:
            raise ValueError(f"Duplicate reference location: {annotation.path}")
        annotations_by_path[annotation.path] = annotation

    used_paths: set[tuple[int, ...]] = set()

    def restore_parts(
        parts: _AnalysedContent, current_prefix: tuple[int, ...]
    ) -> list:
        result: list = []
        for index, part in enumerate(parts):
            path = (*current_prefix, index)
            if isinstance(part, _ReferencePlaceholder):
                annotation = annotations_by_path.get(path)
                if annotation is None:
                    raise ValueError(f"Reference placeholder has no annotation: {path}")
                key = (annotation.citation_page_index, annotation.citation_index)
                if key not in references:
                    raise ValueError(f"Reference points to unknown citation: {key}")
                result.append(references[key])
                used_paths.add(path)
            elif isinstance(part, _AnalysedInlineExpression):
                result.append(InlineExpression(part.kind, part.content))
            elif isinstance(part, _AnalysedHTMLTag):
                definition = tag_definition(part.name)
                if definition is None:
                    raise ValueError(f"Unknown analysed HTML tag: {part.name}")
                result.append(
                    HTMLTag(
                        definition=definition,
                        attributes=list(part.attributes),
                        children=restore_parts(part.children, path),
                    )
                )
            else:
                result.append(part)
        return result

    restored = restore_parts(content, prefix)
    expected_paths = {
        path for path in annotations_by_path if path[: len(prefix)] == prefix
    }
    if used_paths != expected_paths:
        unused = expected_paths - used_paths
        raise ValueError(f"Reference annotations have no placeholder: {unused}")
    return restored


def _analyse_mark(mark: str | Mark) -> _CitationMark:
    if isinstance(mark, str):
        return mark
    return _AnalysedMark(mark.number, mark.char, mark.clazz, mark.style)


def _restore_mark(mark: _CitationMark | None) -> str | Mark:
    if mark is None:
        return ''  # OCR may identify a note's content but miss its printed mark.
    if isinstance(mark, str):
        return mark
    return Mark(mark.number, mark.char, mark.clazz, mark.style)


def analyse_text_source(source: SourceTextFragment, ownership: LayoutOwnership,
                        references: list[ReferenceLocation]) -> _AnalysedTextSource:
    """Freeze a text observation without assigning it to an asset or paragraph."""
    record = _AnalysedTextSource(
        page_index=source.page_index,
        source_order=source.source_order,
        bbox=source.bbox,
        content=_analyse_content(
            list(source.source.content)
            if ownership == "citation" and source.source is not None
            else source.content, references),
        reading_order=source.source.reading_order if source.source else None,
        origin=source.source,
        source_start=source.source_start,
        source_end=source.source_end,
        source_text=source.source_text,
        citation_prefix=_citation_prefix(source) if ownership == "citation" else 0,
    )

    return record
