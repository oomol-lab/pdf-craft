"""Incremental adopted-page assembly; only unfinished natural units stay in RAM."""
# pylint: disable=protected-access
import pickle
import sqlite3
from contextlib import closing
from copy import deepcopy

from .chapter import Reference, SourceAsset, StandaloneAsset, DisplayFormula, _encode_flow, _encode_reference
from .page_analysis import _restore_layouts, _restore_mark, PageStructureError
from .stream import _position


class _Paragraphs:
    def __init__(self, emit, references):
        self.pending = []
        self.emit, self.references = emit, references

    def append(self, layout):
        is_text = layout.source_order is not None
        if is_text and not layout.continues_from_previous:
            self.finish()
        elif is_text and not any(item.source_order is not None for item in self.pending):
            raise PageStructureError('Continuation has no predecessor', layout.page_index)
        if is_text and layout.continues_from_previous:
            previous = next(item for item in reversed(self.pending) if item.source_order is not None)
            if (previous.paragraph_role, previous.paragraph_level) != (layout.paragraph_role, layout.paragraph_level):
                raise PageStructureError('Continued text layouts disagree on paragraph metadata', layout.page_index)
        if not is_text and getattr(layout.source, 'ref', None) == 'formula':
            self.finish()
            self.pending.append(deepcopy(layout))
            self.finish()
        else:
            self.pending.append(deepcopy(layout))

    def finish(self):
        if not self.pending:
            return
        # Incoming edges are authoritative. Outgoing edges are a projection,
        # never a competing edit from an earlier concurrently repaired page.
        texts = [item for item in self.pending if item.source_order is not None]
        for index, item in enumerate(texts):
            item.annotations.continues_to_next = index + 1 < len(texts)
        references = self.references(self.pending)
        for item in _restore_layouts(self.pending, references):
            if isinstance(item, SourceAsset):
                item = DisplayFormula(item) if item.ref == 'formula' else StandaloneAsset(item)
            self.emit(item)
        self.pending.clear()


