import re
import zipfile
from posixpath import normpath
from pathlib import Path
from xml.etree import ElementTree

import pypdf

from pdf_craft.document import DocumentPackage


def check_package(package: DocumentPackage, require_geometry: bool = False) -> list[str]:
    package.validate()
    errors: list[str] = []
    chapters = sorted(package.chapters_path.glob("chapter*.xml"))
    if not chapters:
        errors.append("DocumentPackage contains no chapter XML")
    for chapter in chapters:
        try:
            ElementTree.parse(chapter)
        except ElementTree.ParseError as error:
            errors.append(f"invalid chapter XML {chapter.name}: {error}")
    if package.has_toc():
        try:
            ElementTree.parse(package.toc_path)  # type: ignore[arg-type]
        except ElementTree.ParseError as error:
            errors.append(f"invalid toc.xml: {error}")
    if require_geometry and not package.page_pixel_sizes():
        errors.append("DocumentPackage lacks required page geometry metadata")
    return errors


def check_markdown(path: Path, assets_path: Path) -> list[str]:
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        return ["Markdown output is missing or empty"]
    errors: list[str] = []
    for target in re.findall(r"!\[[^]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        target_path = assets_path / target
        if not target_path.exists():
            errors.append(f"Markdown image reference is missing: {target}")
    return errors


def check_epub(path: Path) -> list[str]:
    if not zipfile.is_zipfile(path):
        return ["EPUB output is not a ZIP file"]
    with zipfile.ZipFile(path) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        errors: list[str] = []
        if "mimetype" not in names:
            return ["EPUB missing mimetype"]
        if "META-INF/container.xml" not in names:
            return ["EPUB missing META-INF/container.xml"]
        container = _parse_archive_xml(archive, "META-INF/container.xml", errors)
        if container is None:
            return errors
        rootfiles = [element for element in container.iter() if _local_name(element.tag) == "rootfile"]
        opf_paths = [element.get("full-path") for element in rootfiles if element.get("full-path")]
        if len(opf_paths) != 1:
            return errors + ["container.xml must declare exactly one OPF rootfile"]
        opf_path = opf_paths[0]
        if opf_path not in names:
            return errors + [f"container.xml references missing OPF: {opf_path}"]
        opf = _parse_archive_xml(archive, opf_path, errors)
        if opf is None:
            return errors
        return errors + _check_opf(archive, names, opf_path, opf)


def _check_opf(archive, names: set[str], opf_path: str, opf) -> list[str]:
    errors: list[str] = []
    manifest = _first_child(opf, "manifest")
    spine = _first_child(opf, "spine")
    if manifest is None:
        errors.append("OPF missing manifest")
    if spine is None:
        errors.append("OPF missing spine")
    if manifest is None or spine is None:
        return errors
    opf_dir = str(Path(opf_path).parent).replace(".", "", 1).strip("/")
    items: dict[str, tuple[str, str, set[str]]] = {}
    for item in _children(manifest, "item"):
        item_id, href = item.get("id"), item.get("href")
        if not item_id or not href:
            errors.append("OPF manifest item missing id or href")
            continue
        item_path = _resolve(opf_dir, href)
        if item_id in items:
            errors.append(f"OPF manifest has duplicate id: {item_id}")
        items[item_id] = (item_path, item.get("media-type", ""), set(item.get("properties", "").split()))
        if item_path not in names:
            errors.append(f"OPF manifest references missing resource: {item_path}")
    spine_items = _children(spine, "itemref")
    if not spine_items:
        errors.append("OPF spine has no itemref")
    spine_paths: list[str] = []
    for itemref in spine_items:
        idref = itemref.get("idref")
        if not idref or idref not in items:
            errors.append(f"OPF spine references missing manifest id: {idref}")
            continue
        spine_paths.append(items[idref][0])
    for chapter_path in spine_paths:
        _parse_archive_xml(archive, chapter_path, errors, "spine XHTML")
    errors.extend(_check_toc(archive, names, opf, items, spine))
    return errors


def _check_toc(archive, names: set[str], opf, items, spine) -> list[str]:
    version = opf.get("version", "2.0")
    if version.startswith("3"):
        nav_items = [item for item in items.values() if "nav" in item[2]]
        if len(nav_items) != 1:
            return ["EPUB 3 OPF must declare exactly one navigation manifest item"]
        nav_path = nav_items[0][0]
        nav = _parse_archive_xml(archive, nav_path, [], "navigation document")
        if nav is None:
            return [f"invalid navigation document: {nav_path}"]
        toc_navs = [element for element in nav.iter() if _local_name(element.tag) == "nav" and "toc" in _property_tokens(element)]
        if not toc_navs:
            return ["EPUB 3 navigation document has no toc nav"]
        return _check_toc_links(toc_navs[0], names, str(Path(nav_path).parent).strip("."))
    ncx_id = spine.get("toc")
    if not ncx_id or ncx_id not in items:
        return ["EPUB 2 spine must reference an NCX manifest item"]
    ncx_path = items[ncx_id][0]
    ncx = _parse_archive_xml(archive, ncx_path, [], "NCX")
    if ncx is None:
        return [f"invalid NCX: {ncx_path}"]
    if not any(_local_name(element.tag) == "navMap" for element in ncx.iter()):
        return ["NCX has no navMap"]
    return _check_toc_links(ncx, names, str(Path(ncx_path).parent).strip("."), attribute="src")


def _check_toc_links(root, names: set[str], base_dir: str, attribute: str = "href") -> list[str]:
    errors: list[str] = []
    links = [element.get(attribute) for element in root.iter() if _local_name(element.tag) in {"a", "content"} and element.get(attribute)]
    if not links:
        return ["TOC contains no links"]
    for link in links:
        target = _resolve(base_dir, link.split("#", 1)[0])
        if target and target not in names:
            errors.append(f"TOC link references missing resource: {target}")
    return errors


def _parse_archive_xml(archive, name: str, errors: list[str], label: str = "XML"):
    try:
        return ElementTree.fromstring(archive.read(name))
    except (KeyError, ElementTree.ParseError) as error:
        errors.append(f"invalid {label} {name}: {error}")
        return None


def _resolve(base_dir: str, href: str) -> str:
    if not href:
        return ""
    return normpath(f"{base_dir}/{href}").lstrip("./")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element, name: str):
    return [child for child in element if _local_name(child.tag) == name]


def _first_child(element, name: str):
    return next(iter(_children(element, name)), None)


def _property_tokens(element) -> set[str]:
    return set(element.get("{http://www.idpf.org/2007/ops}type", "").split()) | set(element.get("epub:type", "").split())


def check_pdf(path: Path, expected_pages: int) -> list[str]:
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as error:
        return [f"PDF output cannot be opened: {error}"]
    if len(reader.pages) != expected_pages:
        return [f"PDF page count changed: expected {expected_pages}, got {len(reader.pages)}"]
    return []
