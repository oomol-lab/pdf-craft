from pathlib import Path
from typing import Any
from ...document import DocumentPackage

class PDFExtractor:
    """Public PDF extraction boundary. Heavy OCR imports remain lazy."""
    def __init__(self, transform: Any) -> None:
        self._transform = transform

    def extract(self, pdf_path: Path, package_path: Path, **kwargs: Any) -> DocumentPackage:
        package, _ = self.extract_with_metering(pdf_path, package_path, **kwargs)
        return package

    def extract_with_metering(self, pdf_path: Path, package_path: Path, **kwargs: Any):
        package_path.mkdir(parents=True, exist_ok=True)
        defaults = {
            "analysing_path": package_path,
            "ocr_size": "gundam", "dpi": None,
            "max_page_image_file_size": None, "includes_cover": False,
            "includes_footnotes": False, "ignore_pdf_errors": False,
            "ignore_ocr_errors": False, "generate_plot": False,
            "toc_llm": None, "toc_assumed": False,
            "aborted": lambda: False, "max_tokens": None,
            "max_output_tokens": None, "on_ocr_event": lambda _: None,
            "page_indexes": None,
        }
        defaults.update(kwargs)
        defaults["analysing_path"] = package_path
        _, _, _, _, metering = self._transform.extract_package(pdf_path=pdf_path,
            **defaults)
        package = DocumentPackage.from_path(package_path)
        package.validate()
        return package, metering
