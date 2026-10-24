# Issue 834: PDF backend preflight

## Original input

The unchanged upload is committed as `tests/assets/pdf/xref-offset-997ad598.pdf`.
Its size is 2,233,754 bytes and SHA-256 is
`c32eb117290bbca606fada374101f86b741f345abd9720d1c9dea6972f525ea3`.
No repaired/normalized copy is used as the regression input.

The declared previous xref offset is 2050785 while the actual table starts at
2050851. The local pypdf reader fails resolving the page tree. The new entry gate
reports `PDFPreflightError`, backend `pypdf`, stage `page_read`, page unknown,
with the original hex-string exception chained. All permissive ErrorPolicy forms
still terminate before OCR/LLM and content/metadata callbacks.

On 2026-10-24 the new default read-only adapter successfully read 251 pages and
rendered **all 251 pages at 36 DPI**, closing each image immediately (5.625 seconds
in this local run). This is a diagnostic adapter check, not permission for a
business task to continue after the pypdf preflight failure. No OCR or hosted LLM
was called. Local runtime: Poppler 26.08.0, pypdf 6.6.0, Python 3.11.
The local render summary is in `pdf-craft-output/issue-834/original-render.json`;
that generated workspace is deliberately not committed.

## Regression coverage

`tests/test_pdf_preflight.py` uses actual pypdf and Poppler with generated small
PDFs and the original upload. Sixteen test methods cover:

- Original hash, backend divergence, and fail-fast public/advanced entry points.
- Poppler-only metadata/count/size/rendering and explicit publication labels,
  distinguishing absent labels from synthesized physical numbers.
- Different page sizes, offset boxes, clipping to the effective CropBox and all
  four rotation orientations.
- Page count/geometry disagreement, later-page read/render failures, and trial
  write/reopen failures with preserved backend/stage/page information.
- Strict failure despite permissive policy or callbacks, without touching custom
  handlers or business callbacks.
- A valid PDF→Markdown composition through the extraction adapter with deterministic
  extraction output; the extraction engine is substituted while preflight, PCEX
  validation and Markdown rendering run normally.
- Task-local deduplication, rechecking on the next call, source mutation detection,
  cancellation with real subprocess reaping, and exception transport through the
  real process execution domain.

Existing orchestration tests with fictional PDF paths explicitly substitute only
that input boundary via `tests/pdf_preflight_helpers.py`. Real PDF inputs still
run the real checks; the dedicated preflight tests do not use this substitute.
A legacy missing-page test now creates a valid one-page PDF before testing an
out-of-range replacement instead of accidentally supplying a zero-page document.

## Checks

- `.venv/bin/python test.py`: **990 tests passed**.
- Focused existing translation/patching plus initial preflight regressions:
  **81 passed**; final preflight-only suite: **16 passed**.
- Pyright on `pdf_craft tests`: **0 errors**.
- Pylint on `pdf_craft tests`: **10.00/10**, with existing Qt introspection notices.
- `poetry build`: sdist and wheel built successfully.
- No release/version change, no QPDF/pikepdf dependency, no paid model requests.

Poppler's GLib public API supplies publication-label strings not exposed by
`pdfinfo`. Presence of an explicit `/PageLabels` tree is checked during the
already-required pypdf traversal. Linux CI installs `libpoppler-glib8`; installation
and error documentation explain this native prerequisite. Preflight is backend
compatibility evidence, not a complete standards certificate or a promise that
future high-DPI rendering and generated overlay composition cannot fail.
