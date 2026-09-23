"""The public PDF Craft Extraction artifact and its internal workspace storage."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Protocol
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from epub_generator import BookMeta

from ..common import indent, save_xml
from ..runtime import IO_DOMAIN, run_atomic_cancellable


FORMAT_VERSION = 3
EXTRACTION_SUFFIX = ".pcex"
_MANIFEST_FIELDS = {"format_version", "producer", "created_at", "document"}
_DOCUMENT_FIELDS_V1 = {
    "title", "description", "publisher", "isbn", "authors", "editors",
    "translators", "modified", "language",
}
_DOCUMENT_FIELDS = {
    "title", "original_title", "description", "publisher", "isbn", "authors",
    "editors", "translators", "publication_date", "edition", "subjects", "rights",
    "modified", "language",
}


@dataclass(frozen=True)
class DocumentAuthor:
    """A named author, with optional information printed for a translated edition."""

    name: str
    original_name: str | None = None
    nationality: str | None = None


@dataclass(frozen=True)
class DocumentMetadata:
    """Normalized bibliographic metadata stored in a PCEX manifest."""

    title: str | None = None
    original_title: str | None = None
    description: str | None = None
    publisher: str | None = None
    isbn: str | None = None
    authors: tuple[DocumentAuthor, ...] = ()
    editors: tuple[str, ...] = ()
    translators: tuple[str, ...] = ()
    publication_date: str | None = None
    edition: str | None = None
    subjects: tuple[str, ...] = ()
    rights: str | None = None
    modified: datetime | None = None
    language: str | None = None


@dataclass(frozen=True)
class ExtractionPaths:
    """Filesystem view used only while an extraction is materialized."""

    root: Path
    manifest: Path
    pages: Path
    chapters: Path
    assets: Path
    toc: Path
    cover: Path
    furnitures: Path
    translation: Path

    @classmethod
    def at(cls, root: Path) -> "ExtractionPaths":
        return cls(
            root=root,
            manifest=root / "manifest.json",
            pages=root / "pages.xml",
            chapters=root / "chapters",
            assets=root / "assets",
            toc=root / "toc.xml",
            cover=root / "cover.png",
            furnitures=root / "furnitures.xml",
            translation=root / "translation.xml",
        )


class _Storage(Protocol):
    @contextmanager
    def materialize(self) -> Iterator[ExtractionPaths]: ...


@dataclass(frozen=True)
class _WorkspaceStorage:
    root: Path

    @contextmanager
    def materialize(self) -> Iterator[ExtractionPaths]:
        yield ExtractionPaths.at(self.root)


class _ArchiveStorage:
    """Archive reference materialized only for the duration of one operation."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def materialize(self) -> Iterator[ExtractionPaths]:
        with TemporaryDirectory(prefix="pdf-craft-extraction-") as directory:
            paths = ExtractionPaths.at(Path(directory))
            _extract_archive(self.path, paths.root)
            _validate_workspace(paths)
            yield paths


