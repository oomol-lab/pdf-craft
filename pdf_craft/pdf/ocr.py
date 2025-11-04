from pathlib import Path
from xml.etree.ElementTree import tostring
from doc_page_extractor import DeepSeekOCRSize

from .asset import AssetHub
from .extractor import Extractor
from .page import encode, Page


def ocr_pdf(
        pdf_path: Path, 
        asset_path: Path,
        ocr_path: Path,
        model_size: DeepSeekOCRSize,
        includes_footnotes: bool,
    ):
    asset_hub = AssetHub(asset_path)
    executor = Extractor(asset_hub)
    ocr_path.mkdir(parents=True, exist_ok=True)
    
    for page in executor.extract(
        pdf_path=pdf_path,
        model_size=model_size,
        includes_footnotes=includes_footnotes,
    ):
        _save_page_to_xml(page, ocr_path)

def _save_page_to_xml(page: Page, ocr_path: Path) -> None:
    """将 page 对象保存为 XML 文件"""
    filename = f"page_{page.index}.xml"
    file_path = ocr_path / filename
    page_element = encode(page)
    xml_string = tostring(page_element, encoding="unicode")
    
    # 写入文件
    with open(file_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(xml_string)