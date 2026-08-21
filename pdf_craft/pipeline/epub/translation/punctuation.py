from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.xml import iter_with_stack

_QUOTE_MAPPING = {
    # 法语引号
    "«": "",
    "»": "",
    "‹": "«",
    "›": "»",
    # 中文书书名号
    "《": "",
    "》": "",
    "〈": "《",
    "〉": "》",
}


def _strip_quotes(text: str):
    for char in text:
        mapped = _QUOTE_MAPPING.get(char, None)
        if mapped is None:
            yield char
        elif mapped:
            yield mapped


def unwrap_french_quotes(element: Element) -> Element:
    for _, child_element in iter_with_stack(element):
        if child_element.text:
            child_element.text = "".join(_strip_quotes(child_element.text))
        if child_element.tail:
            child_element.tail = "".join(_strip_quotes(child_element.tail))
    return element
