import re
import zipfile
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
        names = set(archive.namelist())
        errors: list[str] = []
        for required in ("mimetype", "META-INF/container.xml"):
            if required not in names:
                errors.append(f"EPUB missing {required}")
        opfs = [name for name in names if name.endswith(".opf")]
        chapters = [name for name in names if name.endswith((".xhtml", ".html", ".htm"))]
        if not opfs:
            errors.append("EPUB contains no OPF package document")
        if not chapters:
            errors.append("EPUB contains no XHTML/HTML content")
        if not any(name.endswith((".ncx", "nav.xhtml", "nav.html")) for name in names):
            errors.append("EPUB contains no NCX or navigation document")
        return errors


def check_pdf(path: Path, expected_pages: int) -> list[str]:
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception as error:
        return [f"PDF output cannot be opened: {error}"]
    if len(reader.pages) != expected_pages:
        return [f"PDF page count changed: expected {expected_pages}, got {len(reader.pages)}"]
    return []
