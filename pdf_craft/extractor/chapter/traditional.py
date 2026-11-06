"""Finite page assembly operations for the traditional extraction stream."""
# pylint: disable=protected-access
import pickle
import sqlite3
from collections import Counter
from contextlib import closing, contextmanager
from pathlib import Path

from ...markdown.paragraph import flatten
from ...pdf import Page
from .chapter import SourceTextFragment, TextFlowItem, _encode_flow, _encode_reference
from .content import join_texts_in_content, normalize_source_projection, _parse_block_content
from .generation import _assemble_flow_items, _replace_mark_with_reference, _split_reference_origins
from .jointer import JoinedPageProgress, Jointer
from .marginal_numbers import reading_body, reading_notes
from .mark import transform2mark
from .numeric_marks import explicit_numbers, flattened_numbers
from .reference import References
from .source import capture_sources, _position


def _needs_references(fragment):
    return (explicit_numbers(fragment.content) or flattened_numbers(fragment.content) or any(
        char == '*' or transform2mark(char) is not None
        for part in flatten(fragment.content) if isinstance(part, str) for char in part))


class TraditionalAssembly:
    """Retain paragraph tails; persist closed backlog without waiting for input.

    The async owner calls append serially. Connections and transactions belong
    to individual calls, so no worker or connection is held between pages.
    """

    def __init__(self, database: Path, *, includes_footnotes=True, on_body=None, on_footnote=None):
        self.database = database
        self.includes_footnotes = includes_footnotes
        self.on_body = on_body
        self.on_footnote = on_footnote
        self._sources = {}
        self._body = Jointer((), self._sources)
        self._notes = Jointer((), self._sources)
        self._buffers = {}
        self._numeric = {}
        self._flat = {}
        self._closed_notes = -1
        self._finished = False
        with closing(sqlite3.connect(database)) as db:
            db.executescript('''
                DROP TABLE IF EXISTS bodies;
                DROP TABLE IF EXISTS notes;
                DROP TABLE IF EXISTS refs;
                DROP TABLE IF EXISTS pending;
                DROP TABLE IF EXISTS notifications;
                CREATE TABLE bodies (page INTEGER, position INTEGER, value BLOB, unresolved INTEGER);
                CREATE TABLE notes (page INTEGER PRIMARY KEY, value BLOB);
                CREATE TABLE refs (page INTEGER, number INTEGER, value BLOB, used INTEGER DEFAULT 0,
                                   PRIMARY KEY(page, number));
                CREATE TABLE pending (id INTEGER PRIMARY KEY, last_page INTEGER, value BLOB);
                CREATE TABLE notifications (id INTEGER PRIMARY KEY, kind TEXT, value BLOB);
            ''')

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.database)) as db:
            with db:
                yield db

    def append(self, page: Page):
        """Advance both jointers with an already available page, then return."""
        if self._finished:
            raise RuntimeError('assembly already finished')
        self._sources.clear()
        self._sources.update(capture_sources([page]))
        with self._transaction() as db:
            if self.includes_footnotes:
                explicit = set()
                flattened = Counter()
                for layout in page.body_layouts:
                    content = _parse_block_content(layout.text)
                    explicit.update(explicit_numbers(content))
                    flattened.update(flattened_numbers(content))
                self._flat[page.index] = {mark for mark, count in flattened.items() if count == 1} - explicit
                self._numeric[page.index] = explicit | self._flat[page.index]
                self._consume_notes(db, self._notes.append(page.index, reading_notes(page)))
            self._queue_body(db, self._body.append(
                page.index, reading_body(page, includes_footnotes=self.includes_footnotes)))
            self._drain(db)
        self._deliver()

    def finish(self):
        if self._finished:
            return
        with self._transaction() as db:
            if self.includes_footnotes:
                self._consume_notes(db, self._notes.finish())
            self._queue_body(db, self._body.finish())
            self._drain(db, eof=True)
            self._unresolved(db)
        self._finished = True
        self._deliver()

    def _consume_notes(self, db, events):
        for item in events:
            if not isinstance(item, JoinedPageProgress):
                for page, layout in _split_reference_origins(item, self._numeric):
                    self._buffers.setdefault(page, []).append(layout)
                continue
            self._closed_notes = item.closed_through
            for page in sorted(page for page in self._buffers if page <= item.closed_through):
                references = References(page, self._buffers.pop(page),
                                        numeric_marks=self._numeric.get(page, ()),
                                        flat_marks=self._flat.get(page, ()))
                for body in references.unindexed_items:
                    self._store_body(db, body, unresolved=True)
                for reference in references.values:
                    for body in reference.flow_items:
                        if isinstance(body, TextFlowItem):
                            normalize_source_projection(body)
                    db.execute('INSERT INTO refs(page,number,value) VALUES (?, ?, ?)',
                               (*reference.id, pickle.dumps(reference)))
                    if self.on_footnote is not None:
                        self._notify(db, 'note', _encode_reference(reference))
                db.execute('INSERT INTO notes VALUES (?, ?)', (page, pickle.dumps(references)))
            for page in list(self._numeric):
                if page <= item.closed_through:
                    del self._numeric[page]
                    self._flat.pop(page, None)

    @staticmethod
    def _queue_body(db, events):
        for item in events:
            if isinstance(item, JoinedPageProgress):
                continue
            needed = [_position(item)[0]]
            if isinstance(item, TextFlowItem):
                needed.extend(fragment.page_index for fragment in item.children
                              if isinstance(fragment, SourceTextFragment) and _needs_references(fragment))
            db.execute('INSERT INTO pending(last_page,value) VALUES (?, ?)',
                       (max(needed), pickle.dumps(item)))

    def _drain(self, db, *, eof=False):
        for rowid, last_page, value in db.execute('SELECT id,last_page,value FROM pending ORDER BY id'):
            if self.includes_footnotes and not eof and last_page > self._closed_notes:
                break
            item = pickle.loads(value)
            if isinstance(item, TextFlowItem):
                for fragment in item.children:
                    if not isinstance(fragment, SourceTextFragment):
                        continue
                    if self.includes_footnotes and _needs_references(fragment):
                        row = db.execute('SELECT value FROM notes WHERE page=?', (fragment.page_index,)).fetchone()
                        if row:
                            _replace_mark_with_reference(pickle.loads(row[0]), fragment)
                    join_texts_in_content(fragment.content)
            self._unresolved(db, _position(item))
            self._store_body(db, item)
            self._emit_body(db, item)
            db.execute('DELETE FROM pending WHERE id=?', (rowid,))

    @staticmethod
    def _store_body(db, item, unresolved=False):
        if isinstance(item, TextFlowItem):
            normalize_source_projection(item)
        db.execute('INSERT INTO bodies VALUES (?, ?, ?, ?)',
                   (*_position(item), pickle.dumps(item), int(unresolved)))

    def _unresolved(self, db, position=None):
        query = 'SELECT rowid,value FROM bodies WHERE unresolved=1'
        parameters = ()
        if position is not None:
            query += ' AND (page < ? OR (page = ? AND position <= ?))'
            parameters = (position[0], position[0], position[1])
        query += ' ORDER BY page,position,rowid'
        for rowid, value in db.execute(query, parameters):
            db.execute('UPDATE bodies SET unresolved=2 WHERE rowid=?', (rowid,))
            self._emit_body(db, pickle.loads(value))

    def _emit_body(self, db, item):
        if self.on_body is not None:
            for flow in _assemble_flow_items((item,)):
                self._notify(db, 'body', _encode_flow(flow))

    @staticmethod
    def _notify(db, kind, value):
        db.execute('INSERT INTO notifications(kind,value) VALUES (?, ?)', (kind, pickle.dumps(value)))

    def _deliver(self):
        # A long definition can release many queued bodies at once. Keep that
        # notification backlog on disk, and publish only committed results.
        with self._transaction() as db:
            for rowid, kind, value in db.execute('SELECT id,kind,value FROM notifications ORDER BY id'):
                callback = self.on_body if kind == 'body' else self.on_footnote
                assert callback is not None
                callback(pickle.loads(value))
                db.execute('DELETE FROM notifications WHERE id=?', (rowid,))
