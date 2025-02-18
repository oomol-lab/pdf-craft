import io

from .pdf import Text, TextKind, PDFItem

class MarkDownWriter:
  def __init__(self, md_path: str, assets_path: str, encoding: str | None):
    self._assets_path: str = assets_path
    self._file: io.TextIOWrapper = open(md_path, "w", encoding=encoding)

  def __enter__(self) -> "MarkDownWriter":
    return self

  def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    self.close()

  def flush(self) -> None:
    self._file.flush()

  def close(self) -> None:
    self._file.close()

  def write(self, item: PDFItem) -> None:
    if not isinstance(item, Text):
      return
    if item.kind == TextKind.TITLE:
      self._file.write("# " + item.text)
      self._file.write("\n\n")
    elif item.kind == TextKind.PLAIN_TEXT:
      self._file.write(item.text)
      self._file.write("\n\n")