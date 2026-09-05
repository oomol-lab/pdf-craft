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
  <p><a href="https://trendshift.io/repositories/15538" target="_blank"><img src="https://trendshift.io/api/badge/repositories/15538" alt="oomol-lab%2Fpdf-craft | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a></p>
  <p>English | <a href="./README_zh-CN.md">中文</a></p>
</div>

## What is pdf-craft?

pdf-craft is a PDF-centered conversion library. It turns PDFs into Markdown or EPUB,
and can translate the converted content or write a translated result back to PDF.
It is especially useful for scanned documents: pages that are otherwise only readable
as images become searchable, editable Markdown or EPUB.

The pipeline is designed for books and academic or technical documents, including
body text, tables of contents, footnotes, tables, formulas, and images. OCR can run
entirely on a compatible local GPU, or use a vendor service that supplies remote
compute. Translation uses a separate text LLM.

## Online Version

Want to try the workflow before installing anything? Open [PDF Craft Online](https://inkora.oomol.com/pdf-craft/),
the online version of the same core experience. Upload a PDF in your browser and see
the main workflow in action.

[![PDF Craft Online Version](docs/images/website-en.png)](https://inkora.oomol.com/pdf-craft/)

## Installation

If you are getting started, use the standard installation:

```bash
pip install pdf-craft
```

This includes vendor OCR, Markdown/EPUB rendering, and PDF translation. Vendor OCR
uses remote compute, so your machine does not need CUDA; you provide the service URL,
model name, and API key in the OCR configuration.

Only install the local extra when you explicitly want to run OCR models on your own
NVIDIA GPU. If you are unsure, use the standard installation above:

```bash
pip install "pdf-craft[local]"
```

Local OCR also requires a CUDA-compatible PyTorch build, model storage, and enough
GPU memory. Before processing PDFs, install Poppler; see the [Installation Guide](docs/en/INSTALLATION.md)
for the supported Python versions and complete system setup. If something goes wrong,
start with the [Troubleshooting Guide](docs/en/TROUBLESHOOTING.md).

## Quick Start

The following example converts a scanned PDF into a Markdown file. Replace the
example OCR endpoint, model name, and API key with your own service configuration.

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(ocr=DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr",
)))
craft.convert_pdf_to_markdown(
    "input.pdf", "output.md",
)
```

![PDF to Markdown example](docs/images/pdf2md-en.png)

The conversion uses a temporary analysis workspace automatically and removes it
when the conversion finishes or fails. Pass `analysing_path` to retain diagnostics,
or `extraction_path="book.pcex"` to retain the portable intermediate extraction.

For the complete PDF conversion workflow and customization options, see the
[PDF Translation Guide](docs/en/PDF_TRANSLATION.md) and [API Reference](docs/en/API_REFERENCE.md).
For the field-level contract of the portable intermediate format, see the
[PDFCraftExtraction (`.pcex`) Format Reference](docs/en/PCEX_FORMAT.md).

## Advanced Features

### PDF → EPUB

To produce an EPUB instead of Markdown, call `convert_pdf_to_epub`. This complete
example also shows how to set the book title and author metadata:

```python
from pdf_craft import BookMeta, DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

