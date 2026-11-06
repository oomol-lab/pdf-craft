"""Coordinate logical owners of one indivisible physical OCR rectangle.

Only original source identity joins owners. Equal coordinates alone never do.
Completeness is checked against persisted source evidence, including excluded
parts. A group is settled locally, without a document-wide dependency graph.
"""
from collections import defaultdict
from dataclasses import replace

from .feed import _PreviewReplacement


class SharedSources:
    def __init__(self):
        self.sources = defaultdict(list)
        self.groups = {}
        self.by_key = {}
        self.pending = {}
        self.preview_slots = set()
        self.preserved_pages = set()
        self.primary_planned = False

    def observe(self, key, fragments, prototype):
        for fragment in fragments:
            physical = fragment.page_index, fragment.source_order
            self.sources[physical].append((key, fragment, prototype, len(fragments)))

    def finish(self):
        for physical, members in self.sources.items():
            sliced = len(members) > 1 or any(
                f.source_start or f.source_text is not None and f.source_end != len(f.source_text)
                for _, f, _, _ in members)
            if not sliced:
                continue
            ordered = sorted(members, key=lambda member: member[1].source_start)
            cursor, complete = 0, True
            evidence = members[0][1].source_text
            for _, fragment, prototype, count in ordered:
                complete &= (count == 1 and prototype is not None and evidence is not None
                             and fragment.source_text == evidence and fragment.source_start == cursor
                             and fragment.bbox == members[0][1].bbox)
                cursor = fragment.source_end
            complete &= evidence is not None and cursor == len(evidence)
            complete &= len({(value.layout_ref, value.layout_level) for _, _, value, _ in members
                             if value is not None}) == 1
            group = tuple(dict.fromkeys(key for key, _, _, _ in members))
            self.groups[physical] = group, bool(complete)
            for key in group:
                self.by_key[key] = physical
        # A paragraph crossing several shared sources cannot be independently
        # drawn in each whole rectangle. Preserve it rather than duplicating it.
        for key, physical in list(self.by_key.items()):
            if len(key) != 1:
                group, _ = self.groups[physical]
                self.groups[physical] = group, False
        self.sources.clear()

    def accept(self, key, value, slot, preview, notes):
        """Return a settled group's slot writes, or None while it is pending."""
        physical = self.by_key.get(key)
        if physical is None:
            self.primary_planned |= value is not None
            return [(slot, value, preview, notes)]
        group, complete = self.groups[physical]
        if not complete:
            self._preserve(value)
            return [(slot, None, preview, notes)]
        self.pending[key] = value, slot, preview, notes
        if not all(member in self.pending for member in group):
            return None
        records = [self.pending.pop(member) for member in group]
        if any(value is None or value.inline_formulas for value, _, _, _ in records):
            for value, *_ in records:
                self._preserve(value)
            return [(index, None, content, channel) for _, index, content, channel in records]
        self.primary_planned = True
        values = [record[0] for record in records]
        # One Qt paragraph owns the entire original rectangle. Newline
        # separators retain logical paragraph boundaries inside that rectangle.
        combined = replace(values[0], text='\u2028'.join(value.text for value in values),
                           inline_formulas=tuple(formula for value in values for formula in value.inline_formulas))
        owners, offset = [], 0
        for value, index, content, channel in records:
            owners.append((index, channel, content, offset, offset + len(value.text)))
            offset += len(value.text) + 1
        leader = records[0][1]
        if any(content is not None for _, _, content, _, _ in owners):
            self.preview_slots.update(index for index, _, _, _, _ in owners)
            content = next(content for _, _, content, _, _ in owners if content is not None)
            combined = _PreviewReplacement.attach(combined, content, records[0][3], leader)
            combined = replace(combined, shared_previews=tuple(owners))
        # Non-leading geometry slots settle without a second draw. Their
        # preview is exported by the single group's actual Qt placement.
        return [(leader, combined, None, False),
                *[(index, None, None, False) for _, index, _, _ in records[1:]]]

    def _preserve(self, value):
        if value is not None:
            self.preserved_pages.update(region.page_index for region in value.source_regions())

    def ensure_usable(self):
        """Preserving every intended primary fill is not a successful patch."""
        if self.preserved_pages and not self.primary_planned:
            from ...error import NoUsableFillPagesError
            raise NoUsableFillPagesError(tuple(sorted(self.preserved_pages)))