class PDFCraftExtraction:
    """An opaque, validated PCEX handle created by a PDFCraft facade."""

    _CONSTRUCTION_TOKEN = object()

    def __init__(self, path: str | Path, *, _token: object | None = None) -> None:
        if _token is not self._CONSTRUCTION_TOKEN:
            raise TypeError(
                "PDFCraftExtraction handles must be opened through PDFCraft or AsyncPDFCraft"
            )
        archive = Path(path)
        _require_pcex_path(archive)
        if not archive.is_file():
            raise FileNotFoundError(f"PDFCraftExtraction does not exist: {archive}")
        self._storage: _Storage = _ArchiveStorage(archive)
        self._validate()

    @classmethod
    def _open(cls, path: str | Path) -> "PDFCraftExtraction":
        return cls(path, _token=cls._CONSTRUCTION_TOKEN)

    @classmethod
    def _from_workspace(cls, root: Path) -> "PDFCraftExtraction":
        """Create the internal directory-backed representation."""
        extraction = cls.__new__(cls)
        extraction._storage = _WorkspaceStorage(root)
        return extraction

    @classmethod
    def _from_exported_archive(cls, path: Path) -> "PDFCraftExtraction":
        """Create a lazy view of an archive just written by this process."""
        extraction = cls.__new__(cls)
        extraction._storage = _ArchiveStorage(path)
        return extraction

    @contextmanager
    def _materialize(self) -> Iterator[ExtractionPaths]:
        with self._storage.materialize() as paths:
            yield paths

    def _validate(self, *, require_toc: bool = False) -> "PDFCraftExtraction":
        with self._materialize() as paths:
            _validate_workspace(paths, require_toc=require_toc)
        return self

    def _export(self, path: str | Path) -> "PDFCraftExtraction":
        target = Path(path)
        self._export_sync(target, _commit_unconditionally)
        return PDFCraftExtraction._from_exported_archive(target)

    async def _export_async(self, path: str | Path) -> "PDFCraftExtraction":
        target = Path(path)
        await run_atomic_cancellable(
            IO_DOMAIN,
            lambda bridge: self._export_sync(target, bridge.commit),
        )
        return PDFCraftExtraction._from_exported_archive(target)

    def _export_sync(
        self,
        target: Path,
        commit: Callable[[Callable[[], object]], bool],
    ) -> bool:
        _require_pcex_path(target)
        if target.exists():
            raise FileExistsError(f"PDFCraftExtraction already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="wb", prefix=f".{target.name}.", suffix=".tmp",
                dir=target.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with self._materialize() as paths:
                _validate_workspace(paths)
                _write_archive(paths, temporary_path)
            archive_path = temporary_path
            if not commit(lambda: os.replace(archive_path, target)):
                return False
            temporary_path = None
            return True
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _page_pixel_sizes(self) -> dict[int, tuple[int, int]]:
        with self._materialize() as paths:
            return _read_pages(paths.pages)[1]

    def _render_dpi(self) -> int:
        with self._materialize() as paths:
            return _read_pages(paths.pages)[0]

    def _book_meta(self) -> BookMeta | None:
        return _book_meta(self._document_metadata())

    def _language(self) -> str | None:
        return _optional_string(self._document_metadata().get("language"))

    def _document_metadata(self) -> dict[str, Any]:
        with self._materialize() as paths:
            return dict(_read_manifest(paths.manifest)["document"])


def _book_meta(document: dict[str, Any]) -> BookMeta | None:
    if not document:
        return None
    modified = document.get("modified")
    parsed_modified = datetime.fromisoformat(modified) if isinstance(modified, str) else None
    return BookMeta(
        title=_optional_string(document.get("title")),
        description=_optional_string(document.get("description")),
        publisher=_optional_string(document.get("publisher")),
        isbn=_optional_string(document.get("isbn")),
        authors=_author_names(document.get("authors")),
        editors=_string_list(document.get("editors")),
        translators=_string_list(document.get("translators")),
        modified=parsed_modified,
    )


def _commit_unconditionally(action: Callable[[], object]) -> bool:
    action()
    return True


def write_manifest(
    root: Path,
    *,
    book_meta: BookMeta | None = None,
    document_metadata: DocumentMetadata | None = None,
    language: str | None = None,
) -> None:
    """Write the format manifest at the extraction boundary."""
    if document_metadata is not None and book_meta is not None:
        raise ValueError("pass either document_metadata or book_meta, not both")
    metadata = document_metadata or _metadata_from_book_meta(book_meta or BookMeta())
    modified = metadata.modified.isoformat() if metadata.modified is not None else None
    payload = {
        "format_version": FORMAT_VERSION,
        "producer": {"name": "pdf-craft", "version": _producer_version()},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document": {
            "title": metadata.title,
            "original_title": metadata.original_title,
            "description": metadata.description,
            "publisher": metadata.publisher,
            "isbn": metadata.isbn,
            "authors": [
                {
                    "name": author.name,
                    "original_name": author.original_name,
                    "nationality": author.nationality,
                }
                for author in metadata.authors
            ],
            "editors": list(metadata.editors),
            "translators": list(metadata.translators),
            "publication_date": metadata.publication_date,
            "edition": metadata.edition,
            "subjects": list(metadata.subjects),
            "rights": metadata.rights,
            "modified": modified,
            "language": language if language is not None else metadata.language,
        },
    }
    path = ExtractionPaths.at(root).manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_pages(root: Path, *, render_dpi: int, page_pixel_sizes: dict[int, tuple[int, int]]) -> None:
    """Write page coordinate metadata consumed by all downstream PDF work."""
    pages = ElementTree.Element(
        "pages",
        {"index_base": "1", "coordinate_space": "ocr_pixels", "render_dpi": str(render_dpi)},
    )
    for index, (width, height) in sorted(page_pixel_sizes.items()):
        ElementTree.SubElement(
            pages, "page", {"index": str(index), "width": str(width), "height": str(height)}
        )
    save_xml(indent(pages), ExtractionPaths.at(root).pages)


