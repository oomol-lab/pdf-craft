"""Paragraph-local fitting followed by page-local normalization.

The source index knows every page dependency before translations arrive. A
paragraph touching pages 1/2 and another touching 2/3 do not make page 1 wait
for page 3: stage two never moves text between their frozen regions.
"""
# pylint: disable=protected-access
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from .feed import read_record, write_record
from .text_layout import WindowedParagraphPlanner, _window_obstacles


def plan_ready_pages(feed, filler, page_sizes, options, validated, on_error):
    """Spool first-pass contributions; normalize each page exactly once."""
    dependencies = {}
    slot_pages = {}
    for slot, first, last in feed.spans:
        indexes = tuple(range(first, last + 1))
        slot_pages[slot] = indexes
        for page in indexes:
            dependencies.setdefault(page, set()).add(slot)
    remaining = {page: set(slots) for page, slots in dependencies.items()}
    with TemporaryDirectory(prefix='pdf-craft-initial-layout-') as directory:
        root = Path(directory)
        for slot, value in feed.ready_items():
            replacement = next(iter(validated([value])), None) if value is not None else None
            if replacement is not None:
                try:
                    # Production replacements carry all immutable page obstacles,
                    # including content whose translation is still pending.
                    filler.set_layout_obstacles(_window_obstacles([replacement], page_sizes))
                    paragraph = filler.fit(replacement, page_sizes)
                    for page in slot_pages[slot]:
                        frozen = replace(paragraph, placements=tuple(
                            placement for placement in paragraph.placements if placement.page_index == page))
                        write_record(root / f'{page}-{slot}', (replacement, frozen))
                except Exception as error:
                    # Discard any partial first-pass output before recovery.
                    for page in slot_pages[slot]:
                        (root / f'{page}-{slot}').unlink(missing_ok=True)
                    on_error((replacement,), error)
            for page in slot_pages[slot]:
                remaining[page].remove(slot)
                if remaining[page]:
                    continue
                prepared = []
                for member in sorted(dependencies.pop(page)):
                    path = root / f'{page}-{member}'
                    if path.exists():
                        prepared.append(read_record(path))
                        path.unlink()
                del remaining[page]
                if not prepared:
                    continue
                replacements, paragraphs = map(list, zip(*prepared))
                planner = WindowedParagraphPlanner(filler, page_sizes, options)
                try:
                    plan = planner._plan_window(page, page, replacements, prepared=paragraphs)
                except Exception as error:
                    on_error(tuple(replacements), error)
                    continue
                yield plan
