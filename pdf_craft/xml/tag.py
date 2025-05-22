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

  def find_invalid_name(self) -> str | None:
    for name in self._iter_tag_names():
      if not all(is_valid_value_char(c) for c in name):
        return name
      # https://www.w3schools.com/xml/xml_elements.asp
      # The following logic enforces a subset of XML naming rules:
      # - Names must not be empty.
      # - Names must start with a letter (a-z, A-Z) or an underscore (_).
      if name == "":
        return name
      char = name[0]
      if char == "_":
        continue
      if "a" <= char <= "z" or "A" <= char <= "Z":
        continue
      return name

    return None

  def find_invalid_attr_value(self) -> tuple[str, str] | None:
    for attr_name, attr_value in self.attributes:
      if not all(is_valid_value_char(c) for c in attr_value):
        return attr_name, attr_value
    return None

  def _iter_tag_names(self) -> Generator[str, None, None]:
    yield self.name
    for attr_name, _ in self.attributes:
      yield attr_name

def is_valid_value_char(char: str) -> bool:
  if is_valid_name_char(char):
    return True
  if char == ",":
    return True
  if char == ".":
    return True
  if char == "/":
    return True
  return False

def is_valid_name_char(char: str) -> bool:
  if "a" <= char <= "z":
    return True
  if "A" <= char <= "Z":
    return True
  if "0" <= char <= "9":
    return True
  if char == "_":
    return True
  if char == "-":
    return True
  return False