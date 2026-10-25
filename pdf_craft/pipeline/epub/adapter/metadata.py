from dataclasses import dataclass
from pathlib import Path

from pdf_craft.transformer.xml_translator.xml import XMLLikeNode
from .common import find_opf_path
from .zip import Zip, ReadOnlyZip


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


def read_metadata(zip: ReadOnlyZip) -> tuple[list[MetadataField], MetadataContext]:
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


def write_metadata(
    zip: Zip,
    fields: list[MetadataField],
    context: MetadataContext,
    *,
    target_language: str | None = None,
) -> None:
    apply_metadata(context.xml_node.element, fields, target_language)
    with zip.replace(context.opf_path) as file:
        context.xml_node.save(file)


def apply_metadata(root, fields, target_language):
    """Apply accepted fields once, preserving repeated and unknown OPF nodes."""
    from xml.etree.ElementTree import Element
    metadata_elem = next((child for child in root if child.tag.endswith("metadata")), None)
    if metadata_elem is None:
        metadata_elem = Element("metadata")
        root.insert(0, metadata_elem)
    fields_by_tag: dict[str, list[str]] = {}
    for field in fields:
        fields_by_tag.setdefault(field.tag_name, []).append(field.text)
    counters: dict[str, int] = {}
    has_language = False
    for elem in metadata_elem:
        tag = elem.tag
        if tag == "language":
            has_language = True
            if target_language is not None:
                elem.text = target_language
        elif tag in fields_by_tag and elem.text and elem.text.strip():
            counter = counters.get(tag, 0)
            if counter < len(fields_by_tag[tag]):
                elem.text = fields_by_tag[tag][counter]
                counters[tag] = counter + 1
    if not has_language and target_language is not None:
        language = Element("{http://purl.org/dc/elements/1.1/}language")
        language.text = target_language
        metadata_elem.append(language)


def book_metadata(root):
    """Project standard bibliographic fields; the original OPF stays untouched."""
    from pdf_craft.document.metadata import metadata_from_mapping
    metadata_elem = next((child for child in root if child.tag.endswith("metadata")), ())
    values: dict[str, list[str]] = {}
    authors, editors, translators = [], [], []
    isbn = None
    modified = None
    roles = {node.get('refines', '').lstrip('#'): node.text
             for node in metadata_elem if node.tag == 'meta' and node.get('property') == 'role'}
    for node in metadata_elem:
        tag = node.tag.rsplit('}', 1)[-1]
        text = (node.text or '').strip()
        if not text:
            continue
        values.setdefault(tag, []).append(text)
        if tag in ('creator', 'contributor'):
            role = roles.get(node.get('id', ''), node.get('role', 'aut' if tag == 'creator' else ''))
            if role == 'edt':
                editors.append(text)
            elif role == 'trl':
                translators.append(text)
            elif tag == 'creator':
                authors.append(text)
        if tag == 'identifier' and (node.get('scheme', '').casefold() == 'isbn'
                                     or text.casefold().startswith('urn:isbn:')):
            isbn = text.removeprefix('urn:isbn:').removeprefix('URN:ISBN:')
        if tag == 'meta' and node.get('property') == 'dcterms:modified':
            modified = text
    def first(tag):
        return next(iter(values.get(tag, ())), None)
    return metadata_from_mapping(dict(
        title=first('title'), description=first('description'), publisher=first('publisher'),
        isbn=isbn, authors=authors, editors=editors, translators=translators,
        publication_date=first('date'), subjects=values.get('subject', ()),
        rights=first('rights'), language=first('language'), modified=modified,
    ))


def translated_metadata_content(context, fields, target_language):
    """Build a detached callback value from exactly the same writeback rules."""
    from copy import deepcopy
    from pdf_craft.document.metadata import MetadataContent
    original = book_metadata(context.xml_node.element)
    target = deepcopy(context.xml_node.element)
    apply_metadata(target, fields, target_language)
    return MetadataContent(book_metadata(target), original)
