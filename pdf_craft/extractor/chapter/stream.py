"""Incremental traditional extraction with replayable, disk-backed results.

Global TOC decisions are applied only by the final chapter assembler. Source
records and closed content can be consumed before those decisions are known.
"""
# pylint: disable=protected-access

import pickle
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
from pathlib import Path

from ...markdown.paragraph import flatten
from ...pdf import Page
from .chapter import (
    Chapter, SourceTextFragment, TextFlowItem,
    _encode_flow, _encode_reference, search_references_in_chapter,
)
from .content import join_texts_in_content
from .generation import (
    _assemble_flow_items, _partition_chapters, _split_reference_origins,
    _replace_mark_with_reference,
)
from .jointer import JoinedPageProgress, Jointer
from .content import normalize_source_projection, _parse_block_content
from .mark import transform2mark
from .reference import References
from .source import capture_sources
from .numeric_marks import explicit_numbers, flattened_numbers
from .marginal_numbers import reading_notes, reading_body


def _joined(pages: Iterable[Page], footnotes: bool, on_page=None, on_raw_page=None, *, includes_footnotes=True):
    sources = {}

    def layouts():
        for page in pages:
            if on_raw_page is not None:
                on_raw_page(page)
            if on_page is not None:
                on_page(page.index)
            sources.clear()
            sources.update(capture_sources([page]))
            yield page.index, reading_notes(page) if footnotes else reading_body(page, includes_footnotes=includes_footnotes)

    return Jointer(layouts(), sources)


def _references_with_progress(jointer: Jointer, numeric_marks=None, flat_marks=None):
    numeric_marks = numeric_marks if numeric_marks is not None else {}
    flat_marks = flat_marks if flat_marks is not None else {}
    buffers = {}
    for item in jointer.execute_with_progress():
        if isinstance(item, JoinedPageProgress):
            for page in sorted(page for page in buffers if page <= item.closed_through):
                yield References(page_index=page, items=buffers.pop(page),
                                 numeric_marks=numeric_marks.get(page, ()), flat_marks=flat_marks.get(page, ()))
            for page in list(numeric_marks):
                if page <= item.closed_through:
                    del numeric_marks[page]
                    flat_marks.pop(page, None)
            yield item
        else:
            for page, layout in _split_reference_origins(item, numeric_marks):
                buffers.setdefault(page, []).append(layout)


def _position(item):
    first = item.children[0] if isinstance(item, TextFlowItem) else item
    order = (first.source.reading_order if isinstance(first, SourceTextFragment)
             and first.source is not None else getattr(first, "reading_order", 0))
    return first.page_index, order or 0


