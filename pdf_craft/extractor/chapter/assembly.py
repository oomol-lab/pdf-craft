"""Incremental adopted-page assembly; only unfinished natural units stay in RAM."""
# pylint: disable=protected-access
import pickle
import sqlite3
from contextlib import closing
from copy import deepcopy
from dataclasses import replace

from .chapter import Reference, SourceAsset, TextFlowItem, StandaloneAsset, DisplayFormula, _encode_flow, _encode_reference
from .page_analysis import _restore_layouts
from .analysis_content import _restore_mark
from .analysis_types import PageStructureError
from .asset_text import materialize_asset_text


class _Paragraphs:
    def __init__(self, emit, references):
        self.pending = []
        self.emit, self.references = emit, references

    def append(self, layout):
        is_text = layout.source_order is not None
        if is_text and not layout.continues_from_previous:
            self.finish()
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
            item.annotations.continues_from_previous = index > 0
            item.annotations.continues_to_next = index + 1 < len(texts)
        references = self.references(self.pending)
        offset = 0
        for item in _restore_layouts(self.pending, references, check_connections=False):
            # Restoration preserves every text fragment/asset, including empty
            # fragments. Carry its source ordinal rather than inferring identity
            # from coordinates or the order in which independent streams close.
            document_order = self.pending[offset].document_order
            offset += len(item.children) if isinstance(item, TextFlowItem) else 1
            if isinstance(item, SourceAsset):
                item = DisplayFormula(item) if item.ref == 'formula' else StandaloneAsset(item)
            self.emit(item, document_order)
        self.pending.clear()


