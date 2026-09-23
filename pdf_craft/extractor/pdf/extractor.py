# pylint: disable=protected-access

import asyncio
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from ...document import PDFCraftExtraction
from ...document.package import EXTRACTION_SUFFIX
from ...runtime import OCR_DOMAIN, callback_bridge, run_cancellable


class PDFExtractor:
    """PDF front end that emits a public ``.pcex`` extraction."""

    def __init__(self, transform: Any) -> None:
        self._transform = transform

    def _extract_blocking(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ) -> PDFCraftExtraction:
        extraction, _ = self._extract_with_metering_sync(
            pdf_path, extraction_path, analysing_path=analysing_path, **kwargs,
        )
        return extraction

    def _extract_with_metering_sync(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ):
        if extraction_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        if extraction_path.exists():
            raise FileExistsError(f"PDFCraftExtraction already exists: {extraction_path}")
        with _analysis_workspace(analysing_path) as workspace:
            extraction, metering = self._extract_to_workspace(pdf_path, workspace, **kwargs)
            exported = extraction._export(extraction_path)
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
        extraction._validate()
        return extraction, metering

    async def extract(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ) -> PDFCraftExtraction:
        """Extract without blocking the caller's event loop."""
        extraction, _ = await self.extract_with_metering(
            pdf_path, extraction_path, analysing_path=analysing_path, **kwargs
        )
        return extraction

    async def extract_with_metering(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        **kwargs: Any,
    ):
        """Keep the complete synchronous OCR generator on one OCR worker."""
        kwargs = await self._prepare_async(pdf_path, kwargs)
        original_aborted = kwargs.get("aborted")
        on_ocr_event = callback_bridge(
            asyncio.get_running_loop(), kwargs.get("on_ocr_event"),
        )

        def execute(aborted):
            worker_kwargs = dict(kwargs)
            worker_kwargs["aborted"] = aborted
            worker_kwargs["on_ocr_event"] = on_ocr_event
            return self._extract_with_metering_sync(
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
        kwargs = await self._prepare_async(pdf_path, kwargs)
        original_aborted = kwargs.get("aborted")
        on_ocr_event = callback_bridge(
            asyncio.get_running_loop(), kwargs.get("on_ocr_event"),
        )

        def execute(aborted):
            worker_kwargs = dict(kwargs)
            worker_kwargs["aborted"] = aborted
            worker_kwargs["on_ocr_event"] = on_ocr_event
            return self._extract_to_workspace(pdf_path, analysing_path, **worker_kwargs)

        return await run_cancellable(
            OCR_DOMAIN,
            execute,
            original_aborted=original_aborted,
        )

    async def _prepare_async(
        self,
        pdf_path: Path,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        prepare = getattr(self._transform, "prepare_extract", None)
        if prepare is None:
            return kwargs
        prepared = await prepare(pdf_path=pdf_path, **kwargs)
        return {**kwargs, **prepared}


@contextmanager
def _analysis_workspace(path: Path | None) -> Iterator[Path]:
    if path is not None:
        yield path
        return
    with TemporaryDirectory(prefix="pdf-craft-analysis-") as directory:
        yield Path(directory)
