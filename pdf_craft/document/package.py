"""The public PDF Craft Extraction artifact and its internal workspace storage."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Protocol
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo

from epub_generator import BookMeta

from ..common import indent, save_xml


FORMAT_VERSION = 1
EXTRACTION_SUFFIX = ".pcex"
_MANIFEST_FIELDS = {"format_version", "producer", "created_at", "document"}
_DOCUMENT_FIELDS = {
    "title", "description", "publisher", "isbn", "authors", "editors",
    "translators", "modified", "language",
}


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
    """Validated archive snapshot materialized once for an extraction's lifetime."""

    def __init__(self, path: Path, *, eager: bool = True) -> None:
        self.path = path
        self._temporary: TemporaryDirectory[str] | None = None
        self._paths: ExtractionPaths | None = None
        if eager:
            self._ensure_materialized()

    def _ensure_materialized(self) -> ExtractionPaths:
        if self._paths is not None:
            return self._paths
        temporary = TemporaryDirectory(prefix="pdf-craft-extraction-")
        paths = ExtractionPaths.at(Path(temporary.name))
        try:
            _extract_archive(self.path, paths.root)
            _validate_workspace(paths)
        except Exception:
            temporary.cleanup()
            raise
        self._temporary = temporary
        self._paths = paths
        return paths

    @contextmanager
    def materialize(self) -> Iterator[ExtractionPaths]:
        yield self._ensure_materialized()


class PDFCraftExtraction:
    """A structured PDF extraction backed by a workspace or ``.pcex`` archive.

    Directory-backed instances are internal and keep one-shot conversion paths
    fast. Public persistence and interchange use :meth:`open` and :meth:`export`.
    """

    def __init__(self, path: str | Path) -> None:
        archive = Path(path)
        _require_pcex_path(archive)
        if not archive.is_file():
            raise FileNotFoundError(f"PDFCraftExtraction does not exist: {archive}")
        self._storage: _Storage = _ArchiveStorage(archive)

    @classmethod
    def open(cls, path: str | Path) -> "PDFCraftExtraction":
        """Load and validate a public ``.pcex`` artifact."""
        return cls(path)

    @classmethod
    def load(cls, path: str | Path) -> "PDFCraftExtraction":
        """Alias for :meth:`open` for callers that prefer artifact terminology."""
        return cls(path)

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
        extraction._storage = _ArchiveStorage(path, eager=False)
        return extraction

    @contextmanager
    def _materialize(self) -> Iterator[ExtractionPaths]:
        with self._storage.materialize() as paths:
            yield paths

    def validate(self, *, require_toc: bool = False) -> "PDFCraftExtraction":
        with self._materialize() as paths:
            _validate_workspace(paths, require_toc=require_toc)
        return self

    def export(self, path: str | Path) -> "PDFCraftExtraction":
        target = Path(path)
        _require_pcex_path(target)
        if target.exists():
            raise FileExistsError(f"PDFCraftExtraction already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._materialize() as paths:
            _validate_workspace(paths)
            _write_archive(paths, target)
        return PDFCraftExtraction._from_exported_archive(target)

    def page_pixel_sizes(self) -> dict[int, tuple[int, int]]:
        with self._materialize() as paths:
            return _read_pages(paths.pages)[1]

    def render_dpi(self) -> int:
        with self._materialize() as paths:
            return _read_pages(paths.pages)[0]

    def book_meta(self) -> BookMeta | None:
        document = self.document_metadata()
        if not document:
            return None
        modified = document.get("modified")
        parsed_modified = datetime.fromisoformat(modified) if isinstance(modified, str) else None
        return BookMeta(
            title=_optional_string(document.get("title")),
            description=_optional_string(document.get("description")),
            publisher=_optional_string(document.get("publisher")),
            isbn=_optional_string(document.get("isbn")),
            authors=_string_list(document.get("authors")),
            editors=_string_list(document.get("editors")),
            translators=_string_list(document.get("translators")),
            modified=parsed_modified,
        )

    def language(self) -> str | None:
        return _optional_string(self.document_metadata().get("language"))

    def document_metadata(self) -> dict[str, Any]:
        with self._materialize() as paths:
            return dict(_read_manifest(paths.manifest)["document"])


def write_manifest(
    root: Path,
    *,
    book_meta: BookMeta | None,
    language: str | None = None,
) -> None:
    """Write the format manifest at the extraction boundary."""
    metadata = book_meta or BookMeta()
    modified = metadata.modified.isoformat() if metadata.modified is not None else None
    payload = {
        "format_version": FORMAT_VERSION,
        "producer": {"name": "pdf-craft", "version": _producer_version()},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "document": {
            "title": metadata.title,
            "description": metadata.description,
            "publisher": metadata.publisher,
            "isbn": metadata.isbn,
            "authors": list(metadata.authors),
            "editors": list(metadata.editors),
            "translators": list(metadata.translators),
            "modified": modified,
            "language": language,
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
    from ..extractor.chapter.chapter import decode as decode_chapter

    _read_manifest(paths.manifest)
    _, page_sizes = _read_pages(paths.pages)
    if not paths.chapters.is_dir():
        raise ValueError("PDFCraftExtraction is missing chapters directory")
    if not paths.assets.is_dir():
        raise ValueError("PDFCraftExtraction is missing assets directory")
    if require_toc and not paths.toc.is_file():
        raise ValueError("PDFCraftExtraction is missing toc.xml")
    if paths.toc.exists():
        _require_xml_root(paths.toc, "toc")
    if paths.cover.exists() and not paths.cover.is_file():
        raise ValueError("PDFCraftExtraction cover.png is not a file")
    _validate_workspace_members(paths)

    chapter_paths = list(paths.chapters.glob("chapter_*.xml"))
    for path in chapter_paths:
        if path.name != "chapter_head.xml":
            suffix = path.stem.removeprefix("chapter_")
            if not suffix.isdigit():
                raise ValueError(f"invalid chapter filename: {path.name}")
        root = _require_xml_root(path, "chapter")
        try:
            decode_chapter(root)
        except ValueError as error:
            raise ValueError(f"invalid chapter schema in {path.name}: {error}") from error
        for element in root.iter():
            page_index = element.get("page_index")
            det = element.get("det")
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
            asset_hash = element.get("hash") if element.tag == "asset" else None
            if asset_hash is not None:
                if not _is_asset_hash(asset_hash):
                    raise ValueError(f"invalid asset hash in {path.name}: {asset_hash}")
                if not (paths.assets / f"{asset_hash}.png").is_file():
                    raise ValueError(f"{path.name} references missing asset: {asset_hash}.png")


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("PDFCraftExtraction is missing manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid PDFCraftExtraction manifest.json") from error
    if not isinstance(payload, dict) or set(payload) - _MANIFEST_FIELDS:
        raise ValueError("manifest.json contains unsupported fields")
    if payload.get("format_version") != FORMAT_VERSION:
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
    if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
        raise ValueError("manifest.json has invalid document metadata")
    for key in ("title", "description", "publisher", "isbn", "modified", "language"):
        value = document.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"manifest.json document.{key} must be a string or null")
    for key in ("authors", "editors", "translators"):
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
        paths.toc.name, paths.cover.name,
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
    members: list[tuple[Path, str]] = [
        (paths.manifest, "manifest.json"),
        (paths.pages, "pages.xml"),
    ]
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
            for source, member in members:
                archive.write(source, member)
        temporary_path.replace(target)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


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
        "manifest.json", "pages.xml", "toc.xml", "cover.png", "chapters/", "assets/"
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


def _is_asset_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("document contributor metadata must be arrays of strings")
    return list(value)
