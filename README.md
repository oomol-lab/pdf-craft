<div align=center>
  <h1>PDF Craft</h1>
  <p>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/merge-build.yml" target="_blank"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/merge-build.yml" alt="ci" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/badge/pip_install-pdf--craft-blue" alt="pip install pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/v/pdf-craft.svg" alt="pypi pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="python versions" /></a>
    <a href="https://deepwiki.com/oomol-lab/pdf-craft" target="_blank"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt="license" /></a>
  </p>
  <p><a href="https://hub.oomol.com/package/pdf-craft?open=true" target="_blank"><img src="https://static.oomol.com/assets/button.svg" alt="Open in OOMOL Studio" /></a></p>
  <p>English | <a href="./README_zh-CN.md">中文</a></p>
</div>

## Introduction

pdf-craft converts PDF files into various other formats, with a focus on handling scanned book PDFs.

This project is based on [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR) for document recognition. It supports the recognition of complex content such as tables and formulas. With GPU acceleration, pdf-craft can complete the entire conversion process from PDF to Markdown or EPUB locally. During the conversion, pdf-craft automatically identifies document structure, accurately extracts body text, and filters out interfering elements like headers and footers. For academic or technical documents containing footnotes, formulas, and tables, pdf-craft handles them properly, preserving these important elements (including images and other assets within footnotes). When converting to EPUB, the table of contents is automatically generated. The final Markdown or EPUB files maintain the content integrity and readability of the original book.

## Lightweight and Fast

Starting from the official v1.0.0 release, pdf-craft fully embraces [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR) and no longer relies on LLM for text correction. This change brings significant performance improvements: the entire conversion process is completed locally without network requests, eliminating the long waits and occasional network failures of the old version.

