"""Public facade that composes pdf-craft's independent components."""

from collections.abc import Callable, Container, Sequence
from dataclasses import dataclass
from inspect import signature
from os import PathLike
from pathlib import Path
from typing import Literal, cast

from epub_generator import BookMeta, LaTeXRender, TableRender

from .document import DocumentPackage
from .error import IgnoreOCRErrorsChecker, IgnorePDFErrorsChecker
from .extractor import PDFExtractor
from .llm import LLM
from .metering import AbortedCheck, OCRTokensMetering
from .ocr_config import OCRConfig
from .pdf import DeepSeekOCRSize, OCREvent, PDFHandler
from .pipeline.epub import translate_epub as run_epub_translation
from .pipeline.pdf import PDFTranslationPipeline
from .renderer import EpubRenderer, MarkdownRenderer
from .transformer import ChapterPackageTransformer, ChapterTransformer, PackageTransformer, SubmitKind


@dataclass(frozen=True)
class TranslationStep:
    """A user-requested content transformation inserted before rendering."""

    transformer: ChapterTransformer | PackageTransformer
    mode: SubmitKind = SubmitKind.REPLACE


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
        self, source: PathLike | str, package_path: PathLike | str,
        options: ExtractionOptions | None = None,
    ) -> DocumentPackage:
        package, _ = self.extract_pdf_with_metering(source, package_path, options)
        return package

    def extract_pdf_with_metering(
        self, source: PathLike | str, package_path: PathLike | str,
        options: ExtractionOptions | None = None,
    ) -> tuple[DocumentPackage, OCRTokensMetering]:
        options = options or ExtractionOptions()
        return PDFExtractor(self._pdf_engine()).extract_with_metering(
            Path(source), Path(package_path),
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
        self, package: DocumentPackage, output: PathLike | str,
        assets_path: PathLike | str | None = None,
        *, aborted: AbortedCheck = lambda: False,
    ) -> None:
        MarkdownRenderer().render(package, Path(output),
                                  Path(assets_path) if assets_path is not None else None,
                                  aborted=aborted)

    def transform_package(
        self, package: DocumentPackage, output_path: PathLike | str,
        steps: Sequence[TranslationStep | PackageTransformer],
    ) -> DocumentPackage:
        current = package
        for step in steps:
            transformer = self._as_package_transformer(step)
            current = transformer.transform(current, Path(output_path))
            output_path = Path(output_path).with_name(Path(output_path).name + ".next")
        return current

    def render_epub(
        self, package: DocumentPackage, output: PathLike | str, *,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] = "zh",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        aborted: AbortedCheck = lambda: False,
    ) -> None:
        EpubRenderer().render(package, Path(output), book_meta=book_meta, lan=lan,
                              table_render=table_render, latex_render=latex_render,
                              inline_latex=inline_latex, aborted=aborted)

    def translate_pdf(
        self, source: PathLike | str, package: DocumentPackage,
        output: PathLike | str, transformer: ChapterTransformer | Callable[[str], str],
        *, steps: Sequence[TranslationStep | PackageTransformer] = (),
    ) -> None:
        for step in steps:
            mode = _step_mode(step, self._as_package_transformer(step))
            if mode == SubmitKind.APPEND_BLOCK:
                raise ValueError("PDF output does not support APPEND_BLOCK")
        package = self._apply_steps(package, steps)
        PDFTranslationPipeline(
            pdf_handler=self._pdf.pdf_handler if self._pdf else None
        ).translate(Path(source), Path(output), package, transformer)

    def translate_epub(self, source: PathLike | str, output: PathLike | str, *,
                       target_language: str, submit: SubmitKind,
                       **options) -> None:
        run_epub_translation(source, output, target_language, submit, **options)

    def convert_pdf_to_markdown(
        self, source: PathLike | str, output: PathLike | str, *,
        package_path: PathLike | str, extraction: ExtractionOptions | None = None,
        assets_path: PathLike | str | None = None,
        steps: Sequence[TranslationStep | PackageTransformer] = (),
    ) -> OCRTokensMetering:
        package, metering = self.extract_pdf_with_metering(source, package_path, extraction)
        package = self._apply_steps(package, steps)
        self.render_markdown(package, output, assets_path, aborted=(extraction or ExtractionOptions()).aborted)
        return metering

    def convert_pdf_to_epub(
        self, source: PathLike | str, output: PathLike | str, *,
        package_path: PathLike | str, extraction: ExtractionOptions | None = None,
        book_meta: BookMeta | None = None, lan: Literal["zh", "en"] = "zh",
        table_render: TableRender = TableRender.HTML,
        latex_render: LaTeXRender = LaTeXRender.MATHML,
        inline_latex: bool = True,
        steps: Sequence[TranslationStep | PackageTransformer] = (),
    ) -> OCRTokensMetering:
        extraction = extraction or ExtractionOptions()
        package, metering = self.extract_pdf_with_metering(source, package_path, extraction)
        package = self._apply_steps(package, steps)
        if book_meta is None:
            book_meta = self._extract_book_meta(Path(source))
        self.render_epub(package, output, book_meta=book_meta, lan=lan,
                         table_render=table_render, latex_render=latex_render,
                         inline_latex=inline_latex, aborted=extraction.aborted)
        return metering

    def _apply_steps(
        self, package: DocumentPackage,
        steps: Sequence[TranslationStep | PackageTransformer],
    ) -> DocumentPackage:
        current = package
        for index, step in enumerate(steps):
            transformer = self._as_package_transformer(step)
            output = package.chapters_path.parent / f"transformed-{index}"
            current = transformer.transform(current, output)
        return current

    @staticmethod
    def _as_package_transformer(step: TranslationStep | PackageTransformer) -> PackageTransformer:
        if not isinstance(step, TranslationStep):
            return step
        transformer = step.transformer
        if isinstance(transformer, ChapterPackageTransformer):
            if step.mode != SubmitKind.REPLACE and transformer.mode != step.mode:
                return ChapterPackageTransformer(
                    transformer.chapter_transformer, mode=step.mode
                )
            return transformer
        if _accepts_package(transformer):
            return cast(PackageTransformer, transformer)
        return ChapterPackageTransformer(cast(ChapterTransformer, transformer), mode=step.mode)

    def _pdf_engine(self):
        if self._engine is not None:
            return self._engine
        if self._pdf is None:
            raise ValueError("PDF extraction requires PDFCraft(pdf=PDFOptions(...))")
        # Import lazily so EPUB-only callers never import the historical adapter.
        from .transform import Transform
        return Transform(models_cache_path=self._pdf.models_cache_path,
                         pdf_handler=self._pdf.pdf_handler,
                         local_only=self._pdf.local_only, ocr=self._pdf.ocr)

    def _extract_book_meta(self, source: Path) -> BookMeta | None:
        engine = self._pdf_engine()
        extract = getattr(engine, "_extract_book_meta", None)
        return extract(source) if extract is not None else None


def _accepts_package(transformer: ChapterTransformer | PackageTransformer) -> bool:
    """Recognise the public PackageTransformer method shape explicitly."""
    try:
        parameters = list(signature(transformer.transform).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [
        parameter for parameter in parameters
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) == 2 and [parameter.name for parameter in positional] == [
        "package", "output_path"
    ]


def _step_mode(step: TranslationStep | PackageTransformer, transformer: PackageTransformer) -> SubmitKind | None:
    if isinstance(step, TranslationStep):
        return step.mode if step.mode != SubmitKind.REPLACE else getattr(transformer, "mode", step.mode)
    return getattr(transformer, "mode", None)
