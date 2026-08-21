from collections.abc import Generator
from pathlib import Path
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
                    yield opf_dir / href, media_type