def _validate_workspace(paths: ExtractionPaths, *, require_toc: bool = False) -> None:
    # Import lazily because the extractor package imports the public document API.
    from ..extractor.chapter.chapter import (
        SourceAsset, SourceTextFragment, StandaloneAsset, TextFlowItem,
        decode as decode_chapter,
    )
    from ..extractor.toc.types import decode as decode_toc

    manifest = _read_manifest(paths.manifest)
    _, page_sizes = _read_pages(paths.pages)
    if not paths.chapters.is_dir():
        raise ValueError("PDFCraftExtraction is missing chapters directory")
    if not paths.assets.is_dir():
        raise ValueError("PDFCraftExtraction is missing assets directory")
    if require_toc and not paths.toc.is_file():
        raise ValueError("PDFCraftExtraction is missing toc.xml")
    toc_ids: set[int] = set()
    if paths.toc.exists():
        toc_root = _require_xml_root(paths.toc, "toc")
        try:
            toc = decode_toc(toc_root)
        except ValueError as error:
            raise ValueError(f"invalid toc schema in {paths.toc.name}: {error}") from error
        stack = list(toc.content)
        while stack:
            item = stack.pop()
            toc_ids.add(item.id)
            stack.extend(item.children)
    if paths.furnitures.exists():
        _validate_furnitures(paths.furnitures, page_sizes, toc_ids)
    if paths.cover.exists() and not paths.cover.is_file():
        raise ValueError("PDFCraftExtraction cover.png is not a file")
    _validate_workspace_members(paths)

    chapter_paths = list(paths.chapters.glob("chapter_*.xml"))
    narrative_identities: set[tuple[str, str, str]] = set()
    anchored_identities: set[tuple[str, int, int]] = set()
    for path in chapter_paths:
        if path.name != "chapter_head.xml":
            suffix = path.stem.removeprefix("chapter_")
            if not suffix.isdigit():
                raise ValueError(f"invalid chapter filename: {path.name}")
        root = _require_xml_root(path, "chapter")
        try:
            chapter = decode_chapter(root, allow_legacy=manifest["format_version"] in {1, 2})
        except ValueError as error:
            raise ValueError(f"invalid chapter schema in {path.name}: {error}") from error
        for item in chapter.flow_items:
            if not isinstance(item, TextFlowItem) or item.role not in {"body", "heading"}:
                continue
            first = next((child for child in item.children if isinstance(child, SourceTextFragment)), None)
            if first is None:
                continue
            narrative_identities.add((
                str(chapter.id) if chapter.id is not None else "head",
                str(first.page_index),
                str(first.source_order),
            ))
        chapter_identity = str(chapter.id) if chapter.id is not None else "head"
        for flow_index, item in enumerate(chapter.flow_items):
            if isinstance(item, TextFlowItem):
                anchored_identities.update(
                    (chapter_identity, flow_index, child_index)
                    for child_index, child in enumerate(item.children)
                    if isinstance(child, SourceAsset) and child.ref in {"image", "table"}
                )
            elif isinstance(item, StandaloneAsset) and item.asset.ref in {"image", "table"}:
                anchored_identities.add((chapter_identity, flow_index, -1))
        for element in root.iter():
            page_index = element.get("page_index")
            det = element.get("bbox", element.get("det"))
            if page_index is None:
                continue
            try:
                index = int(page_index)
            except ValueError as error:
                raise ValueError(f"invalid page_index in {path.name}: {page_index}") from error
            if index not in page_sizes:
                raise ValueError(f"{path.name} references page {index} missing from pages.xml")
            if det is not None:
                _validate_bbox(det, page_sizes[index], path.name)
            asset_hash = element.get("asset_hash", element.get("hash")) if element.tag == "asset" else None
            if asset_hash is not None:
                if not _is_asset_hash(asset_hash):
                    raise ValueError(f"invalid asset hash in {path.name}: {asset_hash}")
                if not (paths.assets / f"{asset_hash}.png").is_file():
                    raise ValueError(f"{path.name} references missing asset: {asset_hash}.png")
    if paths.translation.exists():
        _validate_translation(
            paths.translation, paths.furnitures, narrative_identities, anchored_identities,
        )


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("PDFCraftExtraction is missing manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid PDFCraftExtraction manifest.json") from error
    if not isinstance(payload, dict) or set(payload) - _MANIFEST_FIELDS:
        raise ValueError("manifest.json contains unsupported fields")
    format_version = payload.get("format_version")
    if format_version not in {1, 2, FORMAT_VERSION}:
        raise ValueError("unsupported PDFCraftExtraction format version")
    producer = payload.get("producer")
    if not isinstance(producer, dict) or set(producer) != {"name", "version"} or not all(
        isinstance(producer.get(key), str) and producer[key] for key in ("name", "version")
    ):
        raise ValueError("manifest.json has an invalid producer")
    created_at = payload.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        raise ValueError("manifest.json created_at must be a string")
    if isinstance(created_at, str):
        try:
            datetime.fromisoformat(created_at)
        except ValueError as error:
            raise ValueError("manifest.json created_at must be ISO 8601") from error
    document = payload.get("document")
    expected_fields = _DOCUMENT_FIELDS_V1 if format_version == 1 else _DOCUMENT_FIELDS
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise ValueError("manifest.json has invalid document metadata")
    for key in (
        "title", "description", "publisher", "isbn", "modified", "language",
        "original_title", "publication_date", "edition", "rights",
    ):
        if key not in document:
            continue
        value = document.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"manifest.json document.{key} must be a string or null")
    if format_version == 1:
        _string_list(document.get("authors"))
    else:
        _author_list(document.get("authors"))
    for key in ("editors", "translators", "subjects"):
        if key not in document:
            continue
        _string_list(document.get(key))
    if isinstance(document.get("modified"), str):
        try:
            datetime.fromisoformat(document["modified"])
        except ValueError as error:
            raise ValueError("manifest.json document.modified must be ISO 8601") from error
    payload["document"] = document
    return payload


