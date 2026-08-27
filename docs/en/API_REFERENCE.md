# Public API reference

This reference covers the supported library surface imported from `pdf_craft`. Most applications only need `PDFCraft`, one OCR configuration, and—when translating—an `LLM`. The lower-level rendering, transformation, and PDF patching classes are available for applications that need explicit control.

```python
from pdf_craft import PDFCraft, PDFOptions
```

## `PDFCraft`

`PDFCraft` is the public façade for PDF extraction, document rendering, PDF patching, and EPUB translation. Creating it does not initialize OCR, so EPUB-only applications can use `PDFCraft()` with no PDF configuration.

```python
craft = PDFCraft(pdf=PDFOptions(ocr=your_ocr_config))
```

### PDF extraction and rendering

| Method | Signature and purpose |
| --- | --- |
| `extract_pdf` | `extract_pdf(source, package_path, options=None) -> DocumentPackage` extracts a PDF into a persistent package directory. `package_path` is required. |
| `extract_pdf_with_metering` | `extract_pdf_with_metering(source, package_path, options=None) -> tuple[DocumentPackage, OCRTokensMetering]` is the same extraction with OCR token accounting. |
| `render_markdown` | `render_markdown(package, output, assets_path=None, *, aborted=...)` writes Markdown and optional assets. |
| `render_epub` | `render_epub(package, output, *, book_meta=None, lan="zh", table_render=..., latex_render=..., inline_latex=True, aborted=...)` writes an EPUB. |
| `convert_pdf_to_markdown` | `convert_pdf_to_markdown(source, output, *, package_path=None, extraction=None, assets_path=None, translator=None, submit=SubmitKind.REPLACE, on_translation_event=None) -> OCRTokensMetering` is the one-shot PDF-to-Markdown workflow. |
| `convert_pdf_to_epub` | `convert_pdf_to_epub(source, output, *, package_path=None, extraction=None, book_meta=None, lan="zh", table_render=..., latex_render=..., inline_latex=True, translator=None, submit=SubmitKind.REPLACE, on_translation_event=None) -> OCRTokensMetering` is the one-shot PDF-to-EPUB workflow. |

The two `convert_pdf_to_*` methods clean up their temporary package workspace when `package_path` is omitted. Give `package_path` when you need to retain the package. `render_epub` accepts `epub_generator.BookMeta`, `TableRender`, and `LaTeXRender` values for output customization.

### Package translation and PDF patching

| Method | Signature and purpose |
| --- | --- |
| `translate_package` | `translate_package(package, output_path, translator, *, submit=SubmitKind.REPLACE, on_translation_event=None) -> DocumentPackage` translates a package into a new package. `translator` is a chapter transformer. |
| `translate_pdf` | `translate_pdf(source, package, output, transformer, *, on_translation_event=None)` translates then patches text onto the source PDF. `transformer` may be a chapter transformer or `Callable[[str], str]`. |
| `patch_pdf_with_package` | `patch_pdf_with_package(source, package, output)` patches a source PDF from a `DocumentPackage` or package path without OCR or LLM calls. |
| `translate_epub` | `translate_epub(source, output, *, target_language, submit, **options)` translates an existing EPUB. See [EPUB translation](EPUB_TRANSLATION.md) for its options. |

`translate_pdf` and `patch_pdf_with_package` require a package with page geometry that matches the source PDF. PDF patching rejects `SubmitKind.APPEND_BLOCK`.

## PDF configuration

### `PDFOptions`

`PDFOptions(ocr=None, pdf_handler=None, models_cache_path=None, local_only=False)` holds infrastructure that is reused across PDF extractions.

- `ocr`: one of the local or vendor OCR configuration objects below.
- `pdf_handler`: an optional `PDFHandler` implementation. Use it only to replace the PDF reading/rendering layer or manage that layer in your application.
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
| `generate_plot` | `False` | Generate plot-related package assets. |
| `toc_assumed` | `False` | Treat the document as already having usable TOC information. |
| `toc_llm` | `None` | LLM used when TOC analysis is needed. |
| `ignore_pdf_errors` | `False` | `True` or a predicate that decides whether a PDF error may be skipped. |
| `ignore_ocr_errors` | `False` | `True` or a predicate that decides whether an OCR error may be skipped. |
| `aborted` | a callback returning `False` | A callback checked during processing to request cancellation. |
| `on_ocr_event` | no-op callback | Receives per-page `OCREvent` updates. |

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
| `ChapterPackageTransformer` | Applies a chapter transformer across a package and writes a new package. |
| `PackageTransformer` | Public protocol for `transform(package, output_path) -> DocumentPackage`. |
| `XMLTranslator` | XML-aware translation engine for integrations that need direct structured translation. |
| `FillFailedEvent` | Information passed to EPUB XML-repair failure callbacks. |

Translation workflows also accept `on_translation_event`, a callback receiving
`TranslationEvent` values. `TranslationEventKind` reports `START`, `ITEM_START`,
`ITEM_COMPLETE`, `PROGRESS`, and `COMPLETE`; `TranslationItemKind` identifies TOC,
metadata, or chapter items. Character counts are source-text character counts and
are not token counts or percentages. Item events include the current item's
completed and total source characters, while scope events include the aggregate
counts. The same event callback is available for
EPUB translation, package translation, PDF translation, and PDF conversion.

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
from pdf_craft import PDFPatcher, PDFTranslationPipeline, PatchTextOptions

patcher = PDFPatcher(options=PatchTextOptions(
    font_name="STSong-Light",
    max_font_size=14,
    min_font_size=5,
    alignment="left",
    horizontal_padding=1,
    vertical_padding=1,
    overflow="error",
))
pipeline = PDFTranslationPipeline(patcher=patcher)
```

`PatchTextOptions` controls text fitting. `overflow="error"` (the default) stops if translated text cannot fit; `overflow="skip"` records skipped replacements in `PDFPatcher.skipped_replacements`. `PDFReplacement` and `PDFSkippedReplacement` describe individual patch outcomes.

`PDFTranslationPipeline` can perform lower-level translation or patching when the application owns the complete layout and output lifecycle. Prefer `PDFCraft.translate_pdf()` and `PDFCraft.patch_pdf_with_package()` when their fixed layout policy is sufficient.

## Other useful exports

- `DocumentPackage` represents a validated extracted or transformed document package; use `DocumentPackage.from_path(path)` to open one saved on disk.
- `PDFExtractor`, `MarkdownRenderer`, and `EpubRenderer` are the component-level extraction and rendering APIs behind the façade.
- `OCRTokensMetering` exposes `input_tokens` and `output_tokens`.
- `OCREvent` and `OCREventKind` support per-page progress and diagnostics.
- `predownload_models(...)` prepares local OCR models before an offline `local_only=True` run.
- `PDFError`, `OCRError`, and `InterruptedError` are exported error types for application-level handling.

For complete workflows, start with [PDF conversion and translation](PDF_TRANSLATION.md) or [EPUB translation](EPUB_TRANSLATION.md), rather than composing internal modules.
