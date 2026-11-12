"""The model's current, single-decision page repair protocol.

Lowering assigns private ordinals and annotations mechanically. It never consults
initial ownership, geometry, text meaning or neighbouring pages.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from .source_ranges import SourceRange


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RefAnchor(StrictModel):
    before: str
    mark: str
    after: str


class ParagraphDestination(StrictModel):
    join: bool = False
    heading_level: int | None = Field(default=None, ge=0)
    text_removals: list[RefAnchor] = Field(default_factory=list)


class BodyDestination(ParagraphDestination):
    kind: Literal['body']
    as_image: bool = False


class NoteDestination(ParagraphDestination):
    kind: Literal['note']
    note_id: str = Field(min_length=1)
    as_image: bool = False


class AssetDestination(ParagraphDestination):
    kind: Literal['asset']
    asset_id: str = Field(min_length=1)
    slot: Literal['title', 'content', 'caption']


class IndependentDestination(ParagraphDestination):
    kind: Literal['independent']
    stream_id: str = Field(min_length=1)


class IgnoredDestination(StrictModel):
    kind: Literal['ignored']


Destination = Annotated[
    BodyDestination | NoteDestination | AssetDestination | IndependentDestination | IgnoredDestination,
    Field(discriminator='kind'),
]


class LayoutDecision(StrictModel):
    layout_id: str
    source_range: SourceRange | None = None
    reason: str = ""
    destination: Destination


class NewNote(StrictModel):
    operation: Literal['define']
    note_id: str = Field(min_length=1)
    mark: str | None = None


class ContinuingNote(StrictModel):
    operation: Literal['continue']
    note_id: str = Field(min_length=1)


class NoteReference(StrictModel):
    layout_id: str
    note_id: str = Field(min_length=1)
    anchor: RefAnchor
    extract_from_expression: bool = False


class RepairPlan(StrictModel):
    reading: str = ''
    layouts: list[LayoutDecision]
    notes: list[Annotated[NewNote | ContinuingNote, Field(discriminator='operation')]] = Field(default_factory=list)
    references: list[NoteReference] = Field(default_factory=list)


def materialize_plan(plan: RepairPlan, page_index: int):
    """Translate the sole model protocol to the assembly's private records."""
    # Local imports keep this wire schema independent of the candidate compiler.
    from .page_repair import PageCandidate, LayoutCandidate, CitationCandidate, ReferenceCandidate, AssetAttachmentCandidate
    from .review_templates import review_text

    def target_error(kind, identity):
        return ValueError(review_text('page_repair_target_feedback', kind=kind, identity=identity))

    citations = []
    indexes = {}
    next_index = 1
    for order, note in enumerate(plan.notes):
        if note.note_id in indexes:
            raise target_error('duplicate', note.note_id)
        incoming = isinstance(note, ContinuingNote)
        index = None if incoming else next_index
        indexes[note.note_id] = index
        if not incoming:
            next_index += 1
        citations.append(CitationCandidate(citation_id=note.note_id, stream_order=order,
            continues_previous=incoming, index=index, mark=None if incoming else note.mark))
    destinations = {item.layout_id: item.destination for item in plan.layouts}
    layouts = []
    for part in plan.layouts:
        dest = part.destination
        ownership, note_id = 'paragraph', None
        if isinstance(dest, IgnoredDestination):
            ownership = 'ignored'
        elif isinstance(dest, NoteDestination):
            ownership, note_id = 'citation', dest.note_id
        elif isinstance(dest, AssetDestination):
            owner = destinations.get(dest.asset_id)
            if isinstance(owner, NoteDestination):
                ownership, note_id = 'citation', owner.note_id
            elif not isinstance(owner, BodyDestination):
                raise target_error('asset', dest.asset_id)
        if note_id is not None and note_id not in indexes:
            raise target_error('note', note_id)
        retained = isinstance(dest, ParagraphDestination)
        heading = dest.heading_level if retained else None
        layouts.append(LayoutCandidate(layout_id=part.layout_id, source_range=part.source_range,
            ownership_evidence=part.reason, paragraph_evidence='', ownership=ownership,
            continues_from_previous=dest.join if retained else False,
            paragraph_role=('heading' if heading is not None else 'body') if retained else None,
            paragraph_level=(heading if heading is not None else -1) if retained else None,
            citation_id=note_id, citation_index=indexes.get(note_id),
            preserve_as_image=dest.as_image if isinstance(dest, (BodyDestination, NoteDestination)) else False,
            asset_attachment=AssetAttachmentCandidate(asset_id=dest.asset_id, slot=dest.slot) if isinstance(dest, AssetDestination) else None,
            independent_stream=dest.stream_id if isinstance(dest, IndependentDestination) else None,
            text_removals=dest.text_removals if retained else [],
            submission=part.model_dump(mode='json')))
    references = []
    for reference in plan.references:
        index = indexes.get(reference.note_id)
        if index is None:
            raise target_error('reference', reference.note_id)
        references.append(ReferenceCandidate(layout_id=reference.layout_id, citation_index=index,
            anchor=reference.anchor, extract_from_expression=reference.extract_from_expression))
    return PageCandidate(page_index=page_index, layouts=layouts, citations=citations, references=references)


def suggested_destination(annotation) -> dict:
    """Encode fallible input suggestions in the same vocabulary as decisions."""
    ownership = annotation['ownership']
    if ownership == 'ignored':
        return dict(kind='ignored')
    value = dict(join=annotation['continues_from_previous'])
    if annotation['paragraph_role'] == 'heading':
        value['heading_level'] = max(0, annotation['paragraph_level'] or 0)
    if annotation.get('text_removals'):
        value['text_removals'] = annotation['text_removals']
    if attachment := annotation.get('asset_attachment'):
        return dict(value, kind='asset', **attachment)
    if independent := annotation.get('independent_stream'):
        return dict(value, kind='independent', stream_id=independent)
    if ownership == 'citation':
        return dict(value, kind='note', note_id=annotation['citation_id'])
    return dict(value, kind='body')
