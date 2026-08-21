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

    # To make LLM easier to understand, the naming here is restricted in a more strict way.
    # https://github.com/oomol-lab/pdf-craft/issues/149
    invalid_name = tag.find_invalid_name()
    if invalid_name is not None:
        raise ValueError(f"find invalid tag name or attribute name: {invalid_name}")

    invalid_attr_pair = tag.find_invalid_attr_value()
    if invalid_attr_pair is not None:
        attr_name, attr_value = invalid_attr_pair
        raise ValueError(f'find invalid attribute value: {attr_name}="{attr_value}"')

    return tag
