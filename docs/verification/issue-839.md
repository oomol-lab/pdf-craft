# Issue 839: optional input assertions

The automatic dual-backend PDF gate and its `PDFPreflightError` are removed in
2.6.3. PDF operations retain their normal error and ErrorPolicy behavior. The remaining
`pdf/backend.py` scope shares only executable configuration, with no input I/O.

Public blocking assertions return `None` or propagate original exceptions:

- `assert_pdf_readable`: Poppler opens and reads a positive page count.
- `assert_pdf_renderable`: Poppler rasterizes every page at 72 DPI.
- `assert_pdf_editable`: pypdf reads, clones, trial-writes and reopens.
- `assert_epub_readable`: the translation input adapters read navigation,
  metadata and every spine document through a shared lazy chapter reader;
  the assertion exhausts it using a read-only ZIP adapter.

`tests/test_input_assertions.py` uses real small files to cover successful input,
late render/parse errors, missing archive members, unchanged source bytes, and
trial-write cleanup/error propagation. The damaged xref fixture remains readable
by Poppler but fails the editing assertion. Count queries and extraction entry
points are tested with eager rendering/pypdf reads prohibited. Configuration
regressions cover nested readers and the real Qt composition process.

Validation: the final standard runner (`python test.py`) passed 1009 tests.
The earlier full pytest suite passed 1035 tests before the two additional Poppler
diagnostic regressions were restored; the final focused assertion/EPUB suite
passed 20 tests. Pyright passed with no errors or warnings, Pylint passed
(existing PySide6 extension introspection notices only), and Poetry built both
sdist and wheel. No OCR conversion, model download or hosted LLM call was run.
