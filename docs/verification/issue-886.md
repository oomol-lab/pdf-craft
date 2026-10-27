# table/formula inline whitespace fidelity (#886)

## Root cause

The failed table/formula paragraph has adjacent inline formulas, including
`chi(n) =` followed by `chi'(n)`. Its canonical translation contains a space
between those slots. The XML segment scanner discarded every whitespace-only
node, including the tail that separates two inline elements. Thus a correct
candidate could be reported as missing a character. Conversely, an incorrect
candidate adding a space between slots could pass because the scanner erased
that character before comparison. Attribute reassignment during admission also
silently discarded whitespace-only text/tails.

This is a representation defect, not a reason to increase retries or relax the
canonical invariant. Preserve sentence whitespace in inline content, ignore
block-only indentation, and retain text/tails when assigning source attributes.
Canonical PCEX validation explicitly scans all response text slots, including
whitespace-only slots, because the transport template omits source display
attributes. Extra spaces still fail exact normalized-text comparison.

The citation transport writer problem (#885) is a separate layer: friendly XML
encoding can insert/remove whitespace before the candidate reaches this scanner.
This change does not duplicate that writer repair. Formula codec round-tripping
(#887) is also separate; formula restoration is unchanged here.

## Offline evidence

`tests/test_fill_inline_whitespace.py` runs real segmentation, canonical fill,
attribute assignment, formula restoration and final adoption. All three tests
fail on the original implementation: a correct gap exhausts fill retries, an
extra gap is wrongly accepted on the first attempt, and generic inline words
are joined. They pass after the fix. Block indentation is still omitted.

No original PDF, canonical translation, retry budget or structure/ID validation
is changed. Original formula contents are still restored only after acceptance.

## Paid verification

Run the existing matrix from the worktree with a new output directory:

```sh
PDF_CRAFT_LLM_DEFAULT_PROVIDER=oomol \
PDF_CRAFT_LLM_TRANSLATION_PROFILE=default \
PDF_CRAFT_LLM_FILL_PROFILE=default \
.venv/bin/python -m pdf_craft_tool smoke matrix \
  --config tests/smoke/vendor_real.json \
  --output-root pdf-craft-output/issue886-vendor-01
```

Inspect every `checks.json`, especially the table&formula Markdown run. This
calls real OCR/LLM resources and must not be replaced by cached response replay.
See `pdf_craft_tool/README.md` for runtime configuration and artifact locations.
