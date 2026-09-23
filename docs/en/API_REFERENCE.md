# Public API reference

This reference covers the supported library surface imported from `pdf_craft`. Most applications only need `PDFCraft`, one OCR configuration, and—when translating—an `LLM`. The lower-level rendering, transformation, and PDF patching classes are available for applications that need explicit control.

```python
from pdf_craft import AsyncPDFCraft, PDFCraft, PDFOptions
```

## Async and synchronous façades

`AsyncPDFCraft` is the primary integration surface for servers, notebooks, and
other asyncio applications. It exposes async counterparts of every `PDFCraft`
workflow: extraction, rendering, PCEX translation, PDF patching, EPUB
translation, and the two one-shot conversions. OCR, PDF/ZIP/image processing,
Qt layout, and other synchronous third-party libraries run in bounded execution
domains so they do not block the caller's event loop. Translation and LLM
network concurrency are native asyncio operations.

```python
craft = AsyncPDFCraft(pdf=PDFOptions(ocr=your_ocr_config))
extraction = await craft.extract_pdf("input.pdf", "book.pcex")
await craft.render_markdown(extraction, "book.md")
```

OCR and translation event callbacks may be synchronous functions or async
functions. They execute on the event-loop thread and async callbacks are
awaited. Cancelling an async task cancels native network/subprocess work and
signals cooperative blocking stages through their abort callback. The await
does not finish cancelling until that worker has unwound, so temporary
workspaces remain valid through its final writes and cleanup.
External tools such as `pdftotext`, Ghostscript, and LaTeX run in tracked
process groups. Cancellation or an internal timeout terminates the command and
its descendants; Qt-worker cancellation also reaps every command group the
worker registered before the worker itself exits.

Extension authors can implement `AsyncChapterTransformer` with
`async def transform(chapter)`; the async façade awaits it directly on the
caller loop. `PDFOptions.pdf_handler` also accepts `AsyncPDFHandler`, whose
`open()` returns an `AsyncPDFDocument` with awaitable `pages_count()`,
`metadata()`, `page_size()`, `render_page()`, and `close()` methods. The SDK
adapts that document at the synchronous OCR boundary without running its
coroutines in a worker thread. For PDF patching, required source-page rasters
are awaited on the caller loop and materialized temporarily; only the resulting
paths and metadata cross into the isolated Qt process.

The async XML/EPUB translation APIs likewise accept a synchronous or async
`on_fill_failed` callback. Each repair notification runs on the caller's event
loop, and an async callback is awaited before the next repair step proceeds.

`PDFCraft` retains the same synchronous API for scripts as a compatibility
adapter over the async implementation. It must not be called
from a thread that already has a running event loop; doing so raises a clear
`RuntimeError` instead of nesting an event loop. Use `AsyncPDFCraft` there.

`PDFCraftExtraction` also provides async persistence and metadata methods:
`open_async`, `validate_async`, `export_async`, `page_pixel_sizes_async`,
`render_dpi_async`, and `document_metadata_async`. Component users can call
`PDFExtractor.extract_async`, `MarkdownRenderer.render_async`, and
`EpubRenderer.render_async`. Model preloading is available as
`predownload_models_async`.

The standalone existing-EPUB entry is likewise available as
`translate_epub_async`; await it instead of calling `translate_epub` in an
async application.

## `PDFCraft`

`PDFCraft` is the public façade for PDF extraction, document rendering, PDF patching, and EPUB translation. Creating it does not initialize OCR, so EPUB-only applications can use `PDFCraft()` with no PDF configuration.

```python
craft = PDFCraft(pdf=PDFOptions(ocr=your_ocr_config))
```

### PDF extraction and rendering

