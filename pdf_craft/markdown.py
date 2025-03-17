import io
import os

from hashlib import sha256
from typing import Iterable
from .pdf import Text, TextKind, TextBlock, AssetBlock, Block


class MarkDownWriter:
  def __init__(self, md_path: str, assets_path: str, encoding: str | None):
    self._assets_path: str = assets_path
    self._abs_assets_path: str = os.path.abspath(os.path.join(md_path, "..", assets_path))
    self._file: io.TextIOWrapper = open(md_path, "w", encoding=encoding)
    self._texts_buffer: list[Text] = []

  def __enter__(self) -> "MarkDownWriter":
    return self

  def __exit__(self, exc_type, exc_val, exc_tb) -> None:
    self.close()

  def flush(self) -> None:
    self._file.flush()

  def close(self) -> None:
    self._close_texts_buffer()
    self._file.close()

  def write(self, block: Block) -> None:
    if isinstance(block, TextBlock):
      if block.kind == TextKind.TITLE:
        self._close_texts_buffer()
        self._file.write("# ")
        self._write_text_contents(block.texts)
        self._file.write("\n\n")
      elif block.kind == TextKind.PLAIN_TEXT:
        self._write_plain_text(block)
    elif isinstance(block, AssetBlock):
      self._close_texts_buffer()
      self._write_image(block)

  def _close_texts_buffer(self):
    if len(self._texts_buffer) > 0:
      self._write_text_contents(self._texts_buffer)
      self._file.write("\n\n")
      self._texts_buffer.clear()

  def _write_plain_text(self, block: TextBlock) -> None:
    if block.has_paragraph_indentation:
      self._close_texts_buffer()
    self._texts_buffer.extend(block.texts)
    if not block.last_line_touch_end:
      self._close_texts_buffer()

  def _write_image(self, block: AssetBlock) -> None:
    os.makedirs(self._abs_assets_path, exist_ok=True)
    hash = sha256()
    hash.update(block.image.tobytes())
    file_name = f"{hash.hexdigest()}.png"
    file_path = os.path.join(self._abs_assets_path, file_name)
    relative_path = os.path.join(self._assets_path, file_name)

    if not os.path.exists(file_path):
      block.image.save(file_path, "PNG")

    self._file.write("![")
    self._write_text_contents(block.texts, "]")
    self._file.write(f"]({relative_path})")
    self._file.write("\n\n")

  def _write_text_contents(self, texts: Iterable[Text], ban_symbol: str | None = None) -> None:
    for text in texts:
      content = text.content.strip()
      content = content.replace("\n", " ")
      if ban_symbol is not None:
        content.replace(ban_symbol, "")
      self._file.write(content)