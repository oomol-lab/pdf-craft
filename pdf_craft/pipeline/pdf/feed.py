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
