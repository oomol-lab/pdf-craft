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
    lan="en",
)
```

If `book_meta` is omitted, pdf-craft attempts to read metadata from the source PDF. The remaining EPUB options are useful when the default rendering is not appropriate:

| Option | Purpose |
| --- | --- |
| `lan` | Content language: `"zh"` or `"en"`. |
| `table_render` | An `epub_generator.TableRender` mode such as `HTML` or `CLIPPING`. |
| `latex_render` | An `epub_generator.LaTeXRender` mode such as `MATHML`, `SVG`, or `CLIPPING`. |
| `inline_latex` | Keep inline LaTeX expressions; defaults to `True`. |

## Translate during conversion

Pass one chapter translator before Markdown or EPUB rendering. The translator is supplied by your application; it is responsible for calling a text model and returning the transformed chapter.

```python
from pdf_craft import SubmitKind

# translator implements transform(chapter).
craft.convert_pdf_to_markdown(
    "book.pdf",
    "book.zh.md",
    translator=translator,
    submit=SubmitKind.REPLACE,
)
```

Use `SubmitKind.REPLACE` for a target-language-only document. `APPEND_TEXT` appends translated text to the same text flow, while `APPEND_BLOCK` adds separate translated blocks, which is generally the clearer bilingual layout for Markdown and EPUB. The high-level conversion methods perform at most one translation; advanced applications should compose the extraction APIs explicitly.

## Work explicitly with a PDFCraftExtraction

A `PDFCraftExtraction` is pdf-craft's source-mapped intermediate document. It contains chapters, assets, page geometry, document metadata, and optional TOC and cover. On disk it is exchanged as a `.pcex` ZIP file, so it can be stored or moved to another machine without carrying the analysis/OCR cache.

Use an explicit `.pcex` when the same extraction must feed more than one output, or when translation is a distinct operation:

```python
extraction = craft.extract_pdf("book.pdf", "work/book.pcex")

translated = craft.translate_extraction(
    extraction,
    "work/book.zh.pcex",
    translator,
    submit=SubmitKind.REPLACE,
)
craft.render_markdown(translated, "book.zh.md", assets_path="output/assets")
craft.render_epub(translated, "book.zh.epub", lan="zh")
```

`extract_pdf()` deliberately requires a `.pcex` path: its result is meant to survive after the method returns. `translate_extraction()` creates a new archive at `output_path`; it does not overwrite the source extraction. Rendering, translation, and PDF patching accept either the returned object or a `.pcex` path, but not an ordinary directory.

## Translate and patch a PDF

To create a translated PDF, first extract the source and then ask pdf-craft to translate and patch it:

```python
extraction = craft.extract_pdf("book.pdf", "work/book.pcex")

craft.translate_pdf(
    "book.pdf",
    extraction,
    "book.zh.pdf",
    translator,
    # Keep an unmodified visual-base page if one page's fill fails.
    ignore_errors=True,
)
```

The `transformer` may be a chapter transformer or a simple `Callable[[str], str]` for text-only translation:

```python
def translate_text(text: str) -> str:
    return call_your_llm(text)

