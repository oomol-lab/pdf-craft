"""Public facade that composes pdf-craft's independent components."""

# Internal workspace methods are intentionally shared only inside pdf-craft.
# pylint: disable=protected-access

from collections.abc import Callable, Container
from contextlib import contextmanager
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator, Literal

from epub_generator import BookMeta, LaTeXRender, TableRender

from .document import PDFCraftExtraction
from .extractor.chapter.chapter import Chapter, ParagraphLayout
from .extractor.chapter.reader import create_chapters_reader
from .error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .extractor import PDFExtractor
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig
from .pdf import DeepSeekOCRSize, OCREvent, PDFHandler
from .pipeline.epub import translate_epub as run_epub_translation
from .pipeline.pdf import PDFTranslationPipeline
from .pipeline.pdf.pipeline import _to_patch_text
from .renderer import EpubRenderer, MarkdownRenderer
from .transformer import ChapterExtractionTransformer, ChapterTransformer, SubmitKind, TranslationEvent


@dataclass(frozen=True)
class PDFOptions:
    """Long-lived infrastructure needed only when extracting a PDF."""

    ocr: OCRConfig | None = None
    pdf_handler: PDFHandler | None = None
    models_cache_path: PathLike | str | None = None
    local_only: bool = False


@dataclass(frozen=True)
class ExtractionOptions:
    """Controls for one PDF extraction run."""

    page_indexes: Container[int] | None = None
    ocr_size: DeepSeekOCRSize = "gundam"
    dpi: int | None = None
    max_page_image_file_size: int | None = None
    max_ocr_tokens: int | None = None
    max_ocr_output_tokens: int | None = None
    includes_cover: bool = False
    includes_footnotes: bool = False
    generate_plot: bool = False
    toc_assumed: bool = False
    toc_llm: LLM | None = None
    ignore_pdf_errors: IgnorePDFErrorsChecker = False
    ignore_ocr_errors: IgnoreOCRErrorsChecker = False
    aborted: AbortedCheck = lambda: False
    on_ocr_event: Callable[[OCREvent], None] = lambda _: None