def _read_pages(path: Path) -> tuple[int, dict[int, tuple[int, int]]]:
    root = _require_xml_root(path, "pages")
    if set(root.attrib) != {"index_base", "coordinate_space", "render_dpi"}:
        raise ValueError("pages.xml has unsupported root attributes")
    if root.get("index_base") != "1" or root.get("coordinate_space") != "ocr_pixels":
        raise ValueError("pages.xml uses an unsupported coordinate system")
    try:
        render_dpi = int(root.get("render_dpi", ""))
    except ValueError as error:
        raise ValueError("pages.xml render_dpi must be a positive integer") from error
    if render_dpi <= 0:
        raise ValueError("pages.xml render_dpi must be a positive integer")
    sizes: dict[int, tuple[int, int]] = {}
    for page in root:
        if page.tag != "page":
            raise ValueError(f"pages.xml contains unknown element: {page.tag}")
        if set(page.attrib) != {"index", "width", "height"} or len(page):
            raise ValueError("pages.xml has an invalid page element")
        try:
            index = int(page.get("index", ""))
            width = int(page.get("width", ""))
            height = int(page.get("height", ""))
        except ValueError as error:
            raise ValueError("pages.xml page values must be integers") from error
        if index <= 0 or width <= 0 or height <= 0:
            raise ValueError("pages.xml page values must be positive")
        if index in sizes:
            raise ValueError(f"pages.xml contains duplicate page {index}")
        sizes[index] = (width, height)
    return render_dpi, sizes