def resolve_to_disk(
    read_pages: Callable[[], Iterable[Page]],
    database: Path,
    *,
    includes_footnotes: bool = True,
    on_body=None,
    on_footnote=None,
) -> None:
    """Read unknown-length page streams; persist closed units before callbacks."""
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""
            DROP TABLE IF EXISTS bodies;
            DROP TABLE IF EXISTS notes;
            DROP TABLE IF EXISTS refs;
            CREATE TABLE bodies (page INTEGER, position INTEGER, value BLOB, unresolved INTEGER);
            CREATE TABLE notes (page INTEGER PRIMARY KEY, value BLOB);
            CREATE TABLE refs (page INTEGER, number INTEGER, value BLOB, used INTEGER DEFAULT 0,
                               PRIMARY KEY(page, number));
        """)

        def store_body(item, unresolved=False):
            if isinstance(item, TextFlowItem):
                normalize_source_projection(item)
            page, position = _position(item)
            db.execute("INSERT INTO bodies VALUES (?, ?, ?, ?)",
                       (page, position, pickle.dumps(item), int(unresolved)))

        def notes():
            numeric_marks = {}
            flat_marks = {}
            def observe(page):
                explicit = set()
                flattened = Counter()
                for layout in page.body_layouts:
                    content = _parse_block_content(layout.text)
                    explicit.update(explicit_numbers(content))
                    flattened.update(flattened_numbers(content))
                flat_marks[page.index] = {mark for mark, count in flattened.items() if count == 1} - explicit
                numeric_marks[page.index] = explicit | flat_marks[page.index]
            for references in _references_with_progress(
                    _joined(read_pages(), True, on_raw_page=observe), numeric_marks, flat_marks):
                if isinstance(references, JoinedPageProgress):
                    yield references
                    continue
                for item in references.unindexed_items:
                    store_body(item, unresolved=True)
                for reference in references.values:
                    for item in reference.flow_items:
                        if isinstance(item, TextFlowItem):
                            normalize_source_projection(item)
                    db.execute("INSERT INTO refs(page,number,value) VALUES (?, ?, ?)",
                               (*reference.id, pickle.dumps(reference)))
                    if on_footnote is not None:
                        on_footnote(_encode_reference(reference))
                db.execute("INSERT INTO notes VALUES (?, ?)",
                           (references.page_index, pickle.dumps(references)))

        note_pages = iter(notes()) if includes_footnotes else iter(())
        last_note_page = -1
        processed_note_page = -1
        notes_done = not includes_footnotes

        def advance_notes(page, *, require_closed=False):
            nonlocal last_note_page, processed_note_page, notes_done
            while not notes_done and (
                processed_note_page < page or (require_closed and last_note_page < page)
            ):
                progress = next(note_pages, None)
                if progress is None:
                    notes_done = True
                else:
                    last_note_page = progress.closed_through
                    processed_note_page = progress.processed_through

        def get_references(page):
            advance_notes(page, require_closed=True)
            row = db.execute("SELECT value FROM notes WHERE page=?", (page,)).fetchone()
            return pickle.loads(row[0]) if row else None

        def unresolved_before(position=None):
            query = "SELECT rowid,value FROM bodies WHERE unresolved=1"
            parameters = ()
            if position is not None:
                query += " AND (page < ? OR (page = ? AND position <= ?))"
                parameters = (position[0], position[0], position[1])
            query += " ORDER BY page,position,rowid"
            for rowid, value in db.execute(query, parameters):
                db.execute("UPDATE bodies SET unresolved=2 WHERE rowid=?", (rowid,))
                yield pickle.loads(value)

        def paragraphs():
            for item in _joined(read_pages(), False, on_page=advance_notes, includes_footnotes=includes_footnotes).execute():
                if isinstance(item, TextFlowItem):
                    for fragment in item.children:
                        if isinstance(fragment, SourceTextFragment):
                            # Without a printed mark this page need not wait for
                            # the independent definition stream to catch up.
                            if includes_footnotes and (explicit_numbers(fragment.content)
                                or flattened_numbers(fragment.content) or any(
                                char == "*" or transform2mark(char) is not None
                                for part in flatten(fragment.content) if isinstance(part, str) for char in part
                            )):
                                references = get_references(fragment.page_index)
                                if references is not None:
                                    _replace_mark_with_reference(references, fragment)
                            join_texts_in_content(fragment.content)
                # Close ownership on this source page before advancing the
                # reading stream. Unresolved definitions are real body content,
                # not an EOF appendix. Their payloads wait on disk.
                advance_notes(_position(item)[0], require_closed=True)
                yield from unresolved_before(_position(item))
                store_body(item)
                yield item
            for _ in note_pages:
                pass
            yield from unresolved_before()

        for item in _assemble_flow_items(paragraphs()):
            if on_body is not None:
                on_body(_encode_flow(item))
        db.commit()


def chapters_from_disk(database: Path, toc, *, chunk_size: int | None = None) -> Iterator[Chapter]:
    """Finalize chapter boundaries with only the current chapter in memory."""
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""
            DROP TABLE IF EXISTS assembled;
            CREATE TABLE assembled (id INTEGER PRIMARY KEY, min_page INTEGER, value BLOB);
            UPDATE refs SET used=0;
        """)

        adopted = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='adopted'").fetchone())

        def bodies():
            ordering = 'document_order,rowid' if adopted else 'page,position,rowid'
            for row in db.execute('SELECT value FROM bodies ORDER BY ' + ordering):
                item = pickle.loads(row[0])
                if adopted:
                    # Bodies may have been delivered before a continued
                    # definition closed. Hydrate only during final assembly.
                    for reference in search_references_in_chapter(Chapter(None, -1, [item])):
                        found = db.execute('SELECT value FROM refs WHERE page=? AND number=?', reference.id).fetchone()
                        if found is None:
                            raise ValueError(f'Missing adopted reference {reference.id}')
                        stored = pickle.loads(found[0])
                        reference.flow_items = stored.flow_items
                        reference.mark = stored.mark
                    from .chapter import StandaloneAsset, DisplayFormula
                    if isinstance(item, (StandaloneAsset, DisplayFormula)):
                        item = item.asset
                if adopted and isinstance(item, TextFlowItem):
                    children = []
                    for child in item.children:
                        if child.page_index in toc.page_indexes:
                            if children:
                                yield TextFlowItem(item.role, item.level, children)
                                children = []
                        else:
                            children.append(child)
                    if children:
                        yield TextFlowItem(item.role, item.level, children)
                elif not adopted or item.page_index not in toc.page_indexes:
                    yield item

        count = 0
        for count, chapter in enumerate(_partition_chapters(bodies(), toc, assembled=adopted, chunk_size=chunk_size), start=1):
            for reference in search_references_in_chapter(chapter):
                db.execute("UPDATE refs SET used=1 WHERE page=? AND number=?", reference.id)
            minimum = min((child.page_index for item in chapter.flow_items
                           if isinstance(item, TextFlowItem) for child in item.children), default=None)
            db.execute("INSERT INTO assembled VALUES (?, ?, ?)",
                       (count, minimum, pickle.dumps(chapter)))
        if chunk_size is not None:
            for (value,) in db.execute("SELECT value FROM assembled ORDER BY id"):
                yield pickle.loads(value)
            definitions = []
            for page, value in db.execute("SELECT page,value FROM refs WHERE used=0 ORDER BY page,number"):
                if page in toc.page_indexes:
                    continue
                definitions.append(pickle.loads(value))
                if len(definitions) == chunk_size:
                    yield Chapter(None, -1, [], definitions)
                    definitions = []
            if definitions:
                yield Chapter(None, -1, [], definitions)
            return
        for page, number in db.execute("SELECT page,number FROM refs WHERE used=0 ORDER BY page,number"):
            if count == 0:
                count = 1
                db.execute("INSERT INTO assembled VALUES (1,NULL,?)", (pickle.dumps(Chapter(None, -1, [])),))
            row = db.execute("SELECT id FROM assembled WHERE min_page<=? ORDER BY id DESC LIMIT 1", (page,)).fetchone()
            target = row[0] if row else 1
            # Store assignment separately; don't repeatedly deserialize large chapters.
            db.execute("UPDATE refs SET used=? WHERE page=? AND number=?", (-target, page, number))
        for index, value in db.execute("SELECT id,value FROM assembled ORDER BY id"):
            chapter = pickle.loads(value)
            chapter.definitions.extend(pickle.loads(row[0]) for row in db.execute(
                "SELECT value FROM refs WHERE used=? ORDER BY page,number", (-index,)))
            yield chapter