class PageAssembly:
    """Single ordered writer; complete bodies/references share the final disk spool."""
    def __init__(self, database, *, on_body=None, on_footnote=None):
        self.database = database
        self.body_callback, self.note_callback = on_body, on_footnote
        self.notes = {}
        self.active_note = None
        self._transaction = None
        self._events = []
        self.body = _Paragraphs(self._body, self._references)
        self._run('CREATE TABLE adopted (page INTEGER PRIMARY KEY,value BLOB)')
        self._run('CREATE TABLE edges(left_page INTEGER,right_page INTEGER,PRIMARY KEY(left_page,right_page))')
        self._run('CREATE TABLE bodies(page INTEGER,position INTEGER,value BLOB,unresolved INTEGER)')
        self._run('CREATE TABLE refs(page INTEGER,number INTEGER,value BLOB,used INTEGER DEFAULT 0,PRIMARY KEY(page,number))')

    def _run(self, sql, parameters=()):
        if self._transaction is not None:
            return self._transaction.execute(sql, parameters).fetchall()
        with closing(sqlite3.connect(self.database)) as db:
            with db:
                return db.execute(sql, parameters).fetchall()

    def _references(self, layouts):
        result = {}
        for layout in layouts:
            for annotation in layout.references:
                key = annotation.citation_page_index, annotation.citation_index
                rows = self._run('SELECT value FROM refs WHERE page=? AND number=?', key)
                if not rows:
                    raise PageStructureError(f'Reference has no definition: {key}', layout.page_index)
                result[key] = pickle.loads(rows[0][0])
        return result

    def _body(self, item):
        page, position = _position(item.asset if isinstance(item, (StandaloneAsset, DisplayFormula)) else item)
        self._run('INSERT INTO bodies VALUES (?,?,?,0)', (page, position, pickle.dumps(item)))
        if self.body_callback is not None:
            self._events.append((self.body_callback, _encode_flow(item)))

    def _close_note(self, identifier):
        ref, paragraphs = self.notes.pop(identifier)
        paragraphs.finish()
        self._run('UPDATE refs SET value=? WHERE page=? AND number=?', (pickle.dumps(ref), *ref.id))
        if self.note_callback is not None:
            self._events.append((self.note_callback, _encode_reference(ref)))

    def _bind_incoming(self, page, refined):
        # Local jobs see only raw neighbours. A repaired predecessor may have
        # introduced a definition absent from that raw window. Bind an explicit
        # incoming continuation here, at the sole ordered adoption boundary.
        if self.active_note is not None or refined:
            page = deepcopy(page)
            next_index = max((citation.index or 0 for citation in page.citations), default=0) + 1
            for citation in page.citations:
                members = sorted((layout for layout in page.layouts
                                  if layout.citation_id == citation.citation_id
                                  and layout.source_order is not None),
                                 key=lambda item: item.document_order)
                incoming = citation.continues_previous or (members and members[0].continues_from_previous)
                if citation.index is None and citation.origin_page_index is None and incoming and self.active_note is not None:
                    alias = citation.citation_id
                    citation.citation_id = self.active_note
                    citation.origin_page_index = self.notes[self.active_note][0].page_index
                    for layout in page.layouts:
                        if layout.citation_id == alias:
                            layout.annotations.citation_id = self.active_note
                elif refined and citation.index is None and citation.origin_page_index is None and members and not members[0].continues_from_previous:
                    # A validated semantic decision can identify a standalone
                    # note even when OCR missed its printed mark. Allocate an
                    # internal identity; never invent a visible label or an
                    # attachment to a preceding note.
                    citation.index = next_index
                    citation.origin_page_index = page.page_index
                    next_index += 1
                    for layout in page.layouts:
                        if layout.citation_id == citation.citation_id:
                            layout.annotations.citation_index = citation.index
        return page

    def _append(self, page):
        if self.active_note is not None and self.active_note not in {c.citation_id for c in page.citations}:
            self._close_note(self.active_note)
            self.active_note = None
        for citation in page.citations:
            if citation.index is not None:
                if citation.citation_id in self.notes:
                    raise PageStructureError('Duplicate footnote origin', page.page_index)
                ref = Reference(page.page_index, citation.index, _restore_mark(citation.mark), [])
                self._run('INSERT INTO refs(page,number,value) VALUES (?,?,?)', (*ref.id, pickle.dumps(ref)))
                self.notes[citation.citation_id] = ref, _Paragraphs(ref.flow_items.append, self._references)
            elif citation.origin_page_index is not None and citation.citation_id not in self.notes:
                raise PageStructureError('Footnote continuation has no open definition', page.page_index)
        unresolved = {citation.citation_id for citation in page.citations if citation.origin_page_index is None}
        for layout in sorted(page.layouts, key=lambda item: item.document_order):
            if layout.ownership == 'ignored':
                continue
            if layout.ownership == 'paragraph' or layout.citation_id in unresolved:
                if layout.citation_id in unresolved:
                    layout = deepcopy(layout)
                    layout.annotations.ownership = 'paragraph'
                    layout.annotations.citation_id = None
                    layout.annotations.citation_index = None
                self.body.append(layout)
            else:
                state = self.notes.get(layout.citation_id)
                if state is None:
                    raise PageStructureError('Citation layout has no definition', page.page_index)
                if self.active_note != layout.citation_id:
                    if self.active_note is not None:
                        self._close_note(self.active_note)
                    self.active_note = layout.citation_id
                state[1].append(layout)

    def _finish(self):
        self.body.finish()
        for identifier in list(self.notes):
            self._close_note(identifier)

    def _atomic(self, operation):
        body = deepcopy(self.body.pending)
        notes = {key: (deepcopy(ref), deepcopy(flow.pending)) for key, (ref, flow) in self.notes.items()}
        active = self.active_note
        self._events = []
        try:
            with closing(sqlite3.connect(self.database)) as db:
                with db:
                    self._transaction = db
                    operation()
        except BaseException:
            self.body.pending = body
            self.notes = {}
            for key, (ref, pending) in notes.items():
                flow = _Paragraphs(ref.flow_items.append, self._references)
                flow.pending = pending
                self.notes[key] = ref, flow
            self.active_note = active
            raise
        finally:
            self._transaction = None
        # User callbacks observe only committed units and may abort the entire
        # operation. They never participate in rollback or source recovery.
        for callback, value in self._events:
            callback(value)
        self._events = []

    def append(self, page, *, refined=False):
        page = self._bind_incoming(page, refined)
        def commit():
            # Record the semantic dependency closure as scalars, not a book DOM.
            pending_pages = {item.page_index for item in self.body.pending}
            for layout in page.layouts:
                linked = {ref.citation_page_index for ref in layout.references}
                if layout.ownership == 'paragraph':
                    if layout.source_order is None:
                        if getattr(layout.source, 'ref', None) == 'formula':
                            pending_pages.clear()
                        else:
                            pending_pages.add(layout.page_index)
                    elif layout.continues_from_previous:
                        linked.update(pending_pages)
                        pending_pages.add(layout.page_index)
                    else:
                        pending_pages = {layout.page_index}
                for index in linked:
                    self._run('INSERT OR IGNORE INTO edges VALUES (?,?)', (index, page.page_index))
            for citation in page.citations:
                if citation.origin_page_index is not None:
                    self._run('INSERT OR IGNORE INTO edges VALUES (?,?)', (citation.origin_page_index, page.page_index))
            self._append(page)
            self._run('INSERT INTO adopted VALUES (?,?)', (page.page_index, pickle.dumps(page)))
        self._atomic(commit)

    def finish(self):
        self._atomic(self._finish)
