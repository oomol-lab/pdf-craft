from typing import Generator
from xml.etree.ElementTree import Element
from ..pdf import AssetKind


ASSET_TAGS = ("figure", "table", "formula")

class AssetMatcher:
  def __init__(self):
    self._asset_hashes: dict[AssetKind, list[str]] = {}

  def register_raw_xml(self, root: Element) -> "AssetMatcher":
    for element in search_asset_tags(root):
      kind = self._tag_to_asset_kind(element.tag)
      hash = element.get("hash")
      if hash is not None:
        self.register_hash(kind, hash)
    return self

  def register_hash(self, kind: AssetKind, hash: str):
    hashes = self._asset_hashes.get(kind, None)
    if hashes is None:
      hashes = []
      self._asset_hashes[kind] = hashes
    hashes.append(hash)

  def add_asset_hashes_for_xml(self, root: Element):
    for element in search_asset_tags(root):
      kind = self._tag_to_asset_kind(element.tag)
      hashes = self._asset_hashes.get(kind, None)
      hash: str | None = None
      if hashes:
        hash = hashes.pop(0)
      if hash is not None:
        element.set("hash", hash)

  def _tag_to_asset_kind(self, tag_name: str) -> AssetKind:
    if tag_name == "figure":
      return AssetKind.FIGURE
    elif tag_name == "table":
      return AssetKind.TABLE
    elif tag_name == "formula":
      return AssetKind.FORMULA
    else:
      raise ValueError(f"Unknown tag name: {tag_name}")

def search_asset_tags(target: Element) -> Generator[Element, None, None]:
  for child in target:
    if child.tag in ASSET_TAGS:
      yield child
    else:
      yield from search_asset_tags(child)