class PDFCraft:
    """Compose extraction, rendering, and format-specific translation workflows.

    Constructing this facade does not initialise OCR.  EPUB-only callers can
    therefore use ``PDFCraft()`` without PDF infrastructure or credentials.
    """

    def __init__(self, pdf: PDFOptions | None = None, *, _engine=None) -> None:
        self._pdf = pdf
        self._engine = _engine

    @classmethod
    def from_engine(cls, engine) -> "PDFCraft":
        return cls(_engine=engine)

    def extract_pdf(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> PDFCraftExtraction:
        extraction, _ = self.extract_pdf_with_metering(
            source, extraction_path, options, analysing_path=analysing_path
        )
        return extraction

    def extract_pdf_with_metering(
        self, source: PathLike | str, extraction_path: PathLike | str,
        options: ExtractionOptions | None = None,
        *, analysing_path: PathLike | str | None = None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        options = options or ExtractionOptions()
        return PDFExtractor(self._pdf_engine()).extract_with_metering(
            Path(source), Path(extraction_path),
            analysing_path=Path(analysing_path) if analysing_path is not None else None,
            page_indexes=options.page_indexes,
            ocr_size=options.ocr_size, dpi=options.dpi,
            max_page_image_file_size=options.max_page_image_file_size,
            max_tokens=options.max_ocr_tokens,
            max_output_tokens=options.max_ocr_output_tokens,
            includes_cover=options.includes_cover,
            includes_footnotes=options.includes_footnotes,
            generate_plot=options.generate_plot,
            toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
            ignore_pdf_errors=options.ignore_pdf_errors,
            ignore_ocr_errors=options.ignore_ocr_errors,
            aborted=options.aborted, on_ocr_event=options.on_ocr_event,
        )

    def render_markdown(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, aborted: AbortedCheck = lambda: False,
    ) -> None:
        MarkdownRenderer().render(_ensure_extraction(extraction), Path(output),
                                  Path(assets_path) if assets_path is not None else None,
                                  aborted=aborted)

    def translate_extraction(
        self, extraction: PDFCraftExtraction | PathLike | str, output_path: PathLike | str,
        translator: ChapterTransformer,
        *, submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> PDFCraftExtraction:
        """Translate one PDFCraftExtraction into another ``.pcex`` artifact.

        The public operation is intentionally singular and translation-focused;
        arbitrary transformation chains remain an internal composition detail.
        """
        extraction_transformer = ChapterExtractionTransformer(translator, mode=submit)
        return extraction_transformer.transform(
            _ensure_extraction(extraction), Path(output_path), on_translation_event=on_translation_event,
            emit_translation_events=True,
        )

    def render_epub(
        self, extraction: PDFCraftExtraction | PathLike | str, output: PathLike | str, *,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        EpubRenderer().render(_ensure_extraction(extraction), Path(output), book_meta=book_meta, lan=lan,
                              table_render=table_render, latex_render=latex_render,
                              inline_latex=inline_latex, aborted=aborted)

    def translate_pdf(
        self, source: PathLike | str, extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str, transformer: ChapterTransformer | Callable[[str], str],
        *, on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> None:
        with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
            translated = self._translate_for_pdf(
                _ensure_extraction(extraction), Path(directory), transformer,
                on_translation_event=on_translation_event,
            )
            self.patch_pdf_with_extraction(source, translated, output)

    def patch_pdf_with_extraction(
        self,
        source: PathLike | str,
        extraction: PDFCraftExtraction | PathLike | str,
        output: PathLike | str,
    ) -> None:
        """Patch an existing PDF with text and geometry from a PDFCraftExtraction."""
        extraction = _ensure_extraction(extraction)
        extraction.validate()
        _validate_extraction_for_pdf(Path(source), extraction)
        PDFTranslationPipeline(
            pdf_handler=self._pdf.pdf_handler if self._pdf else None
        ).patch(Path(source), Path(output), extraction)

    def translate_epub(self, source: PathLike | str, output: PathLike | str, *,
                       target_language: str, submit: SubmitKind,
                       **options) -> None:
        run_epub_translation(source, output, target_language, submit, **options)

    def convert_pdf_to_markdown(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        assets_path: PathLike | str | None = None,
        translator: ChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> OCRTokensMetering:
        with _analysis_workspace(analysing_path) as workspace:
            document, metering = self._extract_to_workspace(source, workspace, extraction)
            if extraction_path is not None:
                document.export(Path(extraction_path))
            if translator is not None:
                with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
                    document = self._translate_to_workspace(
                        document, Path(directory) / "extraction", translator,
                        submit=submit, on_translation_event=on_translation_event,
                    )
                    self.render_markdown(document, output, assets_path,
                                         aborted=(extraction or ExtractionOptions()).aborted)
            else:
                self.render_markdown(document, output, assets_path,
                                     aborted=(extraction or ExtractionOptions()).aborted)
        return metering

    def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        analysing_path: PathLike | str | None = None,
        extraction_path: PathLike | str | None = None,
        extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] | None = None,
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        translator: ChapterTransformer | None = None,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> OCRTokensMetering:
        extraction = extraction or ExtractionOptions()
        with _analysis_workspace(analysing_path) as workspace:
            document, metering = self._extract_to_workspace(source, workspace, extraction)
            if extraction_path is not None:
                document.export(Path(extraction_path))
            if translator is not None:
                with TemporaryDirectory(prefix="pdf-craft-translated-extraction-") as directory:
                    document = self._translate_to_workspace(
                        document, Path(directory) / "extraction", translator,
                        submit=submit, on_translation_event=on_translation_event,
                    )
                    self.render_epub(document, output, book_meta=book_meta, lan=lan,
                                     table_render=table_render, latex_render=latex_render,
                                     inline_latex=inline_latex, aborted=extraction.aborted)
            else:
                self.render_epub(document, output, book_meta=book_meta, lan=lan,
                                 table_render=table_render, latex_render=latex_render,
                                 inline_latex=inline_latex, aborted=extraction.aborted)
        return metering

    def _translate_for_pdf(
        self,
        extraction: PDFCraftExtraction,
        output_root: Path,
        transformer: ChapterTransformer | Callable[[str], str],
        *, on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> PDFCraftExtraction:
        if callable(transformer):
            transformer = _TextChapterTransformer(transformer)
        return self._translate_to_workspace(
            extraction, output_root / "translated", transformer,
            on_translation_event=on_translation_event,
        )

    def _translate_to_workspace(
        self,
        extraction: PDFCraftExtraction,
        output_path: Path,
        transformer: ChapterTransformer,
        *,
        submit: SubmitKind = SubmitKind.REPLACE,
        on_translation_event: Callable[[TranslationEvent], None] | None = None,
    ) -> PDFCraftExtraction:
        return ChapterExtractionTransformer(transformer, mode=submit)._transform_to_workspace(
            extraction, output_path, on_translation_event=on_translation_event,
            emit_translation_events=True,
        )

    def _extract_to_workspace(
        self,
        source: PathLike | str,
        analysing_path: Path,
        options: ExtractionOptions | None,
    ) -> tuple[PDFCraftExtraction, OCRTokensMetering]:
        options = options or ExtractionOptions()
        return PDFExtractor(self._pdf_engine())._extract_to_workspace(
            Path(source), analysing_path,
            page_indexes=options.page_indexes,
            ocr_size=options.ocr_size, dpi=options.dpi,
            max_page_image_file_size=options.max_page_image_file_size,
            max_tokens=options.max_ocr_tokens,
            max_output_tokens=options.max_ocr_output_tokens,
            includes_cover=options.includes_cover,
            includes_footnotes=options.includes_footnotes,
            generate_plot=options.generate_plot,
            toc_assumed=options.toc_assumed, toc_llm=options.toc_llm,
            ignore_pdf_errors=options.ignore_pdf_errors,
            ignore_ocr_errors=options.ignore_ocr_errors,
            aborted=options.aborted, on_ocr_event=options.on_ocr_event,
        )

    def _pdf_engine(self):
        if self._engine is not None:
            return self._engine
        if self._pdf is None:
            raise ValueError("PDF extraction requires PDFCraft(pdf=PDFOptions(...))")
        # Import lazily so EPUB-only callers never import the historical adapter.
        from .transform import PDFExtractionEngine
        return PDFExtractionEngine(models_cache_path=self._pdf.models_cache_path,
                                   pdf_handler=self._pdf.pdf_handler,
                                   local_only=self._pdf.local_only, ocr=self._pdf.ocr)


@contextmanager
def _analysis_workspace(analysing_path: PathLike | str | None) -> Iterator[Path]:
    """Provide a persistent analysis path or a cleaned-up temporary workspace."""
    if analysing_path is not None:
        yield Path(analysing_path)
        return
    with TemporaryDirectory(prefix="pdf-craft-analysis-") as directory:
        yield Path(directory)


class _TextChapterTransformer:
    """Adapt the block-text callback to the extraction transformer shape."""

    def __init__(self, callback: Callable[[str], str]) -> None:
        self._callback = callback

    def transform(self, chapter: Chapter) -> Chapter:
        for layout in chapter.layouts:
            if not isinstance(layout, ParagraphLayout):
                continue
            for block in layout.blocks:
                text = _to_patch_text(block.content)
                translated = self._callback(text)
                if translated != text:
                    block.content = [translated]
        return chapter


def _validate_extraction_for_pdf(source: Path, extraction: PDFCraftExtraction) -> None:
    """Fail before patching when extraction geometry cannot match the PDF."""
    try:
        import pypdf
    except ImportError as error:
        raise RuntimeError("PDF patching requires the optional 'pypdf' dependency") from error
    page_sizes = extraction.page_pixel_sizes()
    if not page_sizes:
        raise ValueError("PDFCraftExtraction is missing page geometry required for PDF patching")
    page_count = len(pypdf.PdfReader(str(source)).pages)
    with extraction._materialize() as paths:
        chapter_pages = {
            block.page_index
            for chapter in create_chapters_reader(paths.chapters)()
            for layout in chapter.layouts
            if isinstance(layout, ParagraphLayout)
            for block in layout.blocks
        }
    invalid = sorted(page for page in set(page_sizes) | chapter_pages if page > page_count)
    if invalid:
        raise ValueError(
            "PDFCraftExtraction page geometry exceeds source PDF page count: "
            f"pages {invalid} of {page_count}"
        )
    missing = sorted(page for page in chapter_pages if page not in page_sizes)
    if missing:
        raise ValueError(
            "PDFCraftExtraction is missing page geometry for chapter pages: "
            f"{missing}"
        )


def _ensure_extraction(value: PDFCraftExtraction | PathLike | str) -> PDFCraftExtraction:
    if isinstance(value, PDFCraftExtraction):
        return value
    return PDFCraftExtraction.open(Path(value))
