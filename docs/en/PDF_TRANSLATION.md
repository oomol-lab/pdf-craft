# PDF conversion and translation

This guide covers workflows that start with a PDF: converting it to Markdown or EPUB, translating while converting, and placing translated text back onto the pages of the original PDF. For translating an EPUB you already have, see [EPUB translation](EPUB_TRANSLATION.md).

## Choose the workflow that fits the result you need

| Goal | Start here | What it produces |
| --- | --- | --- |
| Convert a PDF once | `convert_pdf_to_markdown()` or `convert_pdf_to_epub()` | Markdown or EPUB |
| Translate as the PDF is converted | Pass one `translator` to either conversion method | Translated Markdown or EPUB |
| Keep, inspect, or reuse OCR output | `extract_pdf()`, then render or translate the returned package | A durable `DocumentPackage` directory |
| Produce a translated PDF | `translate_pdf()` or `patch_pdf_with_package()` | A new PDF with translated text over the source pages |

For a one-off conversion, use the two `convert_pdf_to_*` methods. They extract the PDF, optionally translate it once, and render the final file. Use the lower-level package APIs only when you need a persistent intermediate package or need to control each stage separately.

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

The return value records OCR input and output tokens for the run. By default, pdf-craft creates a temporary `DocumentPackage` workspace and removes it on success or failure. If you want to inspect OCR XML, reuse it for another output format, or retain it after an error, provide `package_path` yourself:

```python
craft.convert_pdf_to_markdown(
    "book.pdf",
    "book.md",
    package_path="work/book-package",
    assets_path="output/assets",
)
```

`assets_path` controls where Markdown images and other extracted assets are written. A caller-supplied package directory is persistent and is the caller's responsibility to manage.

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

Use `SubmitKind.REPLACE` for a target-language-only document. `APPEND_TEXT` appends translated text to the same text flow, while `APPEND_BLOCK` adds separate translated blocks, which is generally the clearer bilingual layout for Markdown and EPUB. The high-level conversion methods perform at most one translation; advanced applications that need additional package transformations should compose the lower-level package APIs explicitly.

## Work explicitly with a DocumentPackage

A `DocumentPackage` is pdf-craft's on-disk, render-ready representation of an extracted document: chapters, assets, OCR metadata, and page geometry live together in one directory. It is intended for durable intermediate results, not as a separate plugin protocol.

Use an explicit package when the same extraction must feed more than one output, or when translation is a distinct operation:

```python
package = craft.extract_pdf("book.pdf", "work/book-package")

translated = craft.translate_package(
    package,
    "work/book-package-zh",
    translator,
    submit=SubmitKind.REPLACE,
)
craft.render_markdown(translated, "book.zh.md", assets_path="output/assets")
craft.render_epub(translated, "book.zh.epub", lan="zh")
```

`extract_pdf()` deliberately requires a package path: its result is meant to survive after the method returns. `translate_package()` creates a new package at `output_path`; it does not overwrite the source package.

## Translate and patch a PDF

To create a translated PDF, first extract the source and then ask pdf-craft to translate and patch it:

```python
package = craft.extract_pdf("book.pdf", "work/book-package")

craft.translate_pdf(
    "book.pdf",
    package,
    "book.zh.pdf",
    translator,
)
```

The `transformer` may be a chapter transformer or a simple `Callable[[str], str]` for text-only translation:

```python
def translate_text(text: str) -> str:
    return call_your_llm(text)

craft.translate_pdf("book.pdf", package, "book.zh.pdf", translate_text)
```

If translation happened elsewhere, call `patch_pdf_with_package()` instead. It runs neither OCR nor an LLM; it uses the translated package's page geometry to patch the source PDF.

```python
craft.patch_pdf_with_package(
    "book.pdf",
    "work/book-package-zh",
    "book.zh.pdf",
)
```

### What PDF patching can and cannot preserve

PDF patching is a page-overlay workflow, not a general-purpose PDF layout engine. Each source page is rendered as an image and translated text is placed over its OCR bounding boxes. The output therefore does not retain the source PDF's selectable vector text, links, annotations, or other page objects. It replaces text and subtitle layouts only; tables and images are not translated in place.

The source PDF and package must match. The package needs page geometry for every chapter page and its page numbers must be valid for the source file. `APPEND_BLOCK` is rejected for PDF output because new block-level content cannot safely be added to a fixed page. Text that cannot fit its original bounding box fails before a partial output PDF is left behind.

For custom fonts, fit rules, alignment, padding, or overflow handling, use the lower-level public `PDFPatcher`, `PatchTextOptions`, and `PDFTranslationPipeline` APIs described in the [API reference](API_REFERENCE.md).

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