| Method | Signature and purpose |
| --- | --- |
| `extract_pdf` | `extract_pdf(source, extraction_path, options=None, *, analysing_path=None) -> PDFCraftExtraction` extracts a PDF into a persistent `.pcex` archive. |
| `extract_pdf_with_metering` | `extract_pdf_with_metering(source, extraction_path, options=None, *, analysing_path=None) -> tuple[PDFCraftExtraction, OCRTokensMetering]` is the same extraction with OCR token accounting. |
| `render_markdown` | `render_markdown(extraction, output, assets_path=None, *, aborted=...)` writes Markdown and optional assets from a `PDFCraftExtraction` or `.pcex` path. |
| `render_epub` | `render_epub(extraction, output, *, book_meta=None, lan=None, table_render=..., latex_render=..., inline_latex=True, aborted=...)` writes an EPUB. Metadata and language default to the extraction manifest. |
| `convert_pdf_to_markdown` | `convert_pdf_to_markdown(source, output, *, analysing_path=None, extraction_path=None, extraction=None, assets_path=None, translator=None, submit=SubmitKind.REPLACE, on_translation_event=None) -> OCRTokensMetering` is the one-shot PDF-to-Markdown workflow. |
| `convert_pdf_to_epub` | `convert_pdf_to_epub(source, output, *, analysing_path=None, extraction_path=None, extraction=None, book_meta=None, lan=None, table_render=..., latex_render=..., inline_latex=True, translator=None, submit=SubmitKind.REPLACE, on_translation_event=None) -> OCRTokensMetering` is the one-shot PDF-to-EPUB workflow. |

The two `convert_pdf_to_*` methods use a directory-backed extraction inside their analysis workspace, avoiding a ZIP round trip. Give `analysing_path` to retain diagnostics and `extraction_path` to additionally export a `.pcex`. `render_epub` accepts `epub_generator.BookMeta`, `TableRender`, and `LaTeXRender` values for output customization.

### Extraction translation and PDF patching

| Method | Signature and purpose |
| --- | --- |
| `translate_extraction` | `translate_extraction(extraction, output_path, translator, *, submit=SubmitKind.REPLACE, with_furniture=False, on_translation_event=None) -> PDFCraftExtraction` translates a `.pcex` into a new `.pcex`; `with_furniture=True` also translates its existing page furniture. |
| `translate_anchored_contents` | `translate_anchored_contents(extraction, output_path, transformer) -> PDFCraftExtraction` applies the separate image/table text translation stage. It is not composed automatically by EPUB, Markdown, or PDF workflows. |
| `translate_pdf` | `translate_pdf(source, extraction, output, transformer, *, with_furniture=False, on_translation_event=None, ignore_errors=False)` translates a PCEX through a structured chapter transformer, then patches it onto the source PDF. |
| `patch_pdf_with_extraction` | `patch_pdf_with_extraction(source, extraction, output, *, ignore_errors=False)` patches a source PDF from a `PDFCraftExtraction` or `.pcex` path without OCR or LLM calls. |
| `translate_epub` | `translate_epub(source, output, *, target_language, submit, **options)` translates an existing EPUB. See [EPUB translation](EPUB_TRANSLATION.md) for its options. |

`with_furniture` belongs to translation, not extraction. When enabled, it translates page furniture already present in the PCEX. `translate_extraction()` persists its coverage for a later PDF patch; `translate_pdf()` then includes it in the PDF patch it produces. It does not re-run OCR and requires the structured `ChapterXMLTransformer` adapter. `translate_pdf` and `patch_pdf_with_extraction` require extraction page geometry that matches the source PDF. PDF patching rejects `SubmitKind.APPEND_BLOCK`.

Set `ignore_errors=True` to preserve a page's non-interactive visual base when that page's fill transaction fails, then continue with later pages. The default remains fail-fast. `ignore_errors` may instead be a `Callable[[Exception], bool]` that chooses whether each page-scoped exception may fall back. If every page scheduled for fill falls back, `NoUsableFillPagesError` is raised and no output is written. This recovery scope intentionally covers ordinary page-level exceptions, including unexpected fill bugs; it does not recover a source PDF that cannot be opened, enumerated, or compiled into a visual base. Enable it only when an untranslated visual-base page beside successfully translated pages is an acceptable result.

## `PDFCraftExtraction` and `.pcex`

