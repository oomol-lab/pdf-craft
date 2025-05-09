from xml.etree.ElementTree import Element
from .tag import Tag, TagKind


def tag_to_element(tag: Tag) -> Element:
  element = Element(tag.name)
  for attr_name, attr_value in tag.attributes:
    element.set(attr_name, attr_value)
  return element

def element_to_tag(element: Element, kind: TagKind, proto: str = "") -> Tag:
  tag = Tag(
    kind=kind,
    name=element.tag,
    proto=proto,
    attributes=[],
  )
  if kind != TagKind.CLOSING:
    for attr_name in sorted(list(element.keys())):
      attr_value = element.get(attr_name, "")
      tag.attributes.append((attr_name, attr_value))

  return tag