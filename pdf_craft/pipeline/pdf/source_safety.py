"""Preflight destructive text geometry against all PCEX source content.

Anchors stay transparent to text layout, but never authorize erasing images.
Preserved (including not-yet-translated) text is included in this disk index.
"""
from contextlib import closing
import hashlib
import sqlite3

from ...document.content import chapter_paths, file_content
from ...extractor.chapter.chapter import TextFlowItem, SourceTextFragment, Reference
from .models import PDFReplacementRegion


def source_identity(key):
    return hashlib.sha256(repr(key).encode()).hexdigest()


def paragraph_key(text):
    return source_identity(tuple((c.page_index, c.source_order, c.bbox)
                                 for c in text.children if isinstance(c, SourceTextFragment)))


def unsafe_source_geometry(chapters, furniture, eligible, sizes, dpi, database):
    """Return rejected regions and the page closure needed to retain paragraphs."""
    with closing(sqlite3.connect(database)) as db:
        db.execute('CREATE TABLE boxes (page, owner, kind, l, t, r, b, ord, UNIQUE(page,owner,kind,l,t,r,b))')
        db.execute('CREATE INDEX by_page ON boxes(page)')
        db.execute('CREATE INDEX by_owner ON boxes(owner)')
        def add(page, owner, kind, bbox, order=0):
            db.execute('INSERT OR IGNORE INTO boxes VALUES (?,?,?,?,?,?,?,?)',
                       (page, owner, kind, *bbox, order))
        for path in chapter_paths(chapters):
            for item in file_content(path):
                values = item.value.flow_items if isinstance(item.value, Reference) else [item.value]
                for value in values:
                    if isinstance(value, TextFlowItem):
                        owner = paragraph_key(value)
                        for child in value.children:
                            if isinstance(child, SourceTextFragment):
                                add(child.page_index, owner, 'text', child.bbox, child.source_order)
                            else:
                                add(child.page_index, owner, 'asset', child.bbox)
                    elif value is not None:
                        add(value.asset.page_index, 'asset', 'asset', value.asset.bbox)
        for number, replacement in enumerate(furniture):
            for region in replacement.source_regions():
                add(region.page_index, f'furniture-{number}', 'text', region.bbox, region.reading_order)
        eligible_owners = set(eligible) | {f'furniture-{i}' for i in range(len(furniture))}
        rejected = []
        # A large text box containing other source objects is contradictory,
        # regardless of whether those objects will receive successful translations.
        query = '''SELECT DISTINCT a.page,a.owner,a.l,a.t,a.r,a.b,a.ord FROM boxes a JOIN boxes b
                   ON a.page=b.page AND (a.owner!=b.owner OR b.kind='asset')
                   WHERE a.kind='text' AND a.l<b.r AND b.l<a.r AND a.t<b.b AND b.t<a.b
                   AND (a.r-a.l)*(a.b-a.t)>4*(b.r-b.l)*(b.b-b.t)
                   AND (min(a.r,b.r)-max(a.l,b.l))*(min(a.b,b.b)-max(a.t,b.t))
                       >0.8*(b.r-b.l)*(b.b-b.t)'''
        for page, owner, left, top, right, bottom, order in db.execute(query):
            if owner in eligible_owners:
                rejected.append(PDFReplacementRegion(page, (left, top, right, bottom), sizes[page], dpi, order))
        affected = {region.page_index for region in rejected}
        while affected:
            owners = {row[0] for page in affected for row in db.execute('SELECT owner FROM boxes WHERE page=?', (page,))
                      if row[0] != 'asset'}
            expanded = affected | {row[0] for owner in owners for row in db.execute('SELECT page FROM boxes WHERE owner=?', (owner,))}
            if expanded == affected:
                break
            affected = expanded
        return tuple(rejected), affected