def _validate_furnitures(
    path: Path,
    page_sizes: dict[int, tuple[int, int]],
    toc_ids: set[int],
) -> None:
    root = _require_xml_root(path, "furnitures")
    if list(root.tag for root in root) != ["patterns", "pages"]:
        raise ValueError("furnitures.xml must contain patterns and pages")
    pattern_ids: set[str] = set()
    positions: set[tuple[str, str]] = set()
    for pattern in root.find("patterns") or []:
        if pattern.tag != "pattern" or set(pattern.attrib) != {"id", "kind"} or pattern.get("kind") not in {"universal", "same_side"}:
            raise ValueError("furnitures.xml has invalid pattern")
        pattern_id = pattern.get("id", "")
        if not pattern_id.isdigit() or pattern_id in pattern_ids:
            raise ValueError("furnitures.xml has invalid pattern id")
        pattern_ids.add(pattern_id)
        for position in pattern:
            position_id = position.get("id", "")
            allowed_attributes = {
                "id", "toc_id", "folio_style", "folio_offset", "folio_prefix", "folio_suffix",
            }
            toc_id = position.get("toc_id")
            folio_style = position.get("folio_style")
            folio_offset = position.get("folio_offset")
            has_folio = folio_style is not None or folio_offset is not None
            valid_folio = (
                folio_style in {"D", "R", "r", "A", "a"}
                and folio_offset is not None
                and folio_offset.lstrip("-").isdigit()
            )
            if (
                position.tag != "position"
                or not set(position.attrib).issubset(allowed_attributes)
                or "id" not in position.attrib
                or not position_id.isdigit()
                or len(position)
                or (has_folio and not valid_folio)
                or (not has_folio and any(
                    name in position.attrib
                    for name in {"folio_prefix", "folio_suffix"}
                ))
                or (not has_folio and not (position.text or "").strip())
                or (has_folio and (position.text or "").strip())
                or not _valid_toc_id(toc_id, toc_ids)
            ):
                raise ValueError("furnitures.xml has invalid position")
            positions.add((pattern_id, position_id))
    seen_pages: set[int] = set()
    for page in root.find("pages") or []:
        if page.tag != "page" or set(page.attrib) != {"index"}:
            raise ValueError("furnitures.xml has invalid page")
        try:
            index = int(page.get("index", ""))
        except ValueError as error:
            raise ValueError("furnitures.xml has invalid page index") from error
        if index not in page_sizes or index in seen_pages:
            raise ValueError("furnitures.xml references an invalid page")
        seen_pages.add(index)
        for section in page:
            if section.tag != "section" or "det" not in section.attrib:
                raise ValueError("furnitures.xml has invalid section")
            _validate_bbox(section.attrib["det"], page_sizes[index], path.name)
            if set(section.attrib).issubset({"det", "toc_id"}):
                toc_id = section.get("toc_id")
                if not _valid_toc_id(toc_id, toc_ids):
                    raise ValueError("furnitures.xml has invalid section toc_id")
                if not len(section) and not (section.text or "").strip():
                    raise ValueError("furnitures.xml fragment is missing content")
                for association in section:
                    if association.tag != "association" or set(association.attrib) != {"kind", "pattern_id", "position_id"} or association.get("kind") not in {"universal", "same_side"} or (association.get("pattern_id", ""), association.get("position_id", "")) not in positions:
                        raise ValueError("furnitures.xml has invalid association")
                if len(section) and (section.text or "").strip():
                    raise ValueError("furnitures.xml association section cannot contain content")
            else:
                raise ValueError("furnitures.xml has invalid section attributes")


