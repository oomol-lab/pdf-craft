# Issue 922: unsafe text at document ingestion

PDF Subject metadata containing XML-illegal control characters reached EPUB
`dc:description` through PCEX JSON. The ZIP was valid but its OPF was not.
The same PDF also makes Poppler's native-text XHTML unparseable, which previously
silently disabled optional furniture extraction.

## Implementation boundary

`pdf_craft.common.safe_text()` removes only characters excluded by XML 1.0's
Char production. It does not escape markup, normalize whitespace, or sanitize
HTML/URLs. Legitimate Unicode, including zero-width characters and emoji, stays.
The rule also applies to bibliographic strings that initially travel as JSON.

Text is accepted at these boundaries:

| Input | Acceptance point |
| --- | --- |
| PDF native metadata, including custom handlers | `PDFDocumentMetadata` construction; standard `DocumentMetadata`/`DocumentAuthor` construction |
| Poppler native-text XHTML | Before the first parse in `pdf/furniture.py` |
| Poppler GLib page labels | Before admitting label strings |
| Local/vendor OCR blocks and nested HTML/text | Shared `PageExtractorNode._normalize_text()` |
| LLM output and existing response caches | Runtime response acceptance; metadata JSON evidence validation also handles escaped controls |
| Existing PCEX manifest / translation metadata JSON | Metadata read/acceptance paths, preserving keys, partial overlays and legacy author shapes |
| Explicit caller `BookMeta` | Admission at renderer metadata merge, without mutating the caller object |

There is no final-output filter in `save_xml()` and no new mandatory EPUB
post-generation gate in production. Already parsed internal XML is not rewritten.
PDF content streams, font encodings and image data are not text-filtered.
Invalid XML structure and invalid PCEX schema remain errors; the change does not
attempt arbitrary document repair or change the archive format/dependency pins.

## Automated verification

```sh
poetry run python -m unittest tests.test_safe_text
poetry run pyright --pythonpath .venv/bin/python pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

The text test compares the complete Unicode code-point domain against the XML
Char production, then exercises metadata lists, JSON, XML text/tail/attributes,
OCR HTML, native-text failure recovery, page-label resource cleanup, explicit
metadata, existing PCEX and live/cached model responses. The existing EPUB
renderer integration verifies a retained readable body and parseable OPF.

## Private real-PDF gate (offline, opt-in)

```sh
PDF_CRAFT_SAFE_TEXT_REGRESSION_PDF=/Users/taozeyu/Downloads/pcc-prod-preparation-timeouts-20261030/ce65b9a3-8cae-4c49-8563-5b7114eb490d/original.pdf \
  poetry run python -m unittest tests.test_safe_text_real
```

The source is 676 pages, 48,281,723 bytes. Its SHA-256 is
`f349729c89938bf99937442b03efeede8e51c8d5d6b177ead027fb7acc2086b4`.
The test checks this identity and confirms the unfiltered Subject is unsafe and
the raw first-page `pdftotext -bbox-layout` output fails XML parsing.

It then runs actual first-page PDF rendering/extraction, metadata callbacks,
PCEX persistence and EPUB rendering. Only the OCR network response is simulated
with explicitly marked probe text containing NUL/DC4; the real PDF metadata and
native-text readers and all normalization remain unmocked. It uses a new
temporary workspace, 72 DPI, page selection `{1}`, furniture enabled, no
translation, no CUDA/models, no network and no provider credentials.

On the development machine the raw parsed Subject was 758 characters and the
adopted description 709 characters. Native text retained one line (two words).
The callback and PCEX manifest matched the filtered description. Initial page,
furniture, TOC, page geometry and chapter XML all parsed successfully, as did the
resulting EPUB OPF/NCX/XHTML/XML; the probe body remained present.

This gate demonstrates the source defect and corrected ingestion, not a full
676-page OCR conversion or reader UI acceptance. Customer PDF/EPUB bytes and
extracted metadata are never committed. Without the environment variable, the
test skips; a skipped private gate is not evidence of a real-case pass.
