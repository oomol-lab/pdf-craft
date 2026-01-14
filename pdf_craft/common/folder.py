import tempfile
from pathlib import Path


class EnsureFolder:
    def __init__(self, path: Path | None):
        self._path = path
        self._temp: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        if self._path is None:
            self._temp = tempfile.TemporaryDirectory()
            self._path = Path(self._temp.name)
        else:
            self._path.mkdir(parents=True, exist_ok=True)
        return self._path

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._temp is not None:
            try:
                self._temp.cleanup()
            finally:
                self._temp = None
        return False