def _valid_toc_id(toc_id: str | None, toc_ids: set[int]) -> bool:
    if toc_id is None:
        return True
    return toc_id.isdigit() and int(toc_id) in toc_ids


def _validate_translation(
    path: Path,
    furnitures_path: Path,
    narrative_identities: set[tuple[str, str, str]],
    anchored_identities: set[tuple[str, int, int]],
) -> None:
    """Validate the optional translation coverage sidecar.

    The source content files deliberately remain free of translation state.  A
    translated pcex may instead carry this sidecar to tell a future PDF
    patcher which furniture regions are safe to replace.
    """
    root = _require_xml_root(path, "translation")
    tags = [child.tag for child in root]
    if tags not in (
        ["narrative"], ["furnitures"], ["anchored"],
        ["narrative", "furnitures"], ["narrative", "anchored"],
        ["furnitures", "anchored"], ["narrative", "furnitures", "anchored"],
    ):
        raise ValueError("translation.xml has unsupported coverage sections")

    narrative = root.find("narrative")
    seen_narrative: set[tuple[str, str, str]] = set()
    for entry in narrative or []:
        if entry.tag != "paragraph" or set(entry.attrib) != {"chapter_id", "page_index", "order", "state"}:
            raise ValueError("translation.xml has invalid narrative paragraph")
        identity = (entry.get("chapter_id", ""), entry.get("page_index", ""), entry.get("order", ""))
        if identity not in narrative_identities or identity in seen_narrative:
            raise ValueError("translation.xml references an invalid narrative paragraph")
        if entry.get("state") not in {"translated", "preserved"}:
            raise ValueError("translation.xml has invalid narrative coverage state")
        seen_narrative.add(identity)

    furnitures = root.find("furnitures")
    if furnitures is not None:
        if not furnitures_path.is_file():
            raise ValueError("translation.xml furniture coverage requires furnitures.xml")
        furniture_root = _require_xml_root(furnitures_path, "furnitures")

        positions = {
            (pattern.get("id", ""), position.get("id", ""))
            for pattern in furniture_root.find("patterns") or []
            for position in pattern
        }
        sections = {
            (page.get("index", ""), section.get("det", ""))
            for page in furniture_root.find("pages") or []
            for section in page
            if not list(section)
        }
        seen_positions: set[tuple[str, str]] = set()
        seen_sections: set[tuple[str, str]] = set()
        for entry in furnitures:
            if entry.tag == "position":
                if set(entry.attrib) != {"pattern_id", "position_id", "state"}:
                    raise ValueError("translation.xml has invalid furniture position")
                identity = (entry.get("pattern_id", ""), entry.get("position_id", ""))
                if identity not in positions or identity in seen_positions:
                    raise ValueError("translation.xml references an invalid furniture position")
                seen_positions.add(identity)
            elif entry.tag == "section":
                if set(entry.attrib) != {"page_index", "det", "state"}:
                    raise ValueError("translation.xml has invalid furniture section")
                identity = (entry.get("page_index", ""), entry.get("det", ""))
                if identity not in sections or identity in seen_sections:
                    raise ValueError("translation.xml references an invalid furniture section")
                seen_sections.add(identity)
            else:
                raise ValueError("translation.xml has invalid furniture entry")
            if entry.get("state") not in {"translated", "preserved"}:
                raise ValueError("translation.xml has invalid furniture coverage state")

    anchored = root.find("anchored")
    if anchored is None:
        return
    seen_anchored: set[tuple[str, int, int]] = set()
    for entry in anchored:
        if entry.tag != "asset" or set(entry.attrib) != {
            "chapter_id", "flow_index", "child_index", "state",
        }:
            raise ValueError("translation.xml has invalid anchored asset")
        try:
            identity = (
                entry.get("chapter_id", ""),
                int(entry.get("flow_index", "")),
                int(entry.get("child_index", "")),
            )
        except ValueError as error:
            raise ValueError("translation.xml has invalid anchored asset identity") from error
        if identity not in anchored_identities or identity in seen_anchored:
            raise ValueError("translation.xml references an invalid anchored asset")
        if entry.get("state") not in {"translated", "preserved"}:
            raise ValueError("translation.xml has invalid anchored coverage state")
        seen_anchored.add(identity)


