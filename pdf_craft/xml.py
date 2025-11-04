from xml.etree.ElementTree import Element


def indent(elem: Element, level: int = 0) -> Element:
    indent_str = "  " * level
    next_indent_str = "  " * (level + 1)

    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = "\n" + next_indent_str
        for i, child in enumerate(elem):
            indent(child, level + 1)
            if i < len(elem) - 1:
                child.tail = "\n" + next_indent_str
            else:
                child.tail = "\n" + indent_str
    elif level > 0 and (not elem.tail or not elem.tail.strip()):
        elem.tail = "\n" + indent_str
    return elem