`PDFCraftExtraction` is pdf-craft's structured, source-mapped intermediate format.
It is more direct than Markdown or EPUB and retains each recognized block's PDF page
and pixel-space bounding box. Public persistence and exchange always use a `.pcex`
file, which is a validated ZIP archive with this layout:

```text
manifest.json
pages.xml
chapters/chapter_head.xml
chapters/chapter_*.xml
assets/
toc.xml        # optional
cover.png      # optional
furnitures.xml  # optional
translation.xml # optional
```

`manifest.json` contains the format version, producer, creation time, and document
metadata. `pages.xml` defines the one-based OCR-pixel coordinate space, extraction
DPI, and actual pixel width and height of every extracted page. Chapter XML retains
page and bounding-box source mappings. Analysis caches such as OCR responses and
plots are deliberately excluded.

Loading validates the archive version, member paths, required files, XML roots,
page references, bounding boxes, and referenced assets. Unsupported, malformed,
corrupt, or path-unsafe archives are rejected. Back-end operations only consume the
extraction; they do not fall back to an analysis/OCR directory.

## PDF configuration

### `PDFOptions`

`PDFOptions(ocr=None, pdf_handler=None, models_cache_path=None, local_only=False)` holds infrastructure that is reused across PDF extractions.

- `ocr`: one of the local or vendor OCR configuration objects below.
- `pdf_handler`: an optional `PDFHandler` or `AsyncPDFHandler` implementation. Use it only to replace the PDF reading/rendering layer or manage that layer in your application.
- `models_cache_path` and `local_only`: convenience settings for the default local DeepSeek OCR configuration when `ocr` is not supplied. They must not be combined with an explicit `ocr` configuration.

### `ExtractionOptions`

`ExtractionOptions` controls one extraction operation:

| Field | Default | Meaning |
| --- | --- | --- |
| `page_indexes` | `None` | A container of one-based page numbers to process. |
| `ocr_size` | `"gundam"` | OCR preset; valid values depend on the backend. |
| `dpi` | `None` | Page-rendering DPI; the underlying default is used when omitted. |
| `max_page_image_file_size` | `None` | Maximum rendered-image size per page. |
| `max_ocr_tokens` | `None` | Cumulative OCR input-plus-output token budget. |
| `max_ocr_output_tokens` | `None` | Cumulative OCR output-token budget. |
| `includes_cover` | `False` | Retain a recognized cover image. |
| `includes_footnotes` | `False` | Request and retain footnotes. |
| `includes_furniture` | `True` | Include native page furniture in the extracted PCEX as `furnitures.xml`; it does not translate it. |
| `extract_book_metadata` | `False` | Extract bibliographic metadata from the first OCR pages. It is persisted in PCEX and, when that PCEX is patched to PDF, corrects the output PDF's document metadata. |
| `metadata_llm` | `None` | Required LLM for `extract_book_metadata=True`; it is independent of `toc_llm`. |
| `generate_plot` | `False` | Generate plot diagnostics in the analysis workspace (not in `.pcex`). |
| `toc_assumed` | `False` | Treat the document as already having usable TOC information. |
| `toc_llm` | `None` | LLM used when TOC analysis is needed. |
| `ignore_pdf_errors` | `False` | `True` or a predicate that decides whether a PDF error may be skipped. |
| `ignore_ocr_errors` | `False` | `True` or a predicate that decides whether an OCR error may be skipped. |
| `aborted` | a callback returning `False` | A callback checked during processing to request cancellation. |
| `on_ocr_event` | no-op callback | Receives per-page `OCREvent` updates. |

Book-metadata extraction is deliberately opt-in. When enabled, PDF Craft lets a dedicated LLM
read the first three raw OCR pages and request further front pages in batches, up to twelve pages.
Only values with OCR evidence are accepted. PDF file metadata is used only to fill fields that OCR
did not provide; it never replaces an OCR value. The resulting PCEX metadata is consumed by EPUB
rendering and by PDF patching, which writes it to the output PDF's document-information dictionary.
If the metadata dialogue cannot be validated after
its bounded repair loop, extraction continues with that same PDF-file fallback rather than failing
the document conversion.

### OCR configurations

All OCR configuration objects are immutable dataclasses and are passed to `PDFOptions(ocr=...)`.

| Class | Required fields | Optional fields |
| --- | --- | --- |
| `DeepSeekOCRLocalConfig` | none | `models_cache_path`, `local_only`, `enable_devices_numbers` |
| `DeepSeekOCR2LocalConfig` | none | `models_cache_path`, `local_only`, `enable_devices_numbers` |
| `UnlimitedOCRLocalConfig` | none | `models_cache_path`, `local_only`, `enable_devices_numbers` |
| `DeepSeekOCRVendorConfig` | `base_url`, `api_key`, `model` | `temperature`, `top_p`, `max_tokens=8000`, `timeout_seconds=180` |
| `DeepSeekOCR2VendorConfig` | `base_url`, `api_key`, `model` | `temperature`, `top_p`, `max_tokens=8000`, `timeout_seconds=180` |
| `UnlimitedOCRVendorConfig` | `ak`, `sk` | `base_url="https://aip.baidubce.com"`, `poll_interval_seconds=2.0`, `timeout_seconds=180` |

See [OCR backends](OCR_BACKENDS.md) for model origin, runtime requirements, and selection guidance.

## Transformations and submission modes

`SubmitKind` determines how transformed text is incorporated:

- `SubmitKind.REPLACE`: replace source text.
- `SubmitKind.APPEND_TEXT`: append translated text to the same text flow.
- `SubmitKind.APPEND_BLOCK`: append translated content as a separate block. It is not supported for PDF patching.

The following classes are exposed for applications that need custom structured transformations:

| Type | Role |
| --- | --- |
| `ChapterXMLTransformer` | Adapts XML-oriented work to chapter transformation. |
| `AnchoredContentXMLTransformer` | Adapts XML-oriented work to independent image/table text translation, validating each immutable asset slot before applying fields. |
| `AnchoredContentTransformer` | Protocol for contextual batches of extracted image/table text; every non-preserved result carries its source `identity`. |
| `ChapterExtractionTransformer` | Applies a chapter transformer across an extraction and writes a new `.pcex`. |
| `ExtractionTransformer` | Public protocol for `transform(extraction, output_path) -> PDFCraftExtraction`. |
| `XMLTranslator` | XML-aware translation engine for integrations that need direct structured translation. |
| `FillFailedEvent` | Information passed to EPUB XML-repair failure callbacks. |

Translation workflows also accept `on_translation_event`, a callback receiving
`TranslationEvent` values. `TranslationEventKind` reports `START`, `ITEM_START`,
`ITEM_COMPLETE`, `PROGRESS`, and `COMPLETE`; `TranslationItemKind` identifies TOC,
metadata, or chapter items. Character counts are source-text character counts and
are not token counts or percentages. Item events include the current item's
completed and total source characters, while scope events include the aggregate
counts. The same event callback is available for
EPUB translation, extraction translation, PDF translation, and PDF conversion.

## `LLM`

`LLM` configures an OpenAI-compatible Chat Completions client used for text translation and TOC analysis:

```python
LLM(
    key,
    url,
    model,
    token_encoding,
    timeout=None,
    top_p=None,
    temperature=None,
    retry_times=5,
    retry_interval_seconds=6.0,
    cache_path=None,
    log_dir_path=None,
)
```

`key`, `url`, `model`, and `token_encoding` are required. `temperature` and `top_p` may be numbers or ranges used while retrying. Successful requests can be reused through `cache_path`; `log_dir_path` records request and cache events. OCR credentials do not configure this object.

## PDF patching primitives

For patch layout beyond the convenience methods, use these public types:

