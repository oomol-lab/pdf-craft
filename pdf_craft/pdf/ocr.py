from pathlib import Path
from xml.etree.ElementTree import tostring
from doc_page_extractor import DeepSeekOCRSize

from ..common import save_xml, AssetHub
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
                save_xml(encode(page), file_path)