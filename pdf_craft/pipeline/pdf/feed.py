"""Process-safe, disk-only replacement and preview transport."""
import pickle
import time
from dataclasses import dataclass, fields
from pathlib import Path

from ...document.paragraph import ParagraphContent, SourceRegion
from .models import PDFReplacement


@dataclass(frozen=True)
class _PreviewReplacement(PDFReplacement):
    """Private process transport; preview state is not a replacement input."""
    preview: ParagraphContent[SourceRegion] | None = None
    preview_is_footnote: bool = False
    preview_index: int = -1

    @classmethod
    def attach(cls, replacement: PDFReplacement, preview, is_footnote: bool, index: int):
        return cls(**{field.name: getattr(replacement, field.name) for field in fields(PDFReplacement)},
                   preview=preview, preview_is_footnote=is_footnote, preview_index=index)


def write_record(path: Path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('wb') as file:
        pickle.dump(value, file, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(path)


def read_record(path):
    with path.open('rb') as file:
        return pickle.load(file)


@dataclass(frozen=True)
class ReplacementFeed:
    root: Path
    count: int
    windows: tuple[tuple[int, ...], ...] = ()

    def __iter__(self):
        for index in range(self.count):
            path = self.root / str(index)
            while not path.exists():
                error = self.root / 'error'
                if error.exists():
                    raise RuntimeError(read_record(error))
                time.sleep(.01)
            value = read_record(path)
            if value is not None:
                yield value

    def first(self):
        if self.windows:
            return next((value for batch in self.ready_batches() for value in batch), None)
        return next(iter(self), None)

    def ready_batches(self):
        """Wait only for a connected page window, not earlier unrelated pages."""
        pending = list(self.windows)
        while pending:
            error = self.root / 'error'
            if error.exists():
                raise RuntimeError(read_record(error))
            ready = next((slots for slots in pending if all((self.root / str(slot)).exists() for slot in slots)), None)
            if ready is None:
                time.sleep(.01)
                continue
            pending.remove(ready)
            yield [value for slot in ready if (value := read_record(self.root / str(slot))) is not None]


def dependency_windows(entries):
    """Connected source-page spans, independent of translated content arrival."""
    groups, slots, last = [], [], 0
    for slot, first, end in sorted(entries, key=lambda entry: (entry[1], entry[0])):
        if slots and first > last:
            groups.append(tuple(slots))
            slots = []
        slots.append(slot)
        last = max(last, end)
    if slots:
        groups.append(tuple(slots))
    return tuple(groups)
