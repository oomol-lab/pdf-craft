# PDF conversion and translation

This guide covers workflows that start with a PDF: converting it to Markdown or EPUB, translating while converting, and placing translated text back onto the pages of the original PDF. For translating an EPUB you already have, see [EPUB translation](EPUB_TRANSLATION.md).

## Choose the workflow that fits the result you need

| Goal | Start here | What it produces |
| --- | --- | --- |
| Convert a PDF once | `convert_pdf_to_markdown()` or `convert_pdf_to_epub()` | Markdown or EPUB |
| Translate as the PDF is converted | Pass one `translator` to either conversion method | Translated Markdown or EPUB |
| Reuse or move an extraction | `extract_pdf()`, then render or translate it | A portable `.pcex` file |
| Produce a translated PDF | `translate_pdf()` or `patch_pdf_with_extraction()` | A new PDF with translated text over the source pages |

For a one-off conversion, use the two `convert_pdf_to_*` methods. They extract the PDF, optionally translate it once, and render the final file. Use the extraction APIs when you need a persistent intermediate artifact or need to control each stage separately.

All PDF extraction requires a configured `PDFCraft` instance. The OCR configuration is independent of the text LLM used for translation.

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(
    ocr=DeepSeekOCRVendorConfig(
        base_url="https://example.com/v1",
        api_key="your-api-key",
        model="deepseek-ocr",
    ),
))
```

See [OCR backends](OCR_BACKENDS.md) for choosing and configuring an OCR backend.

## Convert a PDF to Markdown

```python
metering = craft.convert_pdf_to_markdown("book.pdf", "book.md")
print(metering.input_tokens, metering.output_tokens)
```

The return value records OCR input and output tokens for the run. By default,
pdf-craft creates a temporary analysis workspace and removes it on success or
failure. Retain diagnostics with `analysing_path`; independently export the stable
intermediate format with `extraction_path`:

```python
craft.convert_pdf_to_markdown(
    "book.pdf",
    "book.md",
    analysing_path="work/analysis",
    extraction_path="work/book.pcex",
    assets_path="output/assets",
)
```

`assets_path` controls where Markdown images and other extracted assets are written.
A caller-supplied analysis directory and `.pcex` file are persistent and are the
caller's responsibility to manage.

## Convert a PDF to EPUB

The EPUB conversion path uses the same extraction pipeline and adds EPUB metadata and rendering choices.

```python
from epub_generator import BookMeta

craft.convert_pdf_to_epub(
    "book.pdf",
    "book.epub",
    book_meta=BookMeta(title="A Book", authors=["Author"]),
    label_language="en",
)
```

If `book_meta` is omitted, EPUB metadata comes only from the extraction manifest. By default that
manifest has no automatically inferred book metadata: pdf-craft does not read native PDF Info or
use the filename. To opt in, configure extraction with `extract_book_metadata=True` and an explicit
`metadata_llm`; it verifies front-page OCR evidence, then uses native PDF Info only to fill OCR-missing
fields. The same normalized metadata corrects the document metadata of a PDF produced by
`translate_pdf()` or `patch_pdf_with_extraction()`. When `book_meta` is supplied for rendering, its non-empty fields override the corresponding
manifest fields one by one; `None`, an empty string, and empty contributor lists leave the manifest
value intact rather than clearing it.

The remaining EPUB options are useful when the default rendering is not appropriate:

| Option | Purpose |
| --- | --- |
| `label_language` | Fixed navigation/default-title labels only: exact `"zh"` or `"en"`; all other values fall back to English. Default: `"en"`. Does not select or restrict translation language. Replaces the former `lan` keyword. |
| `table_render` | An `epub_generator.TableRender` mode such as `HTML` or `CLIPPING`. |
| `latex_render` | An `epub_generator.LaTeXRender` mode such as `MATHML`, `SVG`, or `CLIPPING`. |
| `inline_latex` | Keep inline LaTeX expressions; defaults to `True`. |

## Translate during conversion

Pass an `XMLTranslator` before Markdown or EPUB rendering. It translates structured XML groups through the configured text model before the selected output is rendered.

```python
from pdf_craft import SubmitKind

