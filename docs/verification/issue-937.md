# Canonical formula projection (#937)

Source expression IDs allocate text; they do not identify the mathematics in the
first-stage translation. The shared protocol now projects accepted canonical
math into typed PCEX expressions or EPUB MathML before adopting a fill response.
It reuses source presentation only when the mathematical content matches.
Conversion failures consume the existing fill budget. Standalone equations and
the existing pure-prose formula presentation contract remain unchanged.

The PCEX schema remains v5: this changes runtime projection, not persisted fields
or their meaning. MathML generation explicitly declares the already installed
`latex2mathml` dependency; no locked package version changes.

## Offline verification

```sh
poetry run python test.py
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
```

`tests/test_canonical_math_projection.py` checks final decoded content, PDF text
planning, and serialized XHTML/MathML. Cases include the cropped character
paragraph with unequal formula counts, a split equality, formula/text reordering,
repeated values, delimiters crossing allocation fields, significant TeX spaces,
asset captions and nested table cells, and rejection before partial adoption.
Existing structured-translation tests also retain merged source formulas and
reject unauthorized asset formula changes. Fixtures are synthetic and offline.

The vendor and table/formula smoke matrices explicitly use `fill=12`, meaning
one initial attempt plus twelve retries. Dedicated paid smoke entrypoints already
use twelve. Library retry defaults and other budgets are unchanged.

## Targeted provider verification

The original Unlimited page-2 paragraph was replayed from saved PCEX and its
saved first-stage translation, without OCR: 21 source formulas, 22 canonical
references. One real fill request through `new-llm.oomol.com` passed; the final
22 formula values match the canonical sequence, with no dollar-delimited LaTeX
in ordinary strings. The final chapter also passed current-format encode/decode.
An offline replay of that saved response passed with the final implementation.
This is a targeted check, not a rerun of the entire paid matrix.

Private inputs, requests, responses and generated XML are excluded from Git.
Evidence is preserved alongside the original smoke output:

```
/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/pdf-craft-output/paid-full-0615569-5bovzlul/issue-937-verification
```
