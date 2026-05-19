import tempfile
from pathlib import Path


def assert_within_root(path: Path, root: Path) -> None:
    """Raise ValueError if path does not resolve to a location inside root."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError(
            f"Path '{path}' must be within the safe root '{root}'"
        )


class EnsureFolder:
    def __init__(self, path: Path | None, safe_root: Path | None = None):
        self._path = path
        self._safe_root = safe_root
        self._temp: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        if self._path is None:
            self._temp = tempfile.TemporaryDirectory()
            self._path = Path(self._temp.name)
        else:
            if self._safe_root is not None:
                assert_within_root(self._path, self._safe_root)
            self._path.mkdir(parents=True, exist_ok=True)
        return self._path

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._temp is not None:
            try:
                self._temp.cleanup()
            finally:
                self._temp = None
        return False
