# Architecture

**Scope:** package layout, public API, and module ownership. **Not scope:** OCR algorithm details, development commands, or release steps. **Read when:** deciding where code belongs or changing public imports.

## Package Surface

`pdf_craft/__init__.py` is the public import surface. `pdf_craft/functions.py` provides convenience functions that create a `Transform` and delegate to instance methods. `pdf_craft/transform.py` is the orchestration boundary for full Markdown and EPUB conversion.

Treat these names and defaults as public API unless the task explicitly requests a breaking API change:

- `transform_markdown`
- `transform_epub`
- `predownload_models`
- `Transform`
- `LLM`
- `PDFHandler`, `PDFDocument`, `DefaultPDFHandler`, `DefaultPDFDocument`
- `BookMeta`, `TableRender`, `LaTeXRender`

## Module Ownership

- `pdf_craft/pdf/` owns PDF metadata, rendering, page references, DeepSeek OCR integration through `doc-page-extractor`, and OCR XML page data.
- `pdf_craft/toc/` owns table-of-contents detection and title-level analysis, including optional LLM-assisted analysis.
- `pdf_craft/sequence/` owns chapter construction from OCR page XML and TOC facts.
- `pdf_craft/markdown/` owns Markdown paragraph parsing and Markdown output rendering.
- `pdf_craft/epub/` owns conversion from chapter data to `epub-generator` records.
- `pdf_craft/llm/` owns optional LLM calls used for enhanced TOC analysis. Core conversion should remain usable without this optional enhancement.
- `pdf_craft/common/` owns reusable filesystem, XML, asset, and statistical helpers.

## External Package Boundaries

`doc-page-extractor` and `epub-generator` are pinned runtime dependencies. Fixes inside those packages should normally happen in their own repositories and then be consumed by version bump or explicit local testing. The sync scripts under `scripts/` overwrite installed packages inside `.venv`; they are manual local-development helpers, not normal project setup.

`torch` and `torchvision` are intentionally not package dependencies because users must choose CPU or CUDA wheels for their environment. Do not add them to runtime dependencies casually.