ocr_config = DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr",
)
craft = PDFCraft(pdf=PDFOptions(ocr=ocr_config))
craft.convert_pdf_to_epub(
    "input.pdf", "output.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)
```

![PDF to EPUB example](docs/images/pdf2epub-en.png)

If `book_meta` is omitted, pdf-craft uses metadata stored in the PDFCraftExtraction
manifest when the PDF was extracted.

### PDF → translated Markdown or EPUB

To translate while converting, pass one chapter `translator` to either conversion method.
The translator sends chapter text to your text LLM and returns the translated chapter.

```python
craft.convert_pdf_to_markdown(
    "input.pdf", "translated.md", translator=translator,
)
craft.convert_pdf_to_epub(
    "input.pdf", "translated.epub", translator=translator,
)
```

### PDF → translated PDF

Use the PDF translation workflow when you want to keep the original PDF layout. It
extracts the page content, translates it, and writes the result back into the matching
source pages. OCR and translation use separate configurations.

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(ocr=DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-ocr-api-key",
    model="deepseek-ocr",
)))

# Placeholder only: replace this with your text LLM call.
def translator(text: str) -> str:
    return text

extraction = craft.extract_pdf("input.pdf", "work/book.pcex")
craft.translate_pdf("input.pdf", extraction, "translated.pdf", translator)
```

### EPUB → translated EPUB

If you already have an EPUB, translate it directly by providing the target language
and a text LLM:

```python
from pdf_craft import LLM, PDFCraft, SubmitKind

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
)

PDFCraft().translate_epub(
    "input.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE, llm=llm,
)
```

`REPLACE` creates a target-language-only edition. Use `APPEND_BLOCK` to keep the
original and append the translation as a separate block, or `APPEND_TEXT` to place
the translation directly after the original text. See the [EPUB translation guide](docs/en/EPUB_TRANSLATION.md)
for prompts, retries, concurrency, caching, progress callbacks, and failure handling.

## OCR Backends and Model Cache

OCR turns page images into text. pdf-craft offers six backends; choose the runtime
location first, then choose the model family:

- **No CUDA or minimal local setup:** choose vendor OCR. Pages are sent to a remote
  service and processed with its compute resources, so you need network access, a
  service URL, and credentials.
- **A compatible NVIDIA GPU and local execution:** choose local OCR. Models are
  cached locally and run on your GPU, which keeps processing on your machine but
  requires CUDA, VRAM, and model files.

DeepSeek OCR and DeepSeek OCR 2 are from [DeepSeek](https://github.com/deepseek-ai/DeepSeek-OCR);
[Unlimited OCR](https://github.com/baidu/Unlimited-OCR) is from Baidu. Each model family
has local and vendor configurations:

| Backend | Owner | Runs on | Choose it when | You need |
| --- | --- | --- | --- | --- |
| `DeepSeekOCRLocalConfig` | DeepSeek | Local GPU | You want local DeepSeek OCR | CUDA, VRAM, model cache |
| `DeepSeekOCR2LocalConfig` | DeepSeek | Local GPU | You want local DeepSeek OCR 2 | CUDA, VRAM, model cache; `base` is the verified preset |
| `UnlimitedOCRLocalConfig` | Baidu | Local GPU | You want local Unlimited OCR | CUDA, VRAM, model cache |
| `DeepSeekOCRVendorConfig` | DeepSeek | Remote service | You do not have CUDA or prefer remote DeepSeek OCR | URL, model, API key, network |
| `DeepSeekOCR2VendorConfig` | DeepSeek | Remote service | You prefer remote DeepSeek OCR 2 | URL, model, API key, network |
| `UnlimitedOCRVendorConfig` | Baidu | Remote service | You prefer remote Unlimited OCR | URL, credentials, network |

If you simply want to get the workflow running, start with the vendor you already
have credentials for. Choose local OCR when you specifically want local execution.
The library accepts these configuration objects through `PDFOptions(ocr=...)` and
does not read environment variables. See the [OCR Backend Guide](docs/en/OCR_BACKENDS.md)
for detailed configuration examples.

Unlimited OCR local supports `base` and `gundam`. DeepSeek OCR 2 local is verified
with `base`; an explicit `tiny` selection fails early with a clear message.

### Model Cache and Common Parameters

Local OCR models are downloaded from Hugging Face by default. You can pre-download
one into a chosen cache directory and then run with `local_only=True`:

```python
from pdf_craft import DeepSeekOCRLocalConfig, predownload_models

predownload_models(
    ocr=DeepSeekOCRLocalConfig(models_cache_path="models"),
    revision=None,
)
```

`ocr_size` supports `tiny`, `small`, `base`, `large`, and `gundam`, although presets
vary by backend. Markdown defaults to `toc_assumed=False`; EPUB defaults to
`toc_assumed=True`. Complex chapter hierarchies can use an optional `toc_llm`.

## Related Projects

- [Wiki Graph](https://github.com/oomol-lab/wiki-graph): turn a converted EPUB or Markdown book into structured summaries, chapter topology, and a knowledge graph.

## License

This project is licensed under the MIT License. See the [LICENSE](./LICENSE) file for details.

Since v1.0.0, pdf-craft has used DeepSeek OCR under the MIT license and removed
the previous AGPL-3.0 dependency. The project still receives `easydict` transitively
through the OCR stack under the LGPLv3 license. Thanks to the community for their
support and contributions.

## Acknowledgments

- [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR)
- [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor)
- [pyahocorasick](https://github.com/WojciechMula/pyahocorasick)
