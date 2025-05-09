from io import StringIO
from dataclasses import dataclass
from typing import Generator, Iterable
from enum import auto, Enum


_SPACES = (" ", "\n")
_VALUES_EXT = (",",)

class _Phase(Enum):
  OUTSIDE = auto()
  LEFT_BRACKET = auto()
  LEFT_SLASH = auto()
  TAG_NAME = auto()
  TAG_GAP = auto()
  ATTRIBUTE_NAME = auto()
  ATTRIBUTE_NAME_EQUAL = auto()
  ATTRIBUTE_VALUE = auto()
  MUST_CLOSING_SIGN = auto()

class _ParsedResult(Enum):
  Continue = auto()
  Success = auto()
  Failed = auto()

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

def parse_tags(chars: Iterable[str]) -> Generator[str | Tag, None, None]:
  yield from _XMLTagsParser().do(chars)

class _XMLTagsParser:
  def __init__(self):
    self._outside_buffer: StringIO = StringIO()
    self._tag_buffer: StringIO = StringIO()
    self._tag: Tag | None = None
    self._phase: _Phase = _Phase.OUTSIDE

  def do(self, chars: Iterable[str]) -> Generator[str | Tag, None, None]:
    for char in chars:
      parsed_result = self._parse_char(char)
      yield from self._generate_by_result(parsed_result)

    self._outside_buffer.write(self._tag_buffer.getvalue())
    outside_text = self._outside_buffer.getvalue()
    if outside_text != "":
      yield outside_text

  def _parse_char(self, char: str) -> _ParsedResult:
    parsed_result: _ParsedResult = _ParsedResult.Continue

    if self._phase == _Phase.OUTSIDE:
      if char != "<":
        self._outside_buffer.write(char)
      else:
        self._phase = _Phase.LEFT_BRACKET
        self._tag_buffer.write(char)
        self._tag = Tag(
          kind=TagKind.OPENING,
          name="",
          proto="",
          attributes=[],
        )
    else:
      self._tag_buffer.write(char)

      if self._phase == _Phase.LEFT_BRACKET:
        if char == "/":
          self._tag.kind = TagKind.CLOSING
          self._phase = _Phase.LEFT_SLASH
        elif self._is_valid_name_char(char):
          self._tag.name += char
          self._phase = _Phase.TAG_NAME
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.LEFT_SLASH:
        if self._is_valid_name_char(char):
          self._tag.name += char
          self._phase = _Phase.TAG_NAME
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.TAG_NAME:
        if char in _SPACES:
          self._phase = _Phase.TAG_GAP
        elif self._is_valid_name_char(char):
          self._tag.name += char
        elif char == ">":
          parsed_result = _ParsedResult.Success
        elif char == "/" and self._tag.kind == TagKind.OPENING:
          self._tag.kind = TagKind.SELF_CLOSING
          self._phase = _Phase.MUST_CLOSING_SIGN
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.TAG_GAP:
        if char in _SPACES:
          pass
        elif self._is_valid_name_char(char):
          self._tag.attributes.append((char, ""))
          self._phase = _Phase.ATTRIBUTE_NAME
        elif char == ">":
          parsed_result = _ParsedResult.Success
        elif char == "/" and self._tag.kind == TagKind.OPENING:
          self._tag.kind = TagKind.SELF_CLOSING
          self._phase = _Phase.MUST_CLOSING_SIGN
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.ATTRIBUTE_NAME:
        if self._is_valid_name_char(char):
          attr_name, attr_value = self._tag.attributes[-1]
          attr_name = attr_name + char
          self._tag.attributes[-1] = (attr_name, attr_value)
        elif char == "=":
          self._phase = _Phase.ATTRIBUTE_NAME_EQUAL
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.ATTRIBUTE_NAME_EQUAL:
        if char == "\"":
          self._phase = _Phase.ATTRIBUTE_VALUE
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.ATTRIBUTE_VALUE:
        if char in _VALUES_EXT or self._is_valid_name_char(char):
          attr_name, attr_value = self._tag.attributes[-1]
          attr_value = attr_value + char
          self._tag.attributes[-1] = (attr_name, attr_value)
        elif char == "\"":
          self._phase = _Phase.TAG_GAP
        else:
          parsed_result = _ParsedResult.Failed

      elif self._phase == _Phase.MUST_CLOSING_SIGN:
        if char == ">":
          parsed_result = _ParsedResult.Success
        else:
          parsed_result = _ParsedResult.Failed

    return parsed_result

  def _generate_by_result(self, parsed_result: _ParsedResult) -> Generator[str | Tag, None, None]:
    if parsed_result == _ParsedResult.Success:
      assert self._tag is not None
      if self._is_tag_valid(self._tag):
        outside_text = self._outside_buffer.getvalue()
        self._clear_buffer(self._outside_buffer)
        self._clear_buffer(self._tag_buffer)
        if outside_text != "":
          yield outside_text
        yield self._tag
      else:
        self._tag.proto = self._tag_buffer.getvalue()
        self._outside_buffer.write(self._tag.proto)
        self._clear_buffer(self._tag_buffer)
      self._tag = None
      self._phase = _Phase.OUTSIDE

    elif parsed_result == _ParsedResult.Failed:
      self._outside_buffer.write(self._tag_buffer.getvalue())
      self._clear_buffer(self._tag_buffer)
      self._phase = _Phase.OUTSIDE

  def _is_tag_valid(self, tag: Tag) -> bool:
    if tag.kind == TagKind.CLOSING and len(tag.attributes) > 0:
      return False

    for name in self._iter_tag_names(tag):
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

  def _iter_tag_names(self, tag: Tag):
    yield tag.name
    for attr_name, _ in tag.attributes:
      yield attr_name

  def _clear_buffer(self, buffer: StringIO):
    buffer.truncate(0)
    buffer.seek(0)

  def _is_valid_name_char(self, char: str) -> bool:
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