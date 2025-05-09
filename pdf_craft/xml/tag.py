from io import StringIO
from enum import auto, Enum
from typing import Generator
from dataclasses import dataclass


class TagKind(Enum):
  OPENING = auto()
  CLOSING = auto()
  SELF_CLOSING = auto()

@dataclass
class Tag:
  kind: TagKind
  name: str
  proto: str
  attributes: list[tuple[str, str]]

  def __str__(self):
    buffer = StringIO()
    buffer.write("<")
    if self.kind == TagKind.CLOSING:
      buffer.write("/")
    buffer.write(self.name)
    if len(self.attributes) > 0:
      buffer.write(" ")
      for i, (attr_name, attr_value) in enumerate(self.attributes):
        buffer.write(attr_name)
        buffer.write("=")
        buffer.write("\"")
        buffer.write(attr_value)
        buffer.write("\"")
        if i < len(self.attributes) - 1:
          buffer.write(" ")
    if self.kind == TagKind.SELF_CLOSING:
      buffer.write("/>")
    else:
      buffer.write(">")
    return buffer.getvalue()

  def is_valid(self) -> bool:
    if self.kind == TagKind.CLOSING and len(self.attributes) > 0:
      return False

    for name in self._iter_tag_names():
      # https://www.w3schools.com/xml/xml_elements.asp
      if name == "":
        return False
      char = name[0]
      if char == "_":
        continue
      if "a" <= char <= "z" or "A" <= char <= "Z":
        continue
      return False

    return True

  def _iter_tag_names(self) -> Generator[str, None, None]:
    yield self.name
    for attr_name, _ in self.attributes:
      yield attr_name
