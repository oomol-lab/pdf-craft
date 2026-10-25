"""Synchronous wrapper around :class:`AsyncPDFCraft`."""
from ..policies import RetryPolicy, ErrorPolicyInput
from .._policy import OMITTED

from collections.abc import Callable, Mapping
from os import PathLike

from epub_generator import BookMeta, LaTeXRender, TableRender

from ..craft import AsyncPDFCraft, ExtractionOptions, PDFOptions
from ..document import PDFCraftExtraction, RenderMode, TranslationInfo
from ..document.access import PageGeometry
from ..document.metadata import MetadataCallback
from ..document.paragraph import ParagraphContent, SourceRegion, PDFParagraphContent
from ..pipeline.epub.events import EpubFragmentContent

from ..concurrency import AsyncExecutor
from ..llm import LLM
from ..metering import AbortedCheck, OCRTokensMetering
from ..ocr_config import OCRConfig
from ..runtime import run_sync
from ..transformer import (
    AnchoredContentTransformer,
    SubmitKind,
    TranslationEvent,
    XMLTranslator,
)


class PDFCraft:
    """Synchronous facade over the async-first SDK."""

    def __init__(self, pdf: PDFOptions | None = None, *, retry_policy: RetryPolicy | None = None,
                 error_policy: ErrorPolicyInput = None, _engine=None) -> None:
        self._async_craft = AsyncPDFCraft(pdf=pdf, retry_policy=retry_policy, error_policy=error_policy, _engine=_engine)

    @classmethod
    def from_engine(cls, engine) -> "PDFCraft":
        return cls(_engine=engine)

    def predownload_models(
        self, *, models_cache_path: PathLike | None = None,
        revision: str | None = None, ocr: OCRConfig | None = None,
    ) -> None:
        return run_sync(self._async_craft.predownload_models(
            models_cache_path=models_cache_path, revision=revision, ocr=ocr,
        ))

    def open_extraction(self, path: PathLike | str) -> PDFCraftExtraction:
        return run_sync(self._async_craft.open_extraction(path))

    def export_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, path: PathLike | str,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.export_extraction(extraction, path))

    def read_pages(self, extraction: PDFCraftExtraction | PathLike | str) -> dict[int, PageGeometry]:
        return run_sync(self._async_craft.read_pages(
            extraction,
        ))

    def read_asset(self, extraction: PDFCraftExtraction | PathLike | str, asset_hash: str) -> bytes:
        return run_sync(self._async_craft.read_asset(
            extraction, asset_hash,
        ))

    def list_translations(
        self, extraction: PDFCraftExtraction | PathLike | str,
    ) -> tuple[TranslationInfo, ...]:
        return run_sync(self._async_craft.list_translations(extraction))

    def resolve_translation(
        self, extraction: PDFCraftExtraction | PathLike | str,
        translation_id: str | None = None,
    ) -> TranslationInfo:
        return run_sync(self._async_craft.resolve_translation(extraction, translation_id))

    def extract_pdf(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.extract_pdf(
            source, extraction_path, options, analysing_path=analysing_path,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def extract_pdf_with_metering(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        return run_sync(self._async_craft.extract_pdf_with_metering(
            source, extraction_path, options, analysing_path=analysing_path,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
        aborted: AbortedCheck = lambda: False,
        translator: XMLTranslator | None = None, window: int = 1,
        with_anchored: bool = True,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> None:
        return run_sync(self._async_craft.render_markdown(
            extraction, output, assets_path,
            mode=mode,
            translation_id=translation_id,
            aborted=aborted,
            translator=translator,
            window=window,
            with_anchored=with_anchored,
            on_body=on_body,
            on_footnote=on_footnote,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: XMLTranslator,
        *, window: int = 1,
        with_anchored: bool = True,
        with_furniture: bool = False,
        translation_id: str | None = None,
        target_language: str | None = None,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.translate_extraction(
            extraction, output_path, translator,
            window=window,
            with_anchored=with_anchored,
            with_furniture=with_furniture,
            translation_id=translation_id,
            target_language=target_language,
            on_translation_event=on_translation_event,
            on_body=on_body,
            on_footnote=on_footnote,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def translate_anchored_contents(
        self,
        extraction: PDFCraftExtraction | PathLike | str,
        output_path: PathLike | str,
        transformer: AnchoredContentTransformer, *,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> PDFCraftExtraction:
        return run_sync(self._async_craft.translate_anchored_contents(
            extraction, output_path, transformer,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, label_language: str = "en",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        mode: RenderMode = RenderMode.SOURCE,
        translation_id: str | None = None,
        aborted: AbortedCheck = lambda: False,
        translator: XMLTranslator | None = None, window: int = 1,
        with_anchored: bool = True,
        on_body: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_footnote: Callable[[ParagraphContent[SourceRegion]], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> None:
        """Generate EPUB; label_language controls fixed labels only.

        Exact zh/en select built-in labels; all other values use English.
        This option neither selects nor limits the document/translation language.
        """
        return run_sync(self._async_craft.render_epub(
            extraction, output,
            book_meta=book_meta,
            label_language=label_language,
            table_render=table_render,
            latex_render=latex_render,
            inline_latex=inline_latex,
            mode=mode,
            translation_id=translation_id,
            aborted=aborted,
            translator=translator,
            window=window,
            with_anchored=with_anchored,
            on_body=on_body,
            on_footnote=on_footnote,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        translator: XMLTranslator,
        *, window: int = 1,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        on_body: Callable[[PDFParagraphContent], object] | None = None,
        on_footnote: Callable[[PDFParagraphContent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> None:
        return run_sync(self._async_craft.translate_pdf(
            source, extraction, output, translator,
            window=window,
            with_furniture=with_furniture,
            on_translation_event=on_translation_event,
            on_body=on_body,
            on_footnote=on_footnote,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def patch_pdf_with_extraction(
        self,
        source: PathLike | str,
        extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
        *, translation_id: str | None = None,
        translator: XMLTranslator | None = None, window: int = 1,
        on_body: Callable[[PDFParagraphContent], object] | None = None,
        on_footnote: Callable[[PDFParagraphContent], object] | None = None,
        with_furniture: bool = False,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> None:
        return run_sync(self._async_craft.patch_pdf_with_extraction(
            source, extraction, output,
            translation_id=translation_id,
            translator=translator,
            window=window,
            on_body=on_body,
            on_footnote=on_footnote,
            with_furniture=with_furniture,
            on_translation_event=on_translation_event,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def translate_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        target_language: str, submit: SubmitKind,
        user_prompt: str | None = None,
        max_group_tokens: int = 2600,
        llm: LLM | None = None,
        translation_llm: LLM | None = None, fill_llm: LLM | None = None,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        executor: AsyncExecutor | None = None,
        translation_executor: AsyncExecutor | None = None,
        fill_executor: AsyncExecutor | None = None,
        window: int = 1,
        on_body: Callable[[EpubFragmentContent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> None:
        return run_sync(self._async_craft.translate_epub(
            source, output,
            target_language=target_language,
            submit=submit,
            user_prompt=user_prompt,
            max_group_tokens=max_group_tokens,
            llm=llm,
            translation_llm=translation_llm,
            fill_llm=fill_llm,
            on_translation_event=on_translation_event,
            executor=executor,
            translation_executor=translation_executor,
            fill_executor=fill_executor,
            window=window,
            on_body=on_body,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def convert_pdf_to_markdown(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        assets_path: PathLike | str | None = None,
        translator: XMLTranslator | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        window: int = 1,
        with_anchored: bool = True,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> OCRTokensMetering:
        return run_sync(self._async_craft.convert_pdf_to_markdown(
            source, output,
            analysing_path=analysing_path,
            extraction_path=extraction_path,
            extraction=extraction,
            assets_path=assets_path,
            translator=translator,
            submit=submit,
            window=window,
            with_anchored=with_anchored,
            on_translation_event=on_translation_event,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))

    def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, label_language: str = "en",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        translator: XMLTranslator | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        window: int = 1,
        with_anchored: bool = True,
        on_translation_event: Callable[[TranslationEvent], object] | None = None,
        on_metadata: MetadataCallback | None = None,
        retry_policy: RetryPolicy | None = None, error_policy: ErrorPolicyInput = OMITTED,
        request_headers: Mapping[str, str] | None = None,
    ) -> OCRTokensMetering:
        """Generate EPUB; label_language controls fixed labels only.

        Exact zh/en select built-in labels; all other values use English.
        This option neither selects nor limits the document/translation language.
        """
        return run_sync(self._async_craft.convert_pdf_to_epub(
            source, output,
            analysing_path=analysing_path,
            extraction_path=extraction_path,
            extraction=extraction,
            book_meta=book_meta,
            label_language=label_language,
            table_render=table_render,
            latex_render=latex_render,
            inline_latex=inline_latex,
            translator=translator,
            submit=submit,
            window=window,
            with_anchored=with_anchored,
            on_translation_event=on_translation_event,
            on_metadata=on_metadata,
            retry_policy=retry_policy, error_policy=error_policy,
            request_headers=request_headers,
        ))
