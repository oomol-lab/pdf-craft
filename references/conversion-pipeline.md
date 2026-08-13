# Conversion Pipeline

**Scope:** PDF-to-output flow, intermediate artifacts, and conversion contracts. **Not scope:** general setup or packaging. **Read when:** modifying extraction, TOC, chapter generation, Markdown rendering, or EPUB rendering.

## Runtime Flow

`Transform.transform_markdown()` and `Transform.transform_epub()` both call `_extract_from_pdf()` before rendering output. The extraction flow is:

1. Render PDF pages through a `PDFHandler`.
2. Recognize page layouts through `OCR.recognize()`.
3. Write OCR page XML and assets under `analysing_path`.
4. Analyze TOC data.
5. Generate chapter XML.
6. Render Markdown or EPUB from chapter XML.

When `analysing_path` is omitted, `EnsureFolder` creates a temporary directory. When it is provided, it becomes a persistent cache and debug output directory.

## Intermediate Artifact Contract

The conversion pipeline expects these paths under `analysing_path`:

- `assets/`: clipped images, formulas, and tables keyed by content hash.
- `ocr/page_*.xml`: OCR page data.
- `ocr/done`: marker indicating all selected pages were recognized.
- `toc.xml`: TOC analysis result.
- `chapters/chapter_*.xml`: generated chapter records.
- `cover.png`: optional first-page cover.
- `plots/`: optional visual debugging output when plot generation is enabled.

Changing XML schemas, file naming, or skip semantics affects multiple modules and should be treated as a cross-pipeline change with focused tests.

## Heavy Runtime Boundaries

`PageExtractorNode` lazily imports `doc-page-extractor` and triggers model loading only when OCR is needed. Keep that lazy behavior unless the task explicitly requires eager loading.

Full OCR conversion may require Poppler, CUDA-capable PyTorch, large model downloads, and substantial VRAM. Ordinary unit tests should remain able to run without those resources.

## Error And Resume Semantics

`ignore_pdf_errors` and `ignore_ocr_errors` may be boolean values or callables. When an ignored page-level error occurs, the pipeline writes fallback page data and continues.

Existing `page_*.xml` files are skipped. The `done` marker skips OCR recognition entirely. Be careful when changing resume behavior because it controls both local manual runs and VGE worktree reruns.
