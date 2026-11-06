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

from ...pdf import Page
from .chapter import (
    Chapter, TextFlowItem, search_references_in_chapter,
)
from .generation import _partition_chapters


def resolve_to_disk(
    read_pages: Callable[[], Iterable[Page]],
    database: Path,
    *,
    includes_footnotes: bool = True,
    on_body=None,
    on_footnote=None,
) -> None:
    """Resolve an offline iterator using the same finite page operations."""
    from .traditional import TraditionalAssembly
    assembly = TraditionalAssembly(database, includes_footnotes=includes_footnotes,
                                   on_body=on_body, on_footnote=on_footnote)
    for page in read_pages():
        assembly.append(page)
    assembly.finish()


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
