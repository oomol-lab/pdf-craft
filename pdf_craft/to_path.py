from os import PathLike
from pathlib import Path


def to_path(path_like: PathLike | str) -> Path:
    path = Path(path_like)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()
