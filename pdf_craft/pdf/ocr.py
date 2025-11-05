from pathlib import Path
from xml.etree.ElementTree import tostring
from doc_page_extractor import DeepSeekOCRSize

from ..asset import AssetHub
from .extractor import Extractor
from .page import encode, Page


def ocr_pdf(
        pdf_path: Path,
        asset_path: Path,
        ocr_path: Path,
        model_size: DeepSeekOCRSize,
        includes_footnotes: bool,
        plot_path: Path | None = None,
    ):
    asset_hub = AssetHub(asset_path)
    executor = Extractor(asset_hub)
    ocr_path.mkdir(parents=True, exist_ok=True)

    with executor.page_refs(pdf_path) as refs:
        if plot_path is not None:
            plot_path.mkdir(parents=True, exist_ok=True)
        for ref in refs:
            filename = f"page_{ref.page_index}.xml"
            file_path = ocr_path / filename
            if not file_path.exists():
                page = ref.extract(
                    model_size=model_size,
                    includes_footnotes=includes_footnotes,
                    plot_path=plot_path,
                )
                _save_page_to_xml(page, file_path)

def _save_page_to_xml(page: Page, file_path: Path) -> None:
    # 使用临时文件确保写入的原子性
    page_element = encode(page)
    xml_string = tostring(page_element, encoding="unicode")
    temp_path = file_path.with_suffix(".xml.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(xml_string)
        temp_path.replace(file_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise