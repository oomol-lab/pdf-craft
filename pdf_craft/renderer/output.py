"""Stage format output before replacing caller-visible files."""
import os
import shutil
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from pathlib import Path


def publish(entries, commit=None):
    """Prepare all files, then replace with rollback across Markdown resources.

    Each replacement is atomic on its destination filesystem. Multiple paths
    cannot be made atomically visible together. Existing unrelated assets are
    untouched, including when the resource directory is the output directory.
    """
    prepared = []
    committed = []
    with ExitStack() as stack:
        for source, target in entries:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                raise IsADirectoryError(target)
            directory = Path(stack.enter_context(TemporaryDirectory(prefix=".pdf-craft-", dir=target.parent)))
            staged, backup = directory / "new", directory / "previous"
            shutil.copy2(source, staged)
            existed = target.exists()
            if existed:
                shutil.copy2(target, backup)
            prepared.append((staged, target, backup, existed))
        def replace_files():
            try:
                for staged, target, backup, existed in prepared:
                    os.replace(staged, target)
                    committed.append((target, backup, existed))
            except BaseException:
                for target, backup, existed in reversed(committed):
                    if existed:
                        os.replace(backup, target)
                    else:
                        target.unlink()
                raise
        if commit is None:
            replace_files()
        else:
            commit(replace_files)