However, the new version has also removed the LLM text correction feature. If your use case still requires this functionality, you can continue using the old version [v0.2.8](https://github.com/oomol-lab/pdf-craft/tree/v0.2.8).

### Online Demo

We provide an [online demo platform](https://pdf.oomol.com/) that lets you experience PDF Craft's conversion capabilities without any installation. You can directly upload PDF files and convert them.

[![PDF Craft Online Demo](docs/images/website-en.png)](https://pdf.oomol.com/)

## Quick Start

### Installation

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install pdf-craft
```

The above commands are for quick setup only. To actually use pdf-craft, you need to **install Poppler** for PDF parsing (required for all use cases) and **configure a CUDA environment** for OCR recognition (required for actual conversion). Please refer to the [Installation Guide](docs/INSTALLATION.md) for detailed instructions.

### Quick Start

#### Convert to Markdown

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    markdown_assets_path="images",
)
```

![mdmd](https://github.com/user-attachments/assets/d7082496-13b8-4728-9e79-44e2888e57fd)

#### Convert to EPUB

```python
from pdf_craft import transform_epub, BookMeta

transform_epub(
    pdf_path="input.pdf",
    epub_path="output.epub",
    book_meta=BookMeta(
        title="Book Title",
        authors=["Author"],
    ),
)
```

![20251218-162533](https://github.com/user-attachments/assets/7f6df04a-1fa7-48b3-aa5e-d2d056304ad6)

## Detailed Usage

### Convert to Markdown

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    markdown_assets_path="images",
    analysing_path="temp",  # Optional: specify temporary folder
    ocr_size="gundam",  # Optional: tiny, small, base, large, gundam
    models_cache_path="models",  # Optional: model cache path
    dpi=300,  # Optional: DPI for rendering PDF pages (default: 300)
    max_page_image_file_size=None,  # Optional: max image file size in bytes, auto-adjust DPI if exceeded
    includes_cover=False,  # Optional: include cover
    includes_footnotes=True,  # Optional: include footnotes
    ignore_pdf_errors=False,  # Optional: continue on PDF rendering errors
    ignore_ocr_errors=False,  # Optional: continue on OCR recognition errors
    generate_plot=False,  # Optional: generate visualization charts
    toc_llm=None,  # Optional: LLM instance for enhanced TOC extraction
    toc_assumed=False,  # Optional: whether to assume TOC pages exist (default: False)
)
```

### Convert to EPUB

```python
from pdf_craft import transform_epub, BookMeta, TableRender, LaTeXRender

transform_epub(
    pdf_path="input.pdf",
    epub_path="output.epub",
    analysing_path="temp",  # Optional: specify temporary folder
    ocr_size="gundam",  # Optional: tiny, small, base, large, gundam
    models_cache_path="models",  # Optional: model cache path
    dpi=300,  # Optional: DPI for rendering PDF pages (default: 300)
    max_page_image_file_size=None,  # Optional: max image file size in bytes, auto-adjust DPI if exceeded
    includes_cover=True,  # Optional: include cover
    includes_footnotes=True,  # Optional: include footnotes
    ignore_pdf_errors=False,  # Optional: continue on PDF rendering errors
    ignore_ocr_errors=False,  # Optional: continue on OCR recognition errors
    generate_plot=False,  # Optional: generate visualization charts
    toc_llm=None,  # Optional: LLM instance for enhanced TOC extraction
    toc_assumed=True,  # Optional: whether to assume TOC pages exist (default: True for EPUB)
    book_meta=BookMeta(
        title="Book Title",
        authors=["Author 1", "Author 2"],
        publisher="Publisher",
        language="en",
    ),
    lan="en",  # Optional: language (zh/en)
    table_render=TableRender.HTML,  # Optional: table rendering method
    latex_render=LaTeXRender.MATHML,  # Optional: formula rendering method
    inline_latex=True,  # Optional: preserve inline LaTeX expressions
)
```

### Model Management

pdf-craft depends on DeepSeek OCR models, which are automatically downloaded from Hugging Face on first run. You can control model storage and loading behavior through the `models_cache_path` and `local_only` parameters.

#### Pre-download Models

In production environments, it is recommended to download models in advance to avoid downloading on first run:

```python
from pdf_craft import predownload_models

predownload_models(
    models_cache_path="models",  # Specify model cache directory
    revision=None,  # Optional: specify model version
)
```

#### Specify Model Cache Path

By default, models are downloaded to the system's Hugging Face cache directory. You can customize the cache location through the `models_cache_path` parameter:

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    models_cache_path="./my_models",  # Custom model cache directory
)
```

#### Offline Mode

If you have pre-downloaded the models, you can use `local_only=True` to disable network downloads and ensure only local models are used:

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    models_cache_path="./my_models",
    local_only=True,  # Use local models only, do not download from network
)
```

## API Reference

### OCR Models

The `ocr_size` parameter accepts a `DeepSeekOCRSize` type:

- `tiny` - Smallest model, fastest speed
- `small` - Small model
- `base` - Base model
- `large` - Large model
- `gundam` - Largest model, highest quality (default)

### Table Rendering Methods

- `TableRender.HTML` - HTML format (default)
- `TableRender.CLIPPING` - Clipping format (directly clips table images from the original PDF scan)

### Formula Rendering Methods

- `LaTeXRender.MATHML` - MathML format (default)
- `LaTeXRender.SVG` - SVG format
- `LaTeXRender.CLIPPING` - Clipping format (directly clips formula images from the original PDF scan)

### Inline LaTeX

The `inline_latex` parameter (EPUB only, default: `True`) controls whether to preserve inline LaTeX expressions in the output. When enabled, inline mathematical formulas are preserved as LaTeX code, which can be rendered by compatible EPUB readers.

### Table of Contents Detection

The `toc_assumed` parameter controls how pdf-craft handles table of contents extraction:

- `False` (default for Markdown): Assumes no TOC pages exist. The conversion generates TOC based on document headings only, without detecting or processing TOC pages.
- `True` (default for EPUB): Assumes TOC pages exist. The conversion uses statistical analysis to detect TOC pages and extract chapter structure.

For books with complex chapter hierarchies, you can configure the optional `toc_llm` parameter to enable LLM-powered chapter title analysis, which provides more accurate TOC hierarchy detection.

#### LLM-Enhanced TOC Extraction

To use LLM-enhanced TOC extraction, you need to configure an LLM instance:

```python
from pdf_craft import transform_epub, BookMeta, LLM

# Configure LLM for TOC extraction
toc_llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",  # Or your LLM provider URL
    model="gpt-4",
    token_encoding="cl100k_base",
    timeout=60.0,
    retry_times=3,
    retry_interval_seconds=5.0,
)

transform_epub(
    pdf_path="input.pdf",
    epub_path="output.epub",
    toc_assumed=True,  # Enable TOC detection
    toc_llm=toc_llm,  # Enable LLM-powered chapter title analysis
    book_meta=BookMeta(
        title="Book Title",
        authors=["Author"],
    ),
)
```

### Custom PDF Handler

By default, pdf-craft uses Poppler (via `pdf2image`) for PDF parsing and rendering. If Poppler is not in your system PATH, you can specify a custom path:

```python
from pdf_craft import transform_markdown, DefaultPDFHandler

# Specify custom Poppler path
transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    pdf_handler=DefaultPDFHandler(poppler_path="/path/to/poppler/bin"),
)
```

If not specified, pdf-craft will use Poppler from your system PATH. For advanced use cases, you can also implement the `PDFHandler` protocol to use alternative PDF libraries.

### Error Handling

The `ignore_pdf_errors` and `ignore_ocr_errors` parameters provide flexible error handling options. You can use them in two ways:

**1. Boolean Mode** - Simple on/off control:

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    ignore_pdf_errors=True,  # Ignore all PDF rendering errors
    ignore_ocr_errors=True,  # Ignore all OCR recognition errors
)
```

When set to `True`, processing continues when errors occur on individual pages, inserting a placeholder message instead of stopping the entire conversion.

**2. Custom Function Mode** - Fine-grained control:

```python
from pdf_craft import transform_markdown, OCRError, PDFError

def should_ignore_ocr_error(error: OCRError) -> bool:
    # Only ignore specific types of OCR errors
    return error.kind == "recognition_failed"

def should_ignore_pdf_error(error: PDFError) -> bool:
    # Custom logic to decide which PDF errors to ignore
    return "timeout" in str(error)

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    ignore_ocr_errors=should_ignore_ocr_error,  # Pass custom function
    ignore_pdf_errors=should_ignore_pdf_error,  # Pass custom function
)
```

This allows you to implement custom logic for deciding which specific errors should be ignored during conversion.

## Related Open Source Libraries

[epub-translator](https://github.com/oomol-lab/epub-translator) uses AI large language models to automatically translate EPUB e-books while 100% preserving the original book's format, illustrations, table of contents, and layout. It also generates bilingual versions for convenient language learning or international sharing. When combined with this library, you can convert and translate scanned PDF books. For a demonstration, see this [video: Convert PDF scanned books to EPUB format and translate to bilingual books](https://www.bilibili.com/video/BV1tMQZY5EYY).

## License

This project is licensed under the MIT License. See the [LICENSE](./LICENSE) file for details.

Starting from v1.0.0, pdf-craft has fully migrated to DeepSeek OCR (MIT license), removing the previous AGPL-3.0 dependency, allowing the entire project to be released under the more permissive MIT license. Note that pdf-craft has a transitive dependency on easydict (LGPLv3) via DeepSeek OCR. Thanks to the community for their support and contributions!

## Acknowledgments

- [DeepSeekOCR](https://github.com/deepseek-ai/DeepSeek-OCR)
- [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor)
- [pyahocorasick](https://github.com/WojciechMula/pyahocorasick)