craft.translate_pdf("book.pdf", extraction, "book.zh.pdf", translate_text)
```

If translation happened elsewhere, call `patch_pdf_with_extraction()` instead. It runs neither OCR nor an LLM; it uses the translated extraction's page geometry to patch the source PDF.

```python
craft.patch_pdf_with_extraction(
    "book.pdf",
    "work/book.zh.pcex",
    "book.zh.pdf",
)
```

By default a PDF fill error stops the operation. Pass `ignore_errors=True` to either PDF entry point when a service should keep processing later pages: a failing page is emitted as its Ghostscript visual base, with no selectable source text, while successful pages retain their translation layers. If every page scheduled for fill fails, `NoUsableFillPagesError` is raised instead of producing an all-fallback PDF. This option deliberately catches any ordinary exception within a page fill transaction and records its traceback; it cannot recover a source document for which no visual pages can be produced.

### What PDF patching can and cannot preserve

PDF patching is a page-overlay workflow, not a general-purpose PDF layout engine. Before overlays are composed, Ghostscript compiles every source page's non-Annotation content into a font-free visual base. Source vector text remains visible as linework, but is no longer selectable, searchable, or extractable; hidden OCR text is removed as well. The original page's complete `/Annots` array is instead reattached above the translated text, so links, highlights, notes, form widgets, and other PDF Annotations remain independent and interactive. The translated text is the only ordinary selectable text layer.

For each padded source box, the eraser renders the original page only to estimate a local, frequency-weighted median RGB background color, then covers the complete rectangle with that color. It intentionally does not restore paper texture, rules, formulae, or artwork. It replaces text and subtitle layouts only; tables and images are not translated in place.

The source PDF and extraction must match. `pages.xml` must contain geometry for every chapter page and its page numbers must be valid for the source file. There is no fallback to OCR caches or re-rendering to recover missing geometry. `APPEND_BLOCK` is rejected for PDF output because new block-level content cannot safely be added to a fixed page. Ordinary text that cannot fit its available source regions is force-written from its first source bbox at the minimum size, even when it extends past normal geometry; a headline first uses its natural rightward overflow and then uses the same final fallback when necessary.

For custom fonts, semantic title/body styles, fit rules, alignment, or erase padding, use the lower-level public `PDFPatcher`, `PatchTextOptions`, `PatchTextStyle`, `EraseOptions`, and `PDFTranslationPipeline` APIs described in the [API reference](API_REFERENCE.md). When `font_name` is omitted (or empty), patching selects one real local Qt font family and keeps it for the entire run. An explicitly configured but unavailable family does not stop patching: Qt uses its normal fallback chain. PDF patching requires the local Qt/PySide6 runtime, Poppler (or a supplied `PDFHandler`), Ghostscript, and suitable fonts. `render_inline_formulas=True` is the default: available Matplotlib and TeX produce vector formula fragments, while a disabled or unavailable renderer (or one failed formula) falls back to readable plain text without failing the PDF.

When `max_font_size` is omitted from `PatchTextOptions` or a `PatchTextStyle`, text uses automatic geometry-driven fitting rather than a typographic default size. Its search starts from a small internal probe and expands until the source boxes reject the next size; the largest size that fits wins. Supplying a numeric `max_font_size` remains an explicit hard ceiling, including values such as `12`.

### Headline hierarchy and bounded layout windows

PDF patching treats each `ParagraphLayout` as one text flow, even when it has source boxes on more than one page. Its first pass chooses one uniform fitted size and moves complete wrapped lines through ordered source boxes. A second, page-local normalization may then move each already-assigned box toward its semantic-level target size without moving text between boxes or changing that box's line count. Qt remains responsible for ordinary Unicode shaping, wrapping, bidirectional text, and font fallback; pdf-craft does not add language-specific line-break rules. A rendered inline formula, together with its immediately following visible spacer, is the sole indivisible atom.

Within a releasable page window, `text` paragraphs are fitted before `sub_title` paragraphs. The largest fitted body size on every page touched by a headline supplies its preferred lower bound, multiplied by `headline_min_body_ratio` (default `1.2`). A semantic style can override that ratio with `minimum_body_font_ratio`; the normal fitting search may still choose a larger title size when its boxes permit it.

A numeric `max_font_size` is a hard limit for every semantic style, including an implicit `sub_title` inherited from `PatchTextOptions`. When that ceiling is lower than the body-relative preference, the ceiling wins in both initial fitting and page-local normalization. If the resulting headline cannot fit its width at the selected size, it is anchored at the source box's left edge and continues naturally to the right rather than failing or being skipped.

```python
options = PatchTextOptions(
    styles={
        "text": PatchTextStyle(font_name="Noto Serif CJK SC", max_font_size=11),
        "sub_title": PatchTextStyle(
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

Useful controls include `page_indexes` (one-based page numbers), `dpi`, `ocr_size`, OCR token limits, cover and footnote inclusion, and `on_ocr_event` for per-page observability. `ignore_pdf_errors` and `ignore_ocr_errors` can allow a long document to continue past selected failures, but a completed run still needs output review: skipped pages are not successfully recognized pages.

For errors involving Poppler, cache paths, local CUDA, or vendor credentials, see [Troubleshooting](TROUBLESHOOTING.md).
