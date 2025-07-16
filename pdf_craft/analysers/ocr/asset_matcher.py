from typing import Generator, Iterable
from enum import auto, Enum
from xml.etree.ElementTree import Element


ASSET_TAGS = ("figure", "table", "formula")

class AssetKind(Enum):
  FIGURE = auto()
  TABLE = auto()
  FORMULA = auto()

class AssetMatcher:
  def __init__(self):
    self._cloned_store: dict[AssetKind, list[Element]] = {}

  def register_raw_xml(self, root: Element) -> "AssetMatcher":
    for element in search_asset_tags(root):
      kind = self._tag_to_asset_kind(element.tag)
      cloned = self._clone_element(element)
      self._cloned_list(kind).append(cloned)
      element.clear()
    return self

  def register_virtual_dom(
        self,
        kind: AssetKind,
        hash: str | None = None,
        children: Iterable[Element] | None = None,
      ):
    tag_name = self._asset_kind_to_tag(kind)
    cloned = Element(tag_name)
    if hash is not None:
      cloned.attrib = { "hash": hash }
    if children is not None:
      for child in children:
        cloned.append(child)
    self._cloned_list(kind).append(cloned)

  def recover_asset_doms_for_xml(self, root: Element):
    for element in search_asset_tags(root):
      kind = self._tag_to_asset_kind(element.tag)
      cloned_list = self._cloned_store.get(kind, None)
      if not cloned_list:
        continue
      cloned = cloned_list.pop(0)
      attrib = {
        **element.attrib,
        **cloned.attrib,
      }
      element.clear()
      element.attrib = attrib
      for child in cloned:
        element.append(child)

  def _tag_to_asset_kind(self, tag_name: str) -> AssetKind:
    if tag_name == "figure":
      return AssetKind.FIGURE
    elif tag_name == "table":
      return AssetKind.TABLE
    elif tag_name == "formula":
      return AssetKind.FORMULA
    else:
      raise ValueError(f"Unknown tag name: {tag_name}")

  def _asset_kind_to_tag(self, kind: AssetKind) -> str:
    if kind == AssetKind.FIGURE:
      return "figure"
    elif kind == AssetKind.TABLE:
      return "table"
    elif kind == AssetKind.FORMULA:
      return "formula"
    else:
      raise ValueError(f"Unknown asset kind: {kind}")

  def _clone_element(self, element: Element) -> Element:
    cloned = Element(element.tag, element.attrib)
    cloned.text = element.text
    cloned.tail = element.tail
    for child in element:
      cloned.append(self._clone_element(child))
    return cloned

  def _cloned_list(self, kind: AssetKind) -> list[Element]:
    cloned_list = self._cloned_store.get(kind, None)
    if cloned_list is None:
      cloned_list = []
      self._cloned_store[kind] = cloned_list
    return cloned_list

def search_asset_tags(target: Element) -> Generator[Element, None, None]:
  for child in target:
    if child.tag in ASSET_TAGS:
      yield child
    else:
      yield from search_asset_tags(child)