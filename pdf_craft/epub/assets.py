import os
from zipfile import ZipFile


class Assets:
  def __init__(self, assets_path: str | None, file: ZipFile) -> None:
    if assets_path is not None and not os.path.exists(assets_path):
      assets_path = None
    self._assets_path: str | None = assets_path
    self._file: ZipFile = file
    self._used_file_names: set[str] = set()
    self._asset_files: list[str] = []

    if assets_path is not None:
      for file in os.listdir(assets_path):
        if not file.startswith("."):
          self._asset_files.append(file)
      self._asset_files.sort()

  def use_asset(self, file_name: str) -> None:
    self._used_file_names.add(file_name)

  def add_asset(self, file_name: str, data: bytes) -> None:
    if file_name in self._used_file_names:
      return

    self._used_file_names.add(file_name)
    self._file.writestr(
      zinfo_or_arcname="OEBPS/assets/" + file_name,
      data=data,
    )

  @property
  def used_file_names(self) -> list[str]:
    file_names = list(self._used_file_names)
    file_names.sort()
    return file_names

  def add_used_asset_files(self) -> None:
    if self._assets_path is None:
      return
    for file_name in sorted(os.listdir(self._assets_path)):
      if file_name not in self._used_file_names:
        continue
      file_path = os.path.join(self._assets_path, file_name)
      self._file.write(
        filename=file_path,
        arcname="OEBPS/assets/" + file_name,
      )