craft.convert_pdf_to_markdown(
    "book.pdf",
    "book.zh.md",
    translator=translator,
    submit=SubmitKind.REPLACE,
)
```

Use `SubmitKind.REPLACE` for a target-language-only document. `APPEND_TEXT` appends translated text to the same text flow, while `APPEND_BLOCK` adds separate translated blocks, which is generally the clearer bilingual layout for Markdown and EPUB. The high-level conversion methods perform at most one translation; advanced applications should compose the extraction APIs explicitly.

## Work explicitly with a PDFCraftExtraction

A `PDFCraftExtraction` is pdf-craft's source-mapped intermediate document. It contains chapters, assets, page geometry, document metadata, and optional TOC, cover, page furniture, and translations. On disk it is exchanged as a `.pcex` ZIP file, so it can be stored or moved to another machine without carrying the analysis/OCR cache.

Use an explicit `.pcex` when the same extraction must feed more than one output, or when translation is a distinct operation:

```python
extraction = craft.extract_pdf("book.pdf", "work/book.pcex")

translated = craft.translate_extraction(
    extraction,
    "work/book.zh.pcex",
    translator,
    translation_id="zh-main",
    target_language="zh",
)
print(craft.list_translations(translated))
```

`extract_pdf()` deliberately requires a `.pcex` path: its result is meant to survive after the method returns. `translate_extraction()` creates a new archive at `output_path` while retaining the source and adding an independently identified translation. Markdown, EPUB, and PDF patching can select that result with `translation_id`. The standard entry accepts `XMLTranslator` directly: narrative and metadata always run, while `with_anchored` and `with_furniture` independently enable their fixed stages.

## Translate and patch a PDF

To create a translated PDF, first extract the source and then ask pdf-craft to translate and patch it:

```python
extraction = craft.extract_pdf("book.pdf", "work/book.pcex")

