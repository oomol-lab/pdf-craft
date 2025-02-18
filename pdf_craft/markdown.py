import io
import os

from hashlib import sha256
from PIL.Image import Image
from .pdf import Text, TextKind, PDFItem

class MarkDownWriter:
  def __init__(self, md_path: str, assets_path: str, encoding: str | None):
    self._assets_path: str = assets_path
    self._abs_assets_path: str = os.path.abspath(os.path.join(md_path, "..", assets_path))
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
    if isinstance(item, Text):
      self._write_text(item)
    elif isinstance(item, Image):
      self._write_image(item)

  def _write_text(self, text: Text) -> None:
    if text.kind == TextKind.TITLE:
      self._file.write("# " + text.text)
      self._file.write("\n\n")
    elif text.kind == TextKind.PLAIN_TEXT:
      self._file.write(text.text)
      self._file.write("\n\n")

  def _write_image(self, image: Image) -> None:
    os.makedirs(self._abs_assets_path, exist_ok=True)
    hash = sha256()
    hash.update(image.tobytes())
    file_name = f"{hash.hexdigest()}.png"
    file_path = os.path.join(self._abs_assets_path, file_name)
    relative_path = os.path.join(self._assets_path, file_name)

    if not os.path.exists(file_path):
      image.save(file_path, "PNG")

    self._file.write(f"![]({relative_path})")
    self._file.write("\n\n")