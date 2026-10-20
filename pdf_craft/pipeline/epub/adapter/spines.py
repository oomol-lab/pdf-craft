from collections.abc import Generator
from pathlib import Path
from posixpath import normpath
from xml.etree import ElementTree as ET

from .common import find_opf_path, strip_namespace
from .zip import Zip


# yield file_path, media_type
def search_spine_paths(zip: Zip) -> Generator[tuple[Path, str], None, None]:
    opf_path = find_opf_path(zip)
    opf_dir = opf_path.parent

    with zip.read(opf_path) as f:
        content = f.read()
        root = ET.fromstring(content)
        strip_namespace(root)

        manifest = root.find(".//manifest")
        if manifest is None:
            return

        manifest_items = {}
        for item in manifest.findall("item"):
            item_id = item.get("id")
            item_href = item.get("href")
            media_type = item.get("media-type", "")
            if item_id and item_href:
                manifest_items[item_id] = (item_href, media_type)

        spine = root.find(".//spine")
        if spine is None:
            return

        for itemref in spine.findall("itemref"):
            idref = itemref.get("idref")
            if not idref:
                continue

            if idref in manifest_items:
                href, media_type = manifest_items[idref]
                if media_type in ("application/xhtml+xml", "text/html"):
                    yield Path(normpath((opf_dir / href).as_posix())), media_type


def spine_cfi_paths(zip: Zip) -> dict[Path, str]:
    """Package-component paths use actual OPF child positions, not file order."""
    opf_path = find_opf_path(zip)
    with zip.read(opf_path) as file:
        root = ET.fromstring(file.read())
    strip_namespace(root)
    manifest = root.find("manifest")
    spine = root.find("spine")
    if manifest is None or spine is None:
        return {}
    hrefs = {item.get("id"): item.get("href") for item in manifest}
    step = (list(root).index(spine) + 1) * 2
    result = {}
    for index, item in enumerate(spine):
        href = hrefs.get(item.get("idref"))
        if href:
            result[Path(normpath((opf_path.parent / href).as_posix()))] = f"/{step}/{(index + 1) * 2}"
    return result
