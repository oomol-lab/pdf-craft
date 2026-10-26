"""Ready furniture work and title-dependent reuse for the PCEX scheduler."""
# pylint: disable=protected-access
from copy import deepcopy

from ..common import read_xml, save_xml
from ..extractor.chapter.chapter import TextFlowItem, SourceTextFragment
from ..extractor.toc import decode, iter_toc
from ..runtime import IO_DOMAIN
from .._failure_sources import furniture_section_locations
from .._policy import current, locations
from ..policies import FailureLocation
from .furniture_translation import (
    _as_section, _translate_position_async, _translate_page_sections_async,
    _reconcile_toc_section, _block_text,
)
from .translation_coverage import (
    FurniturePositionCoverage, FurnitureSectionCoverage, write_furniture_coverage,
)


class FurniturePlan:
    """Keep the source furniture XML; pending work and title values live on disk."""

    def __init__(self, paths, root, queue):
        self.path = root / 'furnitures.xml'
        self.coverage = root / 'coverage.xml'
        self.queue = queue
        self.root = read_xml(paths.furnitures) if paths.furnitures.exists() else None
        if paths.toc.exists():
            toc = decode(read_xml(paths.toc))
            for item in iter_toc(toc.content):
                queue.run('INSERT OR IGNORE INTO titles(id,page,source_order) VALUES (?,?,?)',
                          (item.id, item.page_index, item.order))
        if self.root is None:
            return
        for pattern in self.root.findall('patterns/pattern'):
            for position in pattern.findall('position'):
                dependency = position.get('toc_id') if position.get('folio_style') is None else None
                queue.add('furniture', ('position', pattern.get('id', ''), position.get('id', ''),
                                        pattern.get('kind', ''), deepcopy(position)), dependency=dependency)
        for page in self.root.findall('pages/page'):
            ordinary = []
            for section in page.findall('section'):
                if len(section):
                    continue
                if section.get('toc_id') is not None:
                    queue.add('furniture', ('reuse', page.get('index', ''), deepcopy(section)),
                              dependency=section.get('toc_id'))
                else:
                    ordinary.append((deepcopy(section), _as_section(page.get('index', ''), section.get('det', ''), section.text or '')))
            if ordinary:
                queue.add('furniture', ('sections', page.get('index', ''), ordinary))

    def titles(self, target):
        for item in target.flow_items:
            if not isinstance(item, TextFlowItem):
                continue
            for fragment in item.children:
                if isinstance(fragment, SourceTextFragment):
                    text = None if getattr(item, '_translation_missing', False) else _block_text(fragment)
                    self.queue.run('UPDATE titles SET resolved=1,value=? WHERE page=? AND source_order=?',
                                   (text or None, fragment.page_index, fragment.source_order))

    async def execute(self, payload, transformer):
        kind, *args = payload
        dependency = args[-1].get('toc_id') if kind in ('position', 'reuse') else None
        rows = await IO_DOMAIN.run(self.queue.run, 'SELECT id,value FROM titles WHERE id=? AND resolved=1 AND value IS NOT NULL', (dependency,))
        titles = dict(rows)
        if kind == 'position':
            pattern_id, position_id, pattern_kind, position = args
            assert self.root is not None
            pages = tuple(dict.fromkeys(int(page.get('index', '0')) for page in self.root.findall('pages/page')
                if any(link.get('pattern_id') == pattern_id and link.get('position_id') == position_id
                       for link in page.findall('section/association'))))
            target = FailureLocation('template', source=current().source, member_path='furnitures.xml',
                                     unit_id=f'{pattern_id}:{position_id}', page_indexes=pages, original=position.text)
            impact = furniture_section_locations(tuple(
                _as_section(page.get('index', '0'), section.get('det', ''), section.text or '')
                for page in self.root.findall('pages/page') for section in page.findall('section')
                if section.get('det') and any(link.get('pattern_id') == pattern_id and link.get('position_id') == position_id
                                             for link in section.findall('association'))))
            with locations((target,), affected=impact):
                state = await _translate_position_async(position, pattern_id, position_id, pattern_kind, titles, transformer)
            return kind, pattern_id, position_id, position, state
        if kind == 'reuse':
            page, section = args
            return kind, page, section, _reconcile_toc_section(section, titles)
        page, entries = args
        states = await _translate_page_sections_async(page, entries, transformer)
        return kind, page, entries, states

    def finish(self):
        if self.root is None:
            return
        positions, sections = [], []
        import pickle
        for (data,) in self.queue.run('SELECT result FROM jobs WHERE kind="furniture" ORDER BY id'):
            kind, *values = pickle.loads(data)
            if kind == 'position':
                pattern, position, target, state = values
                element = self.root.find(f'patterns/pattern[@id="{pattern}"]/position[@id="{position}"]')
                assert element is not None
                element.attrib.clear()
                element.attrib.update(target.attrib)
                element.text = target.text
                positions.append(FurniturePositionCoverage(pattern, position, state))
            elif kind == 'reuse':
                page, target, state = values
                element = self.root.find(f'pages/page[@index="{page}"]/section[@det="{target.get("det")}"]')
                assert element is not None
                element.text = target.text
                sections.append(FurnitureSectionCoverage(int(page), target.get('det', ''), state))
            else:
                page, entries, states = values
                for (target, _), (_, det, state) in zip(entries, states, strict=True):
                    element = self.root.find(f'pages/page[@index="{page}"]/section[@det="{det}"]')
                    assert element is not None
                    element.text = target.text
                    sections.append(FurnitureSectionCoverage(int(page), det, state))
        save_xml(self.root, self.path)
        write_furniture_coverage(self.coverage, positions, sections)
        self.queue.run('DELETE FROM jobs WHERE kind="furniture"')
