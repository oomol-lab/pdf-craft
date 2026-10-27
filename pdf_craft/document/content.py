"""Private PCEX item stream and its chapter-file consumer.

The envelope carries only chapter placement; payloads are the existing PCEX
objects. Resource paths and document metadata belong to the enclosing view.
"""
# pylint: disable=protected-access
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from contextlib import aclosing
from pathlib import Path
from xml.etree.ElementTree import Element

from ..common import save_xml
from ..extractor.chapter.chapter import (
    Chapter, FlowItem, Reference, TextFlowItem, SourceAsset, _encode_flow, _encode_reference, indent_chapter,
)
from ..runtime import IO_DOMAIN, worker_iter

@dataclass
class ContentItem:
    """An existing flow item or definition, with its source chapter placement."""
    filename: str
    chapter_id: int | None
    level: int
    index: int
    value: FlowItem | Reference | None  # None preserves an empty chapter.
    original: FlowItem | Reference | None = None
    asset_context: str = ""
    narrative_states: dict[tuple[str, int, int], str] | None = None
    asset_children: frozenset[int] | None = None


def chapter_filename(chapter: Chapter) -> str:
    return f"chapter_{chapter.id if chapter.id is not None else 'head'}.xml"


def chapter_paths(chapters: Path):
    """Canonical chapter order, without decoding the chapter bodies."""
    import re
    head = chapters / 'chapter_head.xml'
    if head.exists():
        yield head
    numbered = []
    for path in chapters.glob('chapter_*.xml'):
        match = re.fullmatch(r'chapter_(\d+)\.xml', path.name)
        if match:
            numbered.append((int(match[1]), path))
    yield from (path for _, path in sorted(numbered))


def file_content(path: Path):
    """Decode one direct flow item/definition, releasing XML after each item.

    Body reference occurrences only need identity; complete definitions are an
    independent stream channel. They are not copied into every body paragraph.
    """
    from xml.etree.ElementTree import iterparse
    from .package import _v3_chapter_xml
    from ..extractor.chapter.chapter import decode
    from io import BytesIO
    # Legacy chapters have a different source namespace. Keep the established
    # migration codec, then use the same item parser for the migrated XML.
    with path.open('rb') as probe:
        first_tags = []
        for event, element in iterparse(probe, events=('start',)):
            del event
            first_tags.append(element.tag)
            if len(first_tags) == 2:
                break
    document = BytesIO(_v3_chapter_xml(path)) if first_tags[-1:] == ['body'] else path.open('rb')
    with document:
        stack = []
        chapter_id, level, body_index, note_index = None, -1, 0, 0
        for event, element in iterparse(document, events=('start', 'end')):
            if event == 'start':
                stack.append(element)
                if len(stack) == 1:
                    identifier = element.get('id')
                    chapter_id = int(identifier) if identifier is not None else None
                    level = int(element.get('level', '-1'))
                    yield ContentItem(path.name, chapter_id, level, -1, None)
                continue
            if len(stack) == 3 and stack[-2].tag in ('flow', 'references'):
                is_reference = stack[-2].tag == 'references'
                root = Element('chapter', stack[0].attrib)
                flow = Element('flow')
                root.append(flow)
                references = Element('references')
                if is_reference:
                    references.append(element)
                else:
                    flow.append(element)
                    for occurrence in element.iter('ref'):
                        definition = Element('ref', {'id': occurrence.get('id', '')})
                        mark = Element('mark')
                        mark.text = '?'
                        definition.extend((mark, Element('flow')))
                        references.append(definition)
                if len(references):
                    root.append(references)
                chapter = decode(root)
                value = chapter.definitions[0] if is_reference else chapter.flow_items[0]
                yield ContentItem(path.name, chapter_id, level, note_index if is_reference else body_index, value)
                if is_reference:
                    note_index += 1
                else:
                    body_index += 1
                stack[-2].remove(element)
                element.clear()
            stack.pop()


