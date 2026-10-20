"""Incremental traditional extraction with replayable, disk-backed results.

Global TOC decisions are applied only by the final chapter assembler. Source
records and closed content can be consumed before those decisions are known.
"""
# pylint: disable=protected-access

import pickle
import sqlite3
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
    _assemble_flow_items, _extract_page_references, _partition_chapters,
    _replace_mark_with_reference,
)
from .jointer import Jointer, normalize_source_projection
from .mark import transform2mark
from .source import capture_sources


def _joined(pages: Iterable[Page], footnotes: bool):
    sources = {}

    def layouts():
        for page in pages:
            sources.clear()
            sources.update(capture_sources([page]))
            yield page.index, page.footnotes_layouts if footnotes else page.body_layouts

    return Jointer(layouts(), sources)


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
            for references in _extract_page_references(_joined(read_pages(), True)):
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
                yield references.page_index

        note_pages = iter(notes()) if includes_footnotes else iter(())
        last_note_page = -1
        notes_done = not includes_footnotes

        def get_references(page):
            nonlocal last_note_page, notes_done
            while not notes_done and last_note_page < page:
                next_page = next(note_pages, None)
                if next_page is None:
                    notes_done = True
                else:
                    last_note_page = next_page
            row = db.execute("SELECT value FROM notes WHERE page=?", (page,)).fetchone()
            return pickle.loads(row[0]) if row else None

        def paragraphs():
            for item in _joined(read_pages(), False).execute():
                if isinstance(item, TextFlowItem):
                    for fragment in item.children:
                        if isinstance(fragment, SourceTextFragment):
                            # Without a printed mark this page need not wait for
                            # the independent definition stream to catch up.
                            if includes_footnotes and any(
                                char == "*" or transform2mark(char) is not None
                                for part in flatten(fragment.content) if isinstance(part, str) for char in part
                            ):
                                references = get_references(fragment.page_index)
                                if references is not None:
                                    _replace_mark_with_reference(references, fragment)
                            join_texts_in_content(fragment.content)
                store_body(item)
                yield item

        for item in _assemble_flow_items(paragraphs()):
            if on_body is not None:
                on_body(_encode_flow(item))
        for _ in note_pages:
            pass
        if on_body is not None:
            unresolved = (pickle.loads(row[0]) for row in db.execute(
                "SELECT value FROM bodies WHERE unresolved=1 ORDER BY page,position,rowid"))
            for item in _assemble_flow_items(unresolved):
                on_body(_encode_flow(item))
        db.commit()


def chapters_from_disk(database: Path, toc) -> Iterator[Chapter]:
    """Finalize chapter boundaries with only the current chapter in memory."""
    with closing(sqlite3.connect(database)) as db:
        db.executescript("""
            DROP TABLE IF EXISTS assembled;
            CREATE TABLE assembled (id INTEGER PRIMARY KEY, min_page INTEGER, value BLOB);
            UPDATE refs SET used=0;
        """)

        def bodies():
            for row in db.execute("SELECT value FROM bodies ORDER BY page, position, rowid"):
                yield pickle.loads(row[0])

        count = 0
        for count, chapter in enumerate(_partition_chapters(bodies(), toc), start=1):
            for reference in search_references_in_chapter(chapter):
                db.execute("UPDATE refs SET used=1 WHERE page=? AND number=?", reference.id)
            minimum = min((child.page_index for item in chapter.flow_items
                           if isinstance(item, TextFlowItem) for child in item.children), default=None)
            db.execute("INSERT INTO assembled VALUES (?, ?, ?)",
                       (count, minimum, pickle.dumps(chapter)))
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
