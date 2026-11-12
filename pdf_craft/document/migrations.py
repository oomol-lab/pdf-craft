"""PCEX version migrations, run on isolated input copies before business access.

Only this module interprets historical image field shapes. Source chapters and
all persisted translation layers are converted together, without renumbering
flow identities, changing coverage or modifying the caller's archive.
"""
from copy import deepcopy
from pathlib import Path
from typing import Any
import json
from xml.etree import ElementTree

from ..common import save_xml

CURRENT_VERSION = 8


def migrate_workspace(root: Path) -> None:
    """Validate historical data, then upgrade its private materialization to the current format."""
    # Local import keeps the storage schema validator independent of adapters.
    from .package import ExtractionPaths, _read_manifest, _validate_workspace
    paths = ExtractionPaths.at(root)
    manifest = _read_manifest(paths.manifest)
    version = manifest['format_version']
    if version == CURRENT_VERSION:
        return
    _validate_workspace(paths)
    if version in {1, 2}:
        for path in paths.chapters.glob('chapter_*.xml'):
            path.write_bytes(_v3_chapter_xml(path))
    if version in {1, 2, 3}:
        manifest = _v3_manifest(manifest)
    if version < 5:
        _migrate_image_fields(paths.chapters, paths.translations)
    if version < 6:
        _migrate_fragment_identity(paths.chapters, paths.translations)
    # v7 permits adopted empty furniture in translation layers. Existing v6
    # fields, source identities and coverage already have their final meaning;
    # preserve every source/layer value rather than guessing missing text.
    _migrate_formula_coverage(paths.chapters, paths.translations, root / 'translation.xml')
    manifest['format_version'] = CURRENT_VERSION
    paths.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    _validate_workspace(paths)


def historical_chapter_view(root: ElementTree.Element) -> ElementTree.Element:
    """Supply only v6 provenance fields during strict historical validation."""
    result = deepcopy(root)
    for fragment in result.iter('fragment'):
        if fragment.get('source_order') is None:
            raise ValueError('Historical fragment is missing source_order')
        fragment.set('fragment_id', fragment.attrib['source_order'])
        fragment.set('source_start', '0')
    return result


def _migrate_fragment_identity(chapters: Path, translations: Path) -> None:
    """Full-source slots retain keys, including existing coverage keys.

    Historical archives lack raw OCR evidence. Unknown original ranges remain
    unavailable; never reconstruct them from stripped or translated content.
    """
    for path in chapters.glob('chapter_*.xml'):
        save_xml(historical_chapter_view(ElementTree.parse(path).getroot()), path)
        for layer_path in translations.glob(f'*/chapters/{path.name}'):
            save_xml(historical_chapter_view(ElementTree.parse(layer_path).getroot()), layer_path)


def _migrate_image_fields(chapters: Path, translations: Path) -> None:
    for path in chapters.glob('chapter_*.xml'):
        source = ElementTree.parse(path).getroot()
        # v4 layer structures have already been checked against their source.
        images = [node for node in source.iter('asset') if node.get('ref') == 'image']
        indexes = [index for index, image in enumerate(images) if _content_caption(image)]
        if not indexes:
            continue
        _move_captions(images, indexes)
        save_xml(source, path)
        for layer_path in translations.glob(f'*/chapters/{path.name}'):
            layer = ElementTree.parse(layer_path).getroot()
            layer_images = [node for node in layer.iter('asset') if node.get('ref') == 'image']
            _move_captions(layer_images, indexes)
            save_xml(layer, layer_path)


def _content_caption(image: ElementTree.Element) -> bool:
    def populated(name):
        node = image.find(name)
        return node is not None and bool(len(node) or (node.text or '').strip())
    return populated('content') and not populated('title') and not populated('caption')


def _move_captions(images, indexes):
    for index in indexes:
        image = images[index]
        content = image.find('content')
        assert content is not None  # Source/layer structure was validated together.
        caption = image.find('caption')
        if caption is not None:
            if len(caption) or (caption.text or '').strip():
                raise ValueError('ambiguous legacy image caption in translation layer')
            image.remove(caption)
        content.tag = 'caption'


def _v3_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Upgrade historical metadata before applying the v4-to-v5 field migration."""
    document = manifest["document"]
    raw_authors = document.get("authors", [])
    authors = (
        [{"name": author, "original_name": None, "nationality": None} for author in raw_authors]
        if manifest["format_version"] == 1 else raw_authors
    )
    return {
        "format_version": 4,
        "producer": manifest["producer"],
        "created_at": manifest.get("created_at"),
        "document": {
            "title": document.get("title"),
            "original_title": document.get("original_title"),
            "description": document.get("description"),
            "publisher": document.get("publisher"),
            "isbn": document.get("isbn"),
            "authors": authors,
            "editors": document.get("editors", []),
            "translators": document.get("translators", []),
            "publication_date": document.get("publication_date"),
            "edition": document.get("edition"),
            "subjects": document.get("subjects", []),
            "rights": document.get("rights"),
            "modified": document.get("modified"),
            "language": document.get("language"),
        },
    }


def _v3_chapter_xml(path: Path) -> bytes:
    """Decode a legacy chapter and serialize its canonical v3 FlowItem form."""
    from ..extractor.chapter.chapter import decode as decode_chapter, encode as encode_chapter

    root = ElementTree.parse(path).getroot()
    chapter = decode_chapter(root, allow_legacy=True)
    return ElementTree.tostring(encode_chapter(chapter), encoding="utf-8", xml_declaration=True)


def _migrate_formula_coverage(chapters: Path, translations: Path, coverage: Path) -> None:
    """Old display equations were frozen, not adopted translations.

    Source/layer content and paths remain unchanged. Record that fact once at
    the input boundary; runtime rendering never guesses from absent fields.
    """
    identities = []
    for path in chapters.glob('chapter_*.xml'):
        root = ElementTree.parse(path).getroot()
        chapter = root.get('id', 'head')
        for index, item in enumerate(root.find('flow') or []):
            if item.tag == 'display-formula':
                identities.append((chapter, f'body:{index}'))
        for reference in root.findall('references/ref'):
            for index, item in enumerate(reference.find('flow') or []):
                if item.tag == 'display-formula':
                    identities.append((chapter, f"note:{reference.get('id')}:{index}"))
    for path in [coverage, *translations.glob('*/coverage.xml')]:
        if not path.exists() or not identities:
            continue
        root = ElementTree.parse(path).getroot()
        narrative = root.find('narrative')
        if narrative is None:
            narrative = ElementTree.Element('narrative')
            root.insert(0, narrative)
        for chapter, formula_path in identities:
            ElementTree.SubElement(narrative, 'formula', dict(chapter_id=chapter, path=formula_path, state='preserved'))
        save_xml(root, path)