```python
from pdf_craft import EraseOptions, PDFPatcher, PDFTranslationPipeline, PatchTextOptions, PatchTextStyle

patcher = PDFPatcher(options=PatchTextOptions(
    # Omit font_name to select one installed local family for this patch run.
    # An explicit family is preferred; Qt falls back when it is absent.
    max_font_size=14,
    min_font_size=5,
    alignment="left",
    horizontal_padding=1,
    vertical_padding=1,
    styles={"heading": PatchTextStyle(max_font_size=18, min_font_size=8)},
), erase_options=EraseOptions(padding=2))
pipeline = PDFTranslationPipeline(patcher=patcher)
```

`PatchTextOptions` controls the default Qt text style and optional `PatchTextStyle` overrides by semantic layout key (`"body"`, `"heading"`, or `"heading:2"`). Omitting (or passing an empty) `font_name` resolves one installed local family and reuses it across all unspecified styles for that patch run. `PDFPatcher.font_resolutions` reports those automatic choices and any configured-family Qt fallback without conflating them with layout errors. The first pass gives a paragraph one fitted font size and flows it through its ordered `PDFReplacement.regions` without splitting a line across boxes. A second per-page pass locally normalizes each bbox toward its semantic-level character-weighted average without changing the frozen line count or text allocation, so final bbox sizes can differ slightly. Qt handles native shaping, wrapping, fallback fonts and glyph positions; missing configured fonts do not stop a patch. If valid source geometry still cannot contain a complete paragraph at its minimum size, the patcher writes the full text from the first source bbox at that minimum, allowing it to extend past normal bbox and obstacle boundaries rather than aborting the PDF. Headlines first retain their natural rightward overflow rule, then use that same final fallback if necessary.

`render_inline_formulas=True` is the default. Chapter XML preserves inline formulas so they remain in the translation context without replacing their source formula. When Matplotlib and local TeX are available, the patcher writes them as vector PDF content; otherwise (or if an individual formula fails) it falls back to readable plain text without failing the document. Set the option to `False` to select that plain-text behavior explicitly. Vector fragments carry a plain-text PDF `/ActualText` semantic replacement, but pdf-craft does not construct a complete tagged PDF and does not guarantee reader-specific copy order relative to surrounding text. Qt keeps its native language behavior for ordinary text; only a vector formula and its immediately following visible spacing form an unbreakable atom.

The patcher merges two independent overlays over a Ghostscript-compiled, fontless visual base of each source page's non-Annotation content: a local-background RGB rectangle erasure layer and a Qt-generated PDF text layer. `EraseOptions(padding=2)` expands each source box in its OCR-pixel coordinates before sampling and painting it. The supplied `PDFHandler` (or the default Poppler handler) renders original pages only for color sampling; it is never used as an output page image. The visual base preserves the appearance without leaving ordinary source or hidden OCR text selectable, searchable, or extractable. The source `/Annots` array is reattached above translation, so links, highlights, notes, forms, and other PDF Annotations remain independently interactive. The erasure is visual only and deliberately does not restore texture, rules, formulae, or artwork. Qt/PySide6, Ghostscript, a PDF renderer such as Poppler, and the required fonts must be available on the machine producing the PDF.

`PDFTranslationPipeline` is the lower-level patching component for an already translated extraction. Prefer `PDFCraft.translate_pdf()` and `PDFCraft.patch_pdf_with_extraction()` when their fixed layout policy is sufficient.

## Other useful exports

- `PDFCraftExtraction` represents a validated extracted or transformed document. Use `PDFCraftExtraction.open("book.pcex")` to load the portable ZIP-based artifact. Ordinary directories are intentionally not public inputs.
- `PDFExtractor`, `MarkdownRenderer`, and `EpubRenderer` are the component-level extraction and rendering APIs behind the façade.
- `OCRTokensMetering` exposes `input_tokens` and `output_tokens`.
- `OCREvent` and `OCREventKind` support per-page progress and diagnostics.
- `predownload_models(...)` prepares local OCR models before an offline `local_only=True` run.
- `PDFError`, `OCRError`, `NoUsableFillPagesError`, and `InterruptedError` are exported error types for application-level handling.

For complete workflows, start with [PDF conversion and translation](PDF_TRANSLATION.md) or [EPUB translation](EPUB_TRANSLATION.md), rather than composing internal modules.