def _require_xml_root(path: Path, expected: str) -> ElementTree.Element:
    if not path.is_file():
        raise ValueError(f"PDFCraftExtraction is missing {path.name}")
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as error:
        raise ValueError(f"invalid PDFCraftExtraction XML: {path.name}") from error
    if root.tag != expected:
        raise ValueError(f"expected <{expected}> in {path.name}, got <{root.tag}>")
    return root


def _validate_bbox(raw: str, size: tuple[int, int], chapter: str) -> None:
    try:
        values = tuple(int(value) for value in raw.split(","))
    except (ValueError, TypeError) as error:
        raise ValueError(f"invalid bbox in {chapter}: {raw}") from error
    if len(values) != 4:
        raise ValueError(f"invalid bbox in {chapter}: {raw}")
    left, top, right, bottom = values
    width, height = size
    if left < 0 or top < 0 or right <= left or bottom <= top or right > width or bottom > height:
        raise ValueError(f"bbox exceeds page geometry in {chapter}: {raw}")


def _validate_workspace_members(paths: ExtractionPaths) -> None:
    allowed_root = {
        paths.manifest.name, paths.pages.name, paths.chapters.name, paths.assets.name,
        paths.toc.name, paths.cover.name, paths.furnitures.name, paths.translation.name,
    }
    for path in paths.root.iterdir():
        if path.name not in allowed_root or path.is_symlink():
            raise ValueError(f"unsupported PDFCraftExtraction member: {path.name}")
    for path in paths.chapters.iterdir():
        valid = path.is_file() and not path.is_symlink() and (
            path.name == "chapter_head.xml"
            or (
                path.name.startswith("chapter_")
                and path.name.endswith(".xml")
                and path.name[8:-4].isdigit()
            )
        )
        if not valid:
            raise ValueError(f"invalid chapter member: {path.name}")
    for path in paths.assets.iterdir():
        valid = (
            path.is_file()
            and not path.is_symlink()
            and path.name.endswith(".png")
            and _is_asset_hash(path.name[:-4])
        )
        if not valid:
            raise ValueError(f"invalid asset member: {path.name}")