craft.translate_pdf(
    "book.pdf",
    extraction,
    "book.zh.pdf",
    translator,
    # Also translate running heads, footers, and folios present in the PCEX.
    with_furniture=True,
    # Allow recoverable failures; failed writeback keeps the original PDF page.
    error_policy=True,
)
```

`translator` is an `XMLTranslator`. PDF translation always
creates a translated PCEX view before it patches the source PDF; text-only
callback translation is not supported. It translates narrative and optional
furniture only. Anchored image/table text is skipped because PDF patching does
not have field-level geometry for it.

`with_furniture` defaults to `False` because page-furniture translation is an
additional LLM pass. Enable it for a PCEX that contains `furnitures.xml`;
the source PCEX is normally produced with the default
`ExtractionOptions.includes_furniture=True`. PDF-to-Markdown and PDF-to-EPUB
workflows explicitly omit furniture because those output formats do not render
fixed page furniture.

If translation happened elsewhere, call `patch_pdf_with_extraction()` instead. It runs neither OCR nor an LLM; it uses the translated extraction's page geometry to patch the source PDF.

```python
craft.patch_pdf_with_extraction(
    "book.pdf",
    "work/book.zh.pcex",
    "book.zh.pdf",
    translation_id="zh-main",
)
```

Omit `translation_id` to patch the current PCEX root content. An explicit
ID selects that stored translation's narrative, metadata, and optional
furniture. Anchored image/table text remains available to Markdown and EPUB but
is not patched into PDF because PCEX does not retain field-level PDF geometry.

By default a PDF fill error stops the operation. Pass `error_policy=True` to either PDF entry point when a service should keep processing later pages: a failing page is emitted as its original PDF page, with the original source text, while successful pages retain selectable translated text. If every page scheduled for fill fails, `NoUsableFillPagesError` is raised instead of producing an all-fallback PDF. This option deliberately catches any ordinary exception within a page fill transaction and records its traceback; it cannot recover a source document for which no visual pages can be produced.

`error_policy` may also be a synchronous or asynchronous callback receiving
`FailureContext` and returning bool. It receives a final failure's structured
error and source/affected locations, rather than a bare exception. A top-level
callback decides all recoverable categories; use
`ErrorPolicy(ignore_pdf_fill_errors=callback)` to decide only PDF writeback
failures. Leave recovery disabled unless a partial result is useful: failed pages
retain the original PDF content beside successfully patched pages. See
[error handling and migration](ERROR_HANDLING.md).

### What PDF patching can and cannot preserve

The default patcher preserves source fonts and graphics. Before drawing the background masks and Qt translation, it deletes each source character whose bounding box overlaps or touches a padded erase rectangle, including invisible OCR text. Characters outside those masks remain selectable. Shared Forms are edited per invocation. Text used as a clipping path raises a composition error when hit, because removing it would change graphics. Visual Stamps are flattened; interactive annotations are restored above the translation. Ghostscript is only needed when explicitly selecting the legacy outline compiler.

For each padded source box, the eraser renders the original page only to estimate a local, frequency-weighted median RGB background color, then covers the complete rectangle with that color. It intentionally does not restore paper texture, rules, formulae, or artwork. It replaces text and subtitle layouts only; tables and images are not translated in place.

The source PDF and extraction must match. `pages.xml` must contain geometry for every chapter page and its page numbers must be valid for the source file. There is no fallback to OCR caches or re-rendering to recover missing geometry. PDF translation uses replacement text and does not accept a submission mode. Ordinary text that cannot fit its available source regions is force-written from its first source bbox at the minimum size, even when it extends past normal geometry; a headline first uses its natural rightward overflow and then uses the same final fallback when necessary.

For custom fonts, semantic title/body styles, fit rules, alignment, or erase padding, use the lower-level public `PDFPatcher`, `PatchTextOptions`, `PatchTextStyle`, `EraseOptions`, and `PDFTranslationPipeline` APIs described in the [API reference](API_REFERENCE.md). When `font_name` is omitted (or empty), patching selects one real local Qt font family and keeps it for the entire run. An explicitly configured but unavailable family does not stop patching: Qt uses its normal fallback chain. PDF patching requires the local Qt/PySide6 runtime, Poppler for the default background-sampling handler, and suitable fonts.

During translation, a model may flatten source LaTeX into natural text such as “16 times”. XML fill validates its complete text against that translation first. Only after the fill loop accepts the response does the program restore the original formulas. This deliberate post-processing may change their textual representation without starting another fill request.

`render_inline_formulas=True` is the default: available Matplotlib and TeX produce vector formula fragments, while a disabled or unavailable renderer (or one failed formula) falls back to readable plain text without failing the PDF. Vector fragments include a plain-text PDF `/ActualText` semantic replacement; this is not a complete tagged-PDF structure, so reader-specific selection and copy order relative to surrounding text are not guaranteed.

When `max_font_size` is omitted from `PatchTextOptions` or a `PatchTextStyle`, text uses automatic geometry-driven fitting rather than a typographic default size. Its search starts from a small internal probe and expands until the source boxes reject the next size; the largest size that fits wins. Supplying a numeric `max_font_size` remains an explicit hard ceiling, including values such as `12`.

### Headline hierarchy and bounded layout windows

PDF patching treats each `TextFlowItem` as one text flow, even when it has source boxes on more than one page. Its first pass chooses one uniform fitted size and moves complete wrapped lines through ordered source boxes. A second, page-local normalization may then move each already-assigned box toward its semantic-level target size without moving text between boxes or changing that box's line count. Qt remains responsible for ordinary Unicode shaping, wrapping, bidirectional text, and font fallback; pdf-craft does not add language-specific line-break rules. A rendered inline formula, together with its immediately following visible spacer, is the sole indivisible atom.

Within a releasable page window, `body` TextFlowItems are fitted before `heading` TextFlowItems. The largest fitted body size on every page touched by a headline supplies its preferred lower bound, multiplied by `headline_min_body_ratio` (default `1.2`). A semantic style can override that ratio with `minimum_body_font_ratio`; the normal fitting search may still choose a larger title size when its boxes permit it.

A numeric `max_font_size` is a hard limit for every semantic style, including an implicit `heading` inherited from `PatchTextOptions`. When that ceiling is lower than the body-relative preference, the ceiling wins in both initial fitting and page-local normalization. If the resulting headline cannot fit its width at the selected size, it is anchored at the source box's left edge and continues naturally to the right rather than failing or being skipped.

```python
options = PatchTextOptions(
    styles={
        "body": PatchTextStyle(font_name="Noto Serif CJK SC", max_font_size=11),
        "heading": PatchTextStyle(
            font_name="Noto Sans CJK SC",
            max_font_size=24,
            minimum_body_font_ratio=1.35,
        ),
    },
    headline_min_body_ratio=1.2,
    headline_fallback_font_size=14,
)
```

If a title page has no drawn body text, its reference is chosen deterministically: a preceding body on the current window, then the preceding completed window, then a following body in the current window, then `headline_fallback_font_size` (or the title style's own minimum). The required headline lower bound is preserved even when natural-width overflow is necessary.

The window closes after all paragraphs that can touch its pages have been planned. The patcher then renders, samples for erasure, composes, and releases one source page image at a time. Thus a very long cross-page paragraph does not retain a book-wide collection of page rasters; the independent eraser and Qt text layers remain separate.

## Extraction controls

Pass `ExtractionOptions` through the `extraction=` argument to tune a single extraction run:

```python
from pdf_craft import ExtractionOptions

