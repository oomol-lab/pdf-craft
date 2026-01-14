from pathlib import Path
from xml.etree.ElementTree import Element, fromstring, tostring


def indent(elem: Element, level: int = 0) -> Element:
    indent_str = "  " * level
    next_indent_str = "  " * (level + 1)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = "\n" + next_indent_str
        for i, child in enumerate(elem):
            indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                if i == len(elem) - 1:
                    child.tail = "\n" + indent_str
                else:
                    child.tail = "\n" + next_indent_str
    return elem


def read_xml(file_path: Path) -> Element:
    try:
        return fromstring(file_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"Failed to parse XML file: {file_path}") from error


def save_xml(element: Element, file_path: Path) -> None:
    # 使用临时文件确保写入的原子性
    xml_string = tostring(element, encoding="unicode")
    temp_path = file_path.with_suffix(".xml.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(xml_string)
        temp_path.replace(file_path)
    except Exception as err:
        if temp_path.exists():
            temp_path.unlink()
        raise err
