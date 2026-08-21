from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.xml import ID_KEY


def element_fingerprint(element: Element) -> str:
    attrs = sorted(f"{key}={value}" for key, value in element.attrib.items())
    return f"<{element.tag} {' '.join(attrs)}/>"


def id_in_element(element: Element) -> int | None:
    id_str = element.get(ID_KEY, None)
    if id_str is None:
        return None
    try:
        return int(id_str)
    except ValueError:
        return None


class IDGenerator:
    def __init__(self):
        self._previous_id: int = 0

    def next_id(self) -> int:
        self._previous_id += 1
        return self._previous_id