def _write_archive(paths: ExtractionPaths, target: Path) -> None:
    manifest = _read_manifest(paths.manifest)
    legacy = manifest["format_version"] in {1, 2}
    members: list[tuple[Path, str]] = [
        (paths.pages, "pages.xml"),
    ]
    if paths.furnitures.exists():
        members.append((paths.furnitures, "furnitures.xml"))
    if paths.translation.exists():
        members.append((paths.translation, "translation.xml"))
    if paths.toc.is_file():
        members.append((paths.toc, "toc.xml"))
    if paths.cover.is_file():
        members.append((paths.cover, "cover.png"))
    members.extend((path, f"chapters/{path.name}") for path in sorted(paths.chapters.glob("*.xml")))
    members.extend((path, f"assets/{path.name}") for path in sorted(paths.assets.glob("*.png")))

    with NamedTemporaryFile(dir=target.parent, suffix=EXTRACTION_SUFFIX, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("chapters/", b"")
            archive.writestr("assets/", b"")
            if legacy:
                archive.writestr(
                    "manifest.json",
                    json.dumps(_v3_manifest(manifest), ensure_ascii=False, indent=2).encode(),
                )
            else:
                archive.write(paths.manifest, "manifest.json")
            for source, member in members:
                if legacy and member.startswith("chapters/"):
                    archive.writestr(member, _v3_chapter_xml(source))
                else:
                    archive.write(source, member)
        temporary_path.replace(target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _v3_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize a validated v1/v2 manifest at the public export boundary."""
    document = manifest["document"]
    raw_authors = document.get("authors", [])
    authors = (
        [{"name": author, "original_name": None, "nationality": None} for author in raw_authors]
        if manifest["format_version"] == 1 else raw_authors
    )
    return {
        "format_version": FORMAT_VERSION,
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

    root = _require_xml_root(path, "chapter")
    chapter = decode_chapter(root, allow_legacy=True)
    return ElementTree.tostring(encode_chapter(chapter), encoding="utf-8", xml_declaration=True)


def _extract_archive(archive_path: Path, target: Path) -> None:
    try:
        with ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("PDFCraftExtraction contains duplicate ZIP members")
            for info in infos:
                _validate_archive_member(info)
            broken = archive.testzip()
            if broken is not None:
                raise ValueError(f"PDFCraftExtraction contains a corrupt member: {broken}")
            archive.extractall(target)
    except (BadZipFile, RuntimeError) as error:
        raise ValueError("invalid or corrupt PDFCraftExtraction archive") from error


def _validate_archive_member(info: ZipInfo) -> None:
    name = info.filename
    pure = PurePosixPath(name)
    normalized = pure.as_posix() + ("/" if info.is_dir() else "")
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "\\" in name
        or name != normalized
    ):
        raise ValueError(f"unsafe PDFCraftExtraction member path: {name}")
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ValueError(f"PDFCraftExtraction cannot contain symlinks: {name}")
    allowed = name in {
        "manifest.json", "pages.xml", "toc.xml", "cover.png", "furnitures.xml", "translation.xml", "chapters/", "assets/"
    }
    allowed = allowed or (
        len(pure.parts) == 2
        and pure.parts[0] == "chapters"
        and (pure.parts[1] == "chapter_head.xml" or (
            pure.parts[1].startswith("chapter_")
            and pure.parts[1].endswith(".xml")
            and pure.parts[1][8:-4].isdigit()
        ))
    )
    allowed = allowed or (
        len(pure.parts) == 2
        and pure.parts[0] == "assets"
        and pure.parts[1].endswith(".png")
        and _is_asset_hash(pure.parts[1][:-4])
    )
    if not allowed:
        raise ValueError(f"unsupported PDFCraftExtraction member: {name}")


def _require_pcex_path(path: Path) -> None:
    if path.suffix.lower() != EXTRACTION_SUFFIX:
        raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")


def _producer_version() -> str:
    try:
        return version("pdf-craft")
    except PackageNotFoundError:
        return "unknown"


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _metadata_from_book_meta(metadata: BookMeta) -> DocumentMetadata:
    return DocumentMetadata(
        title=metadata.title,
        description=metadata.description,
        publisher=metadata.publisher,
        isbn=metadata.isbn,
        authors=tuple(DocumentAuthor(name=name) for name in metadata.authors),
        editors=tuple(metadata.editors),
        translators=tuple(metadata.translators),
        modified=metadata.modified,
    )


def _is_asset_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("document contributor metadata must be arrays of strings")
    return list(value)


def _author_list(value: object) -> list[DocumentAuthor]:
    if not isinstance(value, list):
        raise ValueError("document authors must be an array")
    authors: list[DocumentAuthor] = []
    for author in value:
        if not isinstance(author, dict) or set(author) != {"name", "original_name", "nationality"}:
            raise ValueError("document author metadata is invalid")
        name = author.get("name")
        original_name = author.get("original_name")
        nationality = author.get("nationality")
        if not isinstance(name, str) or not name:
            raise ValueError("document author name must be a non-empty string")
        if original_name is not None and not isinstance(original_name, str):
            raise ValueError("document author original_name must be a string or null")
        if nationality is not None and not isinstance(nationality, str):
            raise ValueError("document author nationality must be a string or null")
        authors.append(DocumentAuthor(name, original_name, nationality))
    return authors


def _author_names(value: object) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return [author.name for author in _author_list(value)]
