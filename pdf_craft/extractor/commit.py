"""Disk-backed projection of adopted extraction semantics.

OCR is immutable evidence. This private projection is rebuilt per operation and
never used to rerun paragraph analysis: its only consumers are TOC/furniture.
"""
from pathlib import Path
import shutil
import sqlite3
from contextlib import closing
from xml.etree.ElementTree import Element
from tempfile import TemporaryDirectory

from ..common import read_xml, save_xml
from ..document.content import chapter_paths


def project_adopted(chapters: Path, evidence: Path, destination: Path, printed_pages):
    """Project final ownership/heading anchors without renumbering source slots."""
    destination.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(destination / 'projection.sqlite')) as db:
        db.execute('CREATE TABLE fields(page INTEGER, position INTEGER, bbox TEXT, text TEXT, heading INTEGER)')
        db.execute('CREATE INDEX by_page ON fields(page)')
        for path in chapter_paths(chapters):
            root = read_xml(path)
            body_texts = set(root.findall('./flow/text'))
            for text in root.iter('text'):
                fragments = list(text.findall('fragment'))
                first = fragments[0] if fragments else None
                heading = text in body_texts and text.get('role') == 'heading'
                title = ''.join(''.join(f.itertext()) for f in fragments) if heading else ''
                for fragment in fragments:
                    db.execute('INSERT INTO fields VALUES (?,?,?,?,?)', (
                        int(fragment.get('page_index', '0')), int(fragment.get('source_order', '0')),
                        fragment.get('bbox'), title if heading and fragment is first else ''.join(fragment.itertext()),
                        int(heading and fragment is first)))
            for asset in root.iter('asset'):
                if asset.get('page_index') and asset.get('bbox'):
                    db.execute('INSERT INTO fields VALUES (?,NULL,?,?,0)',
                               (int(asset.get('page_index', '0')), asset.get('bbox'), ''.join(asset.itertext())))
        for path in evidence.glob('page_*.xml'):
            root = read_xml(path)
            index = int(root.get('index', '0'))
            if index not in printed_pages:
                rows = db.execute('SELECT position,bbox,text,heading FROM fields WHERE page=?', (index,)).fetchall()
                by_position = {position: (text, heading) for position, _, text, heading in rows if position is not None}
                assets = {box for position, box, _, _ in rows if position is None}
                # Source order is page-wide (body, then footnotes). Keep empty
                # slots so a promoted footnote heading retains its real identity.
                layouts = root.findall('./body/layout') + root.findall('./footnotes/layout')
                for child in list(root):
                    root.remove(child)
                body = Element('body')
                root.append(body)
                for position, layout in enumerate(layouts):
                    body.append(layout)
                    value = by_position.get(position)
                    if value is not None:
                        text, heading = value
                        layout.set('ref', 'title' if heading else 'text')
                        layout.text = text
                    elif layout.get('det') not in assets:
                        layout.set('ref', 'ignored')
                        layout.text = None
                original_boxes = {layout.get('det') for layout in layouts}
                for box in sorted(assets - original_boxes):
                    body.append(Element('layout', {'ref': 'image', 'det': box}))
            save_xml(root, destination / path.name)
        db.commit()
    sizes = evidence / 'page_pixel_sizes.json'
    if sizes.exists():
        shutil.copyfile(sizes, destination / sizes.name)


def partition_adopted(source: Path, destination: Path, toc):
    """Assign final chapter identities to already accepted flow, without repair."""
    from .chapter.chapter import decode, encode, search_references_in_chapter, TextFlowItem
    from .chapter.generation import _partition_chapters
    from .chapter.analyse_level import analyse_chapter_internal_levels
    # A chapter is the existing serialization unit. Do not additionally retain
    # all chapters/definitions while repartitioning a large book.
    import pickle
    destination.mkdir(parents=True, exist_ok=True)
    for path in destination.glob('chapter_*.xml'):
        path.unlink()
    with TemporaryDirectory(prefix='pdf-craft-partition-') as directory, closing(sqlite3.connect(Path(directory) / 'partition.sqlite')) as db:
        db.executescript('CREATE TABLE refs(page,number,value,used DEFAULT 0,PRIMARY KEY(page,number));'
                         'CREATE TABLE chapters(position INTEGER PRIMARY KEY,path,min_page);')
        def items():
            for path in chapter_paths(source):
                chapter = decode(read_xml(path))
                for ref in search_references_in_chapter(chapter):
                    db.execute('INSERT OR IGNORE INTO refs(page,number,value) VALUES (?,?,?)',
                               (*ref.id, pickle.dumps(ref)))
                yield from chapter.flow_items
        for position, chapter in enumerate(_partition_chapters(items(), toc, assembled=True)):
            for ref in search_references_in_chapter(chapter):
                db.execute('UPDATE refs SET used=1 WHERE page=? AND number=?', ref.id)
            chapter = analyse_chapter_internal_levels(chapter)
            tail = 'head' if chapter.id is None else str(chapter.id)
            path = destination / f'chapter_{tail}.xml'
            save_xml(encode(chapter), path)
            minimum = min((child.page_index for item in chapter.flow_items
                           if isinstance(item, TextFlowItem) for child in item.children), default=None)
            db.execute('INSERT INTO chapters VALUES (?,?,?)', (position, str(path), minimum))
        for page, value in db.execute('SELECT page,value FROM refs WHERE used=0 ORDER BY page,number'):
            row = db.execute('SELECT path FROM chapters WHERE min_page<=? ORDER BY position DESC LIMIT 1', (page,)).fetchone()
            row = row or db.execute('SELECT path FROM chapters ORDER BY position LIMIT 1').fetchone()
            if row is None:
                from .chapter.chapter import Chapter
                path = destination / 'chapter_head.xml'
                chapter = Chapter(None, -1, [])
                db.execute('INSERT INTO chapters VALUES (0,?,NULL)', (str(path),))
            else:
                path = Path(row[0])
                chapter = decode(read_xml(path))
            chapter.definitions.append(pickle.loads(value))
            save_xml(encode(chapter), path)
