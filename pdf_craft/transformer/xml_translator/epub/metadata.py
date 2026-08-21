from dataclasses import dataclass
from pathlib import Path

from pdf_craft.transformer.xml_translator.xml import XMLLikeNode
from .common import find_opf_path
from .zip import Zip


@dataclass
class MetadataField:
    tag_name: str
    text: str


@dataclass
class MetadataContext:
    opf_path: Path  # OPF 文件路径
    xml_node: XMLLikeNode  # XMLLikeNode 对象，保留原始文件信息


SKIP_FIELDS = frozenset(
    (
        "language",
        "identifier",
        "date",
        "meta",
        "contributor",  # Usually technical information
    )
)


def read_metadata(zip: Zip) -> tuple[list[MetadataField], MetadataContext]:
    opf_path = find_opf_path(zip)

    with zip.read(opf_path) as f:
        xml_node = XMLLikeNode(f, is_html_like=False)

    metadata_elem = None
    for child in xml_node.element:
        if child.tag.endswith("metadata"):
            metadata_elem = child
            break

    if metadata_elem is None:
        context = MetadataContext(opf_path=opf_path, xml_node=xml_node)
        return [], context

    fields: list[MetadataField] = []
    for elem in metadata_elem:
        tag_name = elem.tag
        if elem.text and elem.text.strip() and tag_name not in SKIP_FIELDS:
            fields.append(MetadataField(tag_name=tag_name, text=elem.text.strip()))

    context = MetadataContext(opf_path=opf_path, xml_node=xml_node)
    return fields, context


def write_metadata(zip: Zip, fields: list[MetadataField], context: MetadataContext) -> None:
    metadata_elem = None
    for child in context.xml_node.element:
        if child.tag.endswith("metadata"):
            metadata_elem = child
            break

    if metadata_elem is None:
        return

    fields_by_tag: dict[str, list[str]] = {}
    for field in fields:
        if field.tag_name not in fields_by_tag:
            fields_by_tag[field.tag_name] = []
        fields_by_tag[field.tag_name].append(field.text)

    tag_counters: dict[str, int] = {tag: 0 for tag in fields_by_tag}

    for elem in metadata_elem:
        tag_name = elem.tag
        if tag_name in fields_by_tag and elem.text and elem.text.strip():
            counter = tag_counters[tag_name]
            if counter < len(fields_by_tag[tag_name]):
                elem.text = fields_by_tag[tag_name][counter]
                tag_counters[tag_name] += 1

    with zip.replace(context.opf_path) as f:
        context.xml_node.save(f)