options = ExtractionOptions(
    page_indexes={1, 2, 3},
    dpi=250,
    includes_cover=True,
)
craft.convert_pdf_to_markdown("book.pdf", "sample.md", extraction=options)
```

Useful controls include `page_indexes` (one-based page numbers), `dpi`, `ocr_size`, OCR token limits, cover and footnote inclusion, and `on_extraction_event` for per-page observability.

`includes_furniture=True` is the default for a direct PDF-to-PCEX extraction: it retains any native-PDF running heads, footers, and folios as `furnitures.xml`, but does not translate them. Scan-only pages can simply have no native furniture to retain. The `convert_pdf_to_markdown()` and `convert_pdf_to_epub()` convenience workflows deliberately override this option to `False`, even if it is set on the supplied `ExtractionOptions`, because neither result renders fixed page furniture.

Set `extract_book_metadata=True` with a separate `metadata_llm` to infer bibliographic metadata from front-page OCR. It is stored in PCEX and, when that PCEX is patched, written to the output PDF's document metadata; see [the API reference](API_REFERENCE.md#extractionoptions) for its bounded evidence and fallback policy. `ErrorPolicy.ignore_pdf_render_errors` and `ErrorPolicy.ignore_ocr_errors` can allow a long document to continue past selected failures, but a completed run still needs output review: skipped pages are not successfully recognized pages.

For errors involving Poppler, cache paths, local CUDA, or vendor credentials, see [Troubleshooting](TROUBLESHOOTING.md).


## Preview extraction, translation and PDF layout

In 2.5.0, `ExtractionOptions(on_body=..., on_footnote=...)` delivers independent
PCEX XML snapshots during extraction. `ExtractionOptions.on_metadata` observes
one finalized metadata view; when LLM extraction is enabled, it waits for the
merge with native PDF fallback.

For existing PCEX files, `render_markdown`, `render_epub`, and `translate_extraction`
deliver `ParagraphContent[SourceRegion]` through optional `on_body` / `on_footnote`.
Paragraphs remain whole across pages, with separate Markdown text and optional
original. Rendering can translate online with `translator=translator`; no complete
translated PCEX is required first. A `translator` cannot be combined with a stored
`translation_id`.

`patch_pdf_with_extraction` and `translate_pdf` deliver `PDFParagraphContent` after
final layout, including source regions, font sizes, text runs, formula placements
and erasure masks. Layout coordinates use source OCR pixels. These are paragraph
previews, not page PDFs or final artifacts.

Use `read_pages()` / `read_asset()` for public geometry and resource access.
Metadata translation has a separate optional `on_metadata`; in one-shot conversion
this top-level callback observes translation, while `ExtractionOptions.on_metadata`
observes extraction. Synchronous and asynchronous callbacks are supported, and
async callbacks are awaited. See the [callback guide](STREAMING.md) for examples,
coordinate mapping, timing and output semantics.


See [error handling](ERROR_HANDLING.md) for strict defaults, source locations and task-wide all-fallback protection.

Translation progress uses cumulative source words; see [the event and counting contract](TRANSLATION_PROGRESS.md).


Pure PDF inspection/rendering continues to use Poppler. Character removal is an editing operation: its font/CMap and content-stream reads may use pdfminer and pypdf without adding a preflight gate to extraction. ExtGState fonts are honored; Hidden/NoView Stamps remain invisible in the screen-view base.