def asset_contexts(path: Path):
    """Only clipped semantic context is indexed; source content remains on disk."""
    from ..transformer.anchored_translation import _flow_text, _clip
    from ..extractor.chapter.chapter import StandaloneAsset
    contexts = {}
    heading, previous = '', ''
    pending = []
    for item in file_content(path):
        value = item.value
        if isinstance(value, TextFlowItem):
            following = _flow_text(value)
            for index, before in pending:
                contexts[index] = _clip('\n'.join(part for part in (before, following) if part))
            pending.clear()
            if any(isinstance(child, SourceAsset) for child in value.children):
                contexts[item.index] = _clip('\n'.join(part for part in (heading, following) if part))
            if value.role == 'heading':
                heading = following
            previous = following
        elif isinstance(value, StandaloneAsset):
            pending.append((item.index, previous))
    for index, before in pending:
        contexts[index] = _clip(before)
    return contexts


def contextual_content(path):
    """Release source items as soon as their original-text lookahead is known.

    Runs on the IO domain. An arbitrary run of standalone assets/formulas is
    spooled, rather than accumulating a chapter-wide context dictionary.
    """
    import pickle
    from tempfile import TemporaryDirectory
    from ..extractor.chapter.chapter import StandaloneAsset
    from ..transformer.anchored_translation import _flow_text, _clip
    heading, previous = '', ''
    with TemporaryDirectory(prefix='pdf-craft-asset-context-') as directory:
        spool = Path(directory) / 'pending'
        count = 0
        def drain(following):
            nonlocal count
            if count:
                with spool.open('rb') as file:
                    for _ in range(count):
                        pending = pickle.load(file)
                        if isinstance(pending.value, StandaloneAsset):
                            pending.asset_context = _clip('\n'.join(part for part in (previous, following) if part))
                        yield pending
                spool.unlink()
                count = 0
        for item in file_content(path):
            value = item.value
            if isinstance(value, TextFlowItem):
                following = _clip(_flow_text(value))
                yield from drain(following)
                if any(isinstance(child, SourceAsset) for child in value.children):
                    item.asset_context = _clip('\n'.join(part for part in (heading, following) if part))
                if value.role == 'heading':
                    heading = following
                previous = following
                yield item
            elif isinstance(value, StandaloneAsset) or count:
                with spool.open('ab') as file:
                    pickle.dump(item, file, protocol=pickle.HIGHEST_PROTOCOL)
                count += 1
            else:
                yield item
        yield from drain('')


async def read_content(chapters: Path) -> AsyncGenerator[ContentItem, None]:
    paths = await IO_DOMAIN.run(lambda: list(chapter_paths(chapters)))
    for path in paths:
        async with aclosing(worker_iter(contextual_content(path))) as incoming:
            async for item in incoming:
                yield item


class ChapterWriter:
    """Retain only the current chapter; no full-book Python object graph."""
    def __init__(self, path: Path):
        self.path = path
        self.filename = None
        self.root = None
        self.flow = None
        self.references = None

    def append(self, item: ContentItem):
        if item.filename != self.filename:
            self.finish()
            self.filename = item.filename
            self.root = Element("chapter")
            if item.chapter_id is not None:
                self.root.set("id", str(item.chapter_id))
            if item.level != -1:
                self.root.set("level", str(item.level))
            self.flow = Element("flow")
            self.references = Element("references")
            self.root.append(self.flow)
        assert self.references is not None and self.flow is not None
        if isinstance(item.value, Reference):
            self.references.append(_encode_reference(item.value))
        elif item.value is not None:
            self.flow.append(_encode_flow(item.value))

    def finish(self):
        if self.root is not None:
            assert self.references is not None and self.filename is not None
            if len(self.references):
                self.root.append(self.references)
            self.path.mkdir(parents=True, exist_ok=True)
            save_xml(indent_chapter(self.root), self.path / self.filename)
            self.root = None
