"""Process-safe, disk-only replacement and preview transport."""
import pickle
import time
from dataclasses import dataclass
from pathlib import Path


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
