# pylint: disable=protected-access

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from ...document import PDFCraftExtraction
from ...document.package import EXTRACTION_SUFFIX
from ...runtime import OCR_DOMAIN, require_sync_context, run_cancellable


class PDFExtractor:
    """PDF front end that emits a public ``.pcex`` extraction."""

    def __init__(self, transform: Any) -> None:
        self._transform = transform

    def extract(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ) -> PDFCraftExtraction:
        require_sync_context()
        extraction, _ = self.extract_with_metering(
            pdf_path, extraction_path, analysing_path=analysing_path, **kwargs
        )
        return extraction

    def extract_with_metering(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ):
        require_sync_context()
        if extraction_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        if extraction_path.exists():
            raise FileExistsError(f"PDFCraftExtraction already exists: {extraction_path}")
        with _analysis_workspace(analysing_path) as workspace:
            extraction, metering = self._extract_to_workspace(pdf_path, workspace, **kwargs)
            exported = extraction.export(extraction_path)
        return exported, metering

    def _extract_to_workspace(
        self, pdf_path: Path, analysing_path: Path, **kwargs: Any
    ):
        """Internal fast path used by complete conversions without ZIP churn."""
        analysing_path.mkdir(parents=True, exist_ok=True)
        defaults = {
            "analysing_path": analysing_path,
            "ocr_size": "gundam", "dpi": None,
            "max_page_image_file_size": None, "includes_cover": False,
            "includes_footnotes": False, "includes_furniture": True, "ignore_pdf_errors": False,
            "ignore_ocr_errors": False, "generate_plot": False,
            "toc_llm": None, "toc_assumed": False,
            "extract_book_metadata": False, "metadata_llm": None,
            "aborted": lambda: False, "max_tokens": None,
            "max_output_tokens": None, "on_ocr_event": lambda _: None,
            "page_indexes": None,
        }
        defaults.update(kwargs)
        defaults["analysing_path"] = analysing_path
        _, _, _, _, metering = self._transform.extract_package(pdf_path=pdf_path, **defaults)
        extraction = PDFCraftExtraction._from_workspace(analysing_path / "extraction")
        extraction.validate()
        return extraction, metering

    async def extract_async(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ) -> PDFCraftExtraction:
        """Extract without blocking the caller's event loop."""
        extraction, _ = await self.extract_with_metering_async(
            pdf_path, extraction_path, analysing_path=analysing_path, **kwargs
        )
        return extraction

    async def extract_with_metering_async(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ):
        """Keep the complete synchronous OCR generator on one OCR worker."""
        original_aborted = kwargs.get("aborted")

        def execute(aborted):
            worker_kwargs = dict(kwargs)
            worker_kwargs["aborted"] = aborted
            return self.extract_with_metering(
                pdf_path,
                extraction_path,
                analysing_path=analysing_path,
                **worker_kwargs,
            )

        return await run_cancellable(
            OCR_DOMAIN,
            execute,
            original_aborted=original_aborted,
        )

    async def _extract_to_workspace_async(
        self,
        pdf_path: Path,
        analysing_path: Path,
        **kwargs: Any,
    ):
        original_aborted = kwargs.get("aborted")

        def execute(aborted):
            worker_kwargs = dict(kwargs)
            worker_kwargs["aborted"] = aborted
            return self._extract_to_workspace(pdf_path, analysing_path, **worker_kwargs)

        return await run_cancellable(
            OCR_DOMAIN,
            execute,
            original_aborted=original_aborted,
        )


@contextmanager
def _analysis_workspace(path: Path | None) -> Iterator[Path]:
    if path is not None:
        yield path
        return
    with TemporaryDirectory(prefix="pdf-craft-analysis-") as directory:
        yield Path(directory)
