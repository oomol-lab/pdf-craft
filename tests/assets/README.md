# Test assets

This directory contains checked-in input documents used by the test suite and the
repository-local smoke CLI. It is not included in the published package.

## Layout

- `pdf/`: PDF input fixtures.
- `epub/`: EPUB input fixtures.
- `expected/`: reserved for small checked-in expected outputs; generated conversion
  output belongs in the ignored `pdf-craft-output/` directory instead.

`pdf/friendly.pdf` is also used by `test_pdf_patch_smoke.py` to exercise real PDF
replacement and Qt text-layer output without running OCR. Run it with
`poetry run python test.py test_pdf_patch_smoke`.

When adding a real document, record its provenance and confirm that its license
allows redistribution with the repository. Prefer small, focused fixtures for unit
tests; use complete books only when a format or integration regression needs them.
