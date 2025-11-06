from pathlib import Path
from os import PathLike

from ..common import save_xml, AssetHub
from .types import encode, DeepSeekOCRModel


def predownload_models(models_cache_path: PathLike | None = None) -> None:
    from .extractor import predownload # 尽可能推迟 doc-page-extractor 的加载时间
    predownload(models_cache_path)

def ocr_pdf(
        pdf_path: Path,
        asset_path: Path,
        ocr_path: Path,
        model: DeepSeekOCRModel,
        includes_footnotes: bool,
        models_cache_path: PathLike | None = None,
        plot_path: Path | None = None,
    ):
    from .extractor import Extractor # 尽可能推迟 doc-page-extractor 的加载时间
    asset_hub = AssetHub(asset_path)
    executor = Extractor(asset_hub, models_cache_path)
    ocr_path.mkdir(parents=True, exist_ok=True)

    with executor.page_refs(pdf_path) as refs:
        if plot_path is not None:
            plot_path.mkdir(parents=True, exist_ok=True)
        for ref in refs:
            filename = f"page_{ref.page_index}.xml"
            file_path = ocr_path / filename
            if not file_path.exists():
                page = ref.extract(
                    model=model,
                    includes_footnotes=includes_footnotes,
                    plot_path=plot_path,
                )
                save_xml(encode(page), file_path)