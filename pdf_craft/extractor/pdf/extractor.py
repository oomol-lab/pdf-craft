from pdf_craft.usage import UsageCollector
from ..progress import extraction_operation, active_extraction
# pylint: disable=unused-argument

from collections.abc import Mapping
from pdf_craft.pdf.backend import pdf_backend_scope, poppler_path_for
from pdf_craft.policies import RetryPolicy, ErrorPolicyInput
from pdf_craft._policy import OMITTED, policy_operation
# pylint: disable=protected-access

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from ...document import PDFCraftExtraction
from ...document.package import EXTRACTION_SUFFIX
from ...runtime import IO_DOMAIN, temporary_directory


class PDFExtractor:
    """PDF front end that emits a public ``.pcex`` extraction."""

    def __init__(self, transform: Any) -> None:
        self._transform = transform

    def _backend_poppler_path(self) -> Path | None:
        """Share the native adapter's tool configuration with nested readers."""
        return poppler_path_for(self._transform)

    @staticmethod
    def _load_workspace(analysing_path: Path, metering):
        extraction = PDFCraftExtraction._from_workspace(analysing_path / "extraction")
        extraction._validate()
        return extraction, metering

    @policy_operation
    @pdf_backend_scope
    async def extract(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
        **kwargs: Any,
    ) -> PDFCraftExtraction:
        """Extract without blocking the caller's event loop."""
        extraction, _ = await self.extract_with_metering(
            pdf_path, extraction_path, analysing_path=analysing_path, **kwargs
        )
        return extraction

    @policy_operation
    @pdf_backend_scope
    @extraction_operation
    async def extract_with_metering(
        self,
        pdf_path: Path,
        extraction_path: Path,
        *,
        analysing_path: Path | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
        usage: UsageCollector | None = None,
        **kwargs: Any,
    ):
        if extraction_path.suffix.lower() != EXTRACTION_SUFFIX:
            raise ValueError(f"PDFCraftExtraction path must end with {EXTRACTION_SUFFIX}")
        if await IO_DOMAIN.run(extraction_path.exists):
            raise FileExistsError(f"PDFCraftExtraction already exists: {extraction_path}")
        async with _analysis_workspace_async(analysing_path) as workspace:
            extraction, metering = await self._extract_to_workspace_async(
                pdf_path, workspace, **kwargs,
            )
            exported = await extraction._export_async(extraction_path)
        return exported, metering

    @pdf_backend_scope
    @extraction_operation
    async def _extract_to_workspace_async(
        self,
        pdf_path: Path,
        analysing_path: Path,
        **kwargs: Any,
    ):
        kwargs.pop('on_extraction_event', None)
        progress = active_extraction()
        if progress is not None:
            previous = kwargs.get('on_ocr_event')
            async def native_event(event):
                from ...runtime import invoke_callback
                await progress.ocr(event)
                await invoke_callback(previous, event)
            kwargs['on_ocr_event'] = native_event
        kwargs = await self._prepare_async(pdf_path, kwargs)
        original_aborted = kwargs.get("aborted")
        on_ocr_event = kwargs.get("on_ocr_event")
        defaults = {
                "analysing_path": analysing_path,
                "ocr_size": "gundam", "dpi": None,
                "max_page_image_file_size": None, "includes_cover": False,
                "includes_footnotes": False, "includes_furniture": True,
                "generate_plot": False, "toc_llm": None,
                "toc_assumed": False, "extract_book_metadata": False,
                "metadata_llm": None, "aborted": original_aborted or (lambda: False),
                "max_tokens": None, "max_output_tokens": None,
                "on_ocr_event": on_ocr_event, "page_indexes": None,
                "footnote_refinement": None,
        }
        defaults.update(kwargs)
        defaults["analysing_path"] = analysing_path
        defaults["aborted"] = original_aborted or (lambda: False)
        defaults["on_ocr_event"] = on_ocr_event
        _, _, _, _, metering = await self._transform.extract_package_async(
            pdf_path=pdf_path,
            **defaults,
        )
        return await IO_DOMAIN.run(self._load_workspace, analysing_path, metering)

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

@asynccontextmanager
async def _analysis_workspace_async(
    path: Path | None,
) -> AsyncIterator[Path]:
    if path is not None:
        yield path
        return
    async with temporary_directory("pdf-craft-analysis-") as directory:
        yield directory
