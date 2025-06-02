from pathlib import Path
from zipfile import ZipFile
from .types import TableRender, LaTeXRender


class Context:
  def __init__(
        self,
        file: ZipFile,
        assets_path: Path | None,
        table_render: TableRender,
        latex_render: LaTeXRender,
      ) -> None:

    self._assets_path: Path | None = assets_path
    self._file: ZipFile = file
    self._table_render: TableRender = table_render
    self._latex_render: LaTeXRender = latex_render
    self._used_file_names: dict[str, str] = {}

  @property
  def file(self) -> ZipFile:
    return self._file

  @property
  def table_render(self) -> TableRender:
    return self._table_render

  @property
  def latex_render(self) -> LaTeXRender:
    return self._latex_render

  def use_asset(self, file_name: str, media_type: str) -> None:
    self._used_file_names[file_name] = media_type

  def add_asset(self, file_name: str, media_type: str, data: bytes) -> None:
    if file_name in self._used_file_names:
      return

    self._used_file_names[file_name] = media_type
    self._file.writestr(
      zinfo_or_arcname="OEBPS/assets/" + file_name,
      data=data,
    )

  @property
  def used_files(self) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for file_name in sorted(list(self._used_file_names.keys())):
      media_type = self._used_file_names[file_name]
      files.append((file_name, media_type))
    return files

  def add_used_asset_files(self) -> None:
    if self._assets_path is None:
      return
    for file in sorted(self._assets_path.iterdir()):
      if file.name not in self._used_file_names:
        continue
      self._file.write(
        filename=file,
        arcname="OEBPS/assets/" + file.name,
      )