import os
import zipfile
from tempfile import NamedTemporaryFile
from pathlib import Path
from typing import IO

_BUFFER_SIZE = 8192  # 8KB


class ReadOnlyZip:
    """The input side of an EPUB archive; never creates an output file."""

    def __init__(self, source_path: Path) -> None:
        self._source_zip = zipfile.ZipFile(source_path, "r")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        self._source_zip.close()
        return False

    def list_files(self, prefix_path: Path | None = None) -> list[Path]:
        all_files = self._source_zip.namelist()
        if prefix_path is None:
            return [Path(f) for f in all_files]
        prefix = prefix_path.as_posix()
        if not prefix.endswith("/"):
            prefix += "/"
        return [Path(f) for f in all_files if f.startswith(prefix)]

    def read(self, path: Path) -> IO[bytes]:
        return self._source_zip.open(path.as_posix(), "r")


class Zip(ReadOnlyZip):
    def __init__(self, source_path: Path, target_path: Path) -> None:
        source_zip: zipfile.ZipFile | None = None
        target_zip: zipfile.ZipFile | None = None
        self._target_path = target_path
        self._temporary_path: Path | None = None
        try:
            super().__init__(source_path)
            source_zip = self._source_zip
            with NamedTemporaryFile(prefix=f".{target_path.name}.", suffix=".tmp",
                                    dir=target_path.parent, delete=False) as temporary:
                self._temporary_path = Path(temporary.name)
            target_zip = zipfile.ZipFile(self._temporary_path, "w", zipfile.ZIP_DEFLATED)
        except Exception:
            if source_zip:
                source_zip.close()
            if target_zip:
                target_zip.close()
            if self._temporary_path is not None:
                self._temporary_path.unlink(missing_ok=True)
            raise
        self._source_zip: zipfile.ZipFile = source_zip
        self._target_zip: zipfile.ZipFile = target_zip
        self._processed_files: set[Path] = set()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        try:
            try:
                if _exc_type is None:
                    for source_info in self._source_zip.infolist():
                        if (not source_info.is_dir()
                                and Path(source_info.filename) not in self._processed_files):
                            self._migrate_entry(source_info)
            finally:
                self._target_zip.close()
                self._source_zip.close()
            if _exc_type is None:
                assert self._temporary_path is not None
                os.replace(self._temporary_path, self._target_path)
        finally:
            if self._temporary_path is not None:
                self._temporary_path.unlink(missing_ok=True)
        return False

    def migrate(self, path: Path):
        self._migrate_entry(self._source_zip.getinfo(path.as_posix()))
        self._processed_files.add(path)

    def _migrate_entry(self, source_info: zipfile.ZipInfo) -> None:
        # ZIP entry identity must survive filesystem path normalization.
        with self._source_zip.open(source_info, "r") as source_file:
            content = source_file.read()
        self._target_zip.writestr(
            zinfo_or_arcname=source_info,
            data=content,
            compress_type=source_info.compress_type,
        )

    def replace(self, path: Path) -> IO[bytes]:
        self._processed_files.add(path)
        return self._target_zip.open(path.as_posix(), "w")