class PageAssembly:
    """Single ordered writer; complete bodies/references share the final disk spool."""
    def __init__(self, database, *, on_body=None, on_footnote=None):
        self.database = database
        self.body_callback, self.note_callback = on_body, on_footnote
        self.notes = {}
        self.unresolved = {}
        self.active_note = None
        self._transaction = None
        self._events = []
        self.body = _Paragraphs(self._body, self._references)
        self._run('CREATE TABLE adopted (page INTEGER PRIMARY KEY,value BLOB)')
        self._run('CREATE TABLE edges(left_page INTEGER,right_page INTEGER,PRIMARY KEY(left_page,right_page))')
        self._run('CREATE TABLE bodies(page INTEGER,position INTEGER,value BLOB,unresolved INTEGER,document_order INTEGER)')
        self._run('CREATE INDEX body_delivery ON bodies(unresolved,document_order)')
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

    def _body(self, item, document_order):
        source = item.asset if isinstance(item, (StandaloneAsset, DisplayFormula)) else item.children[0]
        page, position = source.page_index, document_order
        self._run('INSERT INTO bodies VALUES (?,?,?,0,?)', (page, position, pickle.dumps(item), document_order))

    def _deliver_bodies(self):
        if self.body_callback is None:
            return
        # Independent unresolved notes must not overtake an earlier open body
        # paragraph (or vice versa). Ready units wait on disk, not in a book DOM.
        starts = [flow.pending[0].document_order for flow in (self.body, *self.unresolved.values()) if flow.pending]
        frontier = ' AND document_order < ?' if starts else ''
        parameters = (min(starts),) if starts else ()
        while rows := self._run('SELECT rowid,value FROM bodies WHERE unresolved=0' + frontier
                               + ' ORDER BY document_order,rowid LIMIT 128', parameters):
            for rowid, value in rows:
                self.body_callback(_encode_flow(pickle.loads(value)))
                self._run('UPDATE bodies SET unresolved=2 WHERE rowid=?', (rowid,))

    def _note_paragraphs(self, reference):
        return _Paragraphs(lambda item, _order: reference.flow_items.append(item), self._references)

    def _close_note(self, identifier):
        ref, paragraphs = self.notes.pop(identifier)
        paragraphs.finish()
        self._run('UPDATE refs SET value=? WHERE page=? AND number=?', (pickle.dumps(ref), *ref.id))
        if self.note_callback is not None:
            self._events.append((self.note_callback, _encode_reference(ref)))

    def _bind_incoming(self, page):
        """Bind local handles to output cursors, without opening adopted pages.

        A missing cursor starts an explicitly unbound stream from the available
        content. It does not claim a successful cross-page join or discard text.
        Unbound tails stay in their own narrative stream, never in body prose.
        """
        page = deepcopy(page)
        cursor = self.active_note or next(reversed(self.unresolved), None)
        # Handles are output entities, independent of model/algorithm aliases.
        aliases = {}
        for citation in sorted(page.citations, key=lambda item: item.stream_order):
            incoming = citation.continues_previous
            alias = citation.citation_id
            if citation.index is None and incoming and cursor is not None:
                citation.citation_id = cursor
                citation.origin_page_index = self.notes[cursor][0].page_index if cursor in self.notes else None
            elif citation.index is not None:
                citation.citation_id = f'citation-{page.page_index}-{citation.index}'
                citation.origin_page_index = page.page_index
            else:
                citation.citation_id = f'unbound-{page.page_index}-{citation.stream_order}'
                citation.origin_page_index = None
            aliases[alias] = citation.citation_id, citation.index
            cursor = citation.citation_id
        for layout in page.layouts:
            if layout.citation_id in aliases:
                layout.annotations.citation_id, layout.annotations.citation_index = aliases[layout.citation_id]
        return page

    def _append(self, page):
        page = materialize_asset_text(page)
        unresolved = {citation.citation_id for citation in page.citations if citation.state == 'unresolved'}
        for identifier in list(self.unresolved):
            if identifier not in unresolved:
                self.unresolved.pop(identifier).finish()
        starts = {}
        for citation in sorted(page.citations, key=lambda item: item.stream_order):
            identifier = citation.citation_id
            if identifier not in unresolved or identifier in self.unresolved:
                continue
            self.unresolved[identifier] = _Paragraphs(self._body, self._references)
            # An unresolved stream has no adopted predecessor at its first
            # appearance. Preserve its text without borrowing a body predecessor.
            starts[identifier] = next((layout for layout in sorted(page.layouts, key=lambda item: item.document_order)
                                       if layout.citation_id == identifier and layout.source_order is not None), None)
        if self.active_note is not None and self.active_note not in {c.citation_id for c in page.citations}:
            self._close_note(self.active_note)
            self.active_note = None
        for citation in page.citations:
            if citation.index is not None:
                if citation.citation_id in self.notes:
                    raise PageStructureError('Duplicate footnote origin', page.page_index)
                ref = Reference(page.page_index, citation.index, _restore_mark(citation.mark), [])
                self._run('INSERT INTO refs(page,number,value) VALUES (?,?,?)', (*ref.id, pickle.dumps(ref)))
                self.notes[citation.citation_id] = ref, self._note_paragraphs(ref)
        independent = {}
        for layout in sorted(page.layouts, key=lambda item: item.document_order):
            if layout.ownership == 'ignored':
                continue
            if layout.citation_id in unresolved:
                if layout is starts.get(layout.citation_id):
                    layout = replace(layout, annotations=replace(layout.annotations, continues_from_previous=False))
                self.unresolved[layout.citation_id].append(layout)
            elif layout.annotations.independent_stream is not None:
                stream = independent.setdefault(layout.annotations.independent_stream,
                                                 _Paragraphs(self._body, self._references))
                stream.append(layout)
            elif layout.ownership == 'paragraph':
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

        for stream in independent.values():
            stream.finish()

    def _finish(self):
        self.body.finish()
        for flow in self.unresolved.values():
            flow.finish()
        self.unresolved.clear()
        for identifier in list(self.notes):
            self._close_note(identifier)

    def _atomic(self, operation):
        body = deepcopy(self.body.pending)
        unresolved = {key: deepcopy(flow.pending) for key, flow in self.unresolved.items()}
        notes = {key: (deepcopy(ref), deepcopy(flow.pending)) for key, (ref, flow) in self.notes.items()}
        active = self.active_note
        self._events = []
        succeeded = False
        try:
            with closing(sqlite3.connect(self.database)) as db:
                with db:
                    self._transaction = db
                    operation()
            succeeded = True
        finally:
            self._transaction = None
            if not succeeded:
                self.body.pending = body
                self.unresolved = {}
                for key, pending in unresolved.items():
                    flow = _Paragraphs(self._body, self._references)
                    flow.pending = pending
                    self.unresolved[key] = flow
                self.notes = {}
                for key, (ref, pending) in notes.items():
                    flow = self._note_paragraphs(ref)
                    flow.pending = pending
                    self.notes[key] = ref, flow
                self.active_note = active
                self._events = []
        # User callbacks observe only committed units and may abort the entire
        # operation. They never participate in rollback or source recovery.
        for callback, value in self._events:
            callback(value)
        self._events = []
        self._deliver_bodies()

    def _commit_page(self, page):
        page = self._bind_incoming(page)
        # Record the semantic dependency closure as scalars, not a book DOM.
        pending_streams = {None: {item.page_index for item in self.body.pending}}
        pending_streams.update({key: {item.page_index for item in flow.pending}
                                for key, flow in self.unresolved.items()})
        unresolved = {citation.citation_id for citation in page.citations if citation.state == 'unresolved'}
        for layout in page.layouts:
            linked = {ref.citation_page_index for ref in layout.references}
            if ((layout.ownership == 'paragraph' and layout.annotations.independent_stream is None
                 and layout.annotations.asset_attachment is None) or layout.citation_id in unresolved):
                key = layout.citation_id if layout.citation_id in unresolved else None
                pending_pages = pending_streams.setdefault(key, set())
                if layout.source_order is None:
                    if getattr(layout.source, 'ref', None) == 'formula':
                        pending_pages.clear()
                    else:
                        pending_pages.add(layout.page_index)
                elif layout.continues_from_previous:
                    linked.update(pending_pages)
                    pending_pages.add(layout.page_index)
                else:
                    pending_pages.clear()
                    pending_pages.add(layout.page_index)
            for index in linked:
                self._run('INSERT OR IGNORE INTO edges VALUES (?,?)', (index, page.page_index))
        for citation in page.citations:
            if citation.origin_page_index is not None:
                self._run('INSERT OR IGNORE INTO edges VALUES (?,?)', (citation.origin_page_index, page.page_index))
        self._append(page)
        self._run('INSERT INTO adopted VALUES (?,?)', (page.page_index, pickle.dumps(page)))

    @staticmethod
    def validate(page):
        """The only semantic gate receives the complete current page."""
        from .page_validation import validate_page
        validate_page(page)

    def append(self, page):
        self.validate(page)
        self._atomic(lambda: self._commit_page(page))

    def finish(self):
        self._atomic(self._finish)
