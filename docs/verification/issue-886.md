# table/formula inline whitespace fidelity (#886)

## Root cause

The failed table/formula paragraph has adjacent inline formulas, including
`chi(n) =` followed by `chi'(n)`. Its canonical translation contains a space
between those slots. The XML segment scanner discarded every whitespace-only
node, including the tail that separates two inline elements. Thus a correct
candidate could be reported as missing a character. Conversely, an incorrect
candidate adding a space between slots could pass because the scanner erased
that character before comparison rather than restoring the canonical padding. Attribute reassignment during admission also
silently discarded whitespace-only text/tails.

This is a representation defect, not a reason to increase retries or relax the
canonical invariant. Preserve sentence whitespace in inline content, ignore
block-only indentation, and retain text/tails when assigning source attributes.
Canonical PCEX validation explicitly scans all response text slots, including
whitespace-only slots, because the transport template omits source display
attributes. Final visible text must still exactly equal the canonical value.

The citation transport writer problem (#885) is a separate layer: friendly XML
encoding can insert/remove whitespace before the candidate reaches this scanner.
This change does not duplicate that writer repair. Formula codec round-tripping
(#887) is also separate; formula restoration is unchanged here.

## Offline evidence

`tests/test_fill_inline_whitespace.py` runs real segmentation, canonical fill,
attribute assignment, formula restoration and final adoption. The initial three tests
fail on the original implementation: a correct gap exhausts fill retries, an
extra gap disappears before validation, and generic inline words are joined.
The expanded regression suite also covers deterministic formula padding and
strict rejection of other text changes. Block indentation is still omitted.

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

## Formula slots with no first-stage characters

Recorded diagnostic requests in `pdf-craft-output/issue886-table-recorded/`
show a second cause in the same paragraph. The first-stage source ends in the
unfinished clause `chi_0(n) = 0 when`; its translated result ends at the Chinese
conjunction before that formula. The fill template still correctly requires
`expression#25`. Models either omitted the required ID or appended its source
spelling, so no such candidate could satisfy both structure and canonical text.

The protocol now explicitly requires a retained, empty inline-expression slot
when the canonical translation assigns it no characters. Source formulas are
already restored from their stable identity after acceptance, including empty
slots. This completes the existing two-stage contract rather than inventing
canonical text or accepting missing IDs. An offline regression first rejects
an omitted ID, then accepts the complete template with an empty slot and proves
that both original formulas survive in order.

## Shared transport repair

Reuse #885's committed XML writer and prompt repair (`a77a540`, cherry-picked
as `8392b95`) rather than introducing another encoder implementation. It avoids
adding pretty-print whitespace around mixed content. The initial live diagnostic
captured that problem in other table/formula paragraphs as well.


## Canonical ownership of formula-boundary padding

A later fresh run isolated a correct-words candidate that persistently added
spaces between Chinese punctuation and formula slots. Delegating these padding
choices to another language-model retry is unnecessary: the first-stage text
already specifies them. `canonical_text.py` restores only whitespace at temporary
expression boundaries, and only when every nonspace character exactly matches.
The final existing canonical equality check still runs before admission.

Literal-text spaces (including spaces inside words), outer spaces away from formulas, punctuation,
wording, cross-fragment spaces and all structure/ID constraints remain strict.
Corrections are placed outside formula nodes so restoring original formulas does
not swallow sentence separators. A candidate with any other discrepancy is left
untouched and must be corrected by the normal bounded loop. This is deterministic
reconstruction of the canonical representation, not whitespace-insensitive
acceptance. The recorded notation candidate is retained as the small fixture
`tests/assets/translation/table_formula_padding.json`.

## Final acceptance

- Final real PDF routes: `pdf-craft-output/issue886-accepted-pdf/`.
  Both citation OCR → Markdown and table&formula OCR → Chinese Markdown report
  `status=passed`, with real vendor/LLM calls and unchanged matrix parameters.
- Both EPUB routes (Cambridge and The little prince) passed in
  `issue886-final-vendor/`. The later canonical-padding code is PCEX-only and is
  not called for EPUB. PDF routes in that invocation failed before translation
  on proxy `RemoteProtocolError`/`ConnectError`; those failures remain recorded.
- Cross-check: `issue886-padding-final-citation/citation-checks.json` has
  `automated_passed=true`, all three masks pass, and the refined English PDF
  completed without accepted fallback. Inspected all three output previews:
  no duplicated footnote overlay was seen.
- Aggregated local evidence is `pdf-craft-output/issue886-acceptance/summary.json`.
  Earlier failed and diagnostic runs remain intact, including the recorded
  protocol response that motivated explicit empty slots and the final-formula
  padding edge case. These were implementation investigations, not replacement
  acceptance results or a collection of nonconsecutive gate successes.
- Pyright has no errors and Pylint scores 10/10. Full test verification is
  recorded in the implementation handoff. One earlier full run hit the known
  title-scheduler timing assertion; isolated and subsequent full runs passed
  without any scheduling changes.

Reviewer acceptance must independently execute the normal vendor matrix on the
reviewed source, using new response caches/output directories. All generated
outputs and model request/response diagnostics remain ignored and uncommitted.

Final local verification: **1,164 tests passed**, Pyright **0 errors**, Pylint
**10/10**, and `git diff --check` clean. The final formula-boundary edge cases
include an empty last expression and canonical leading/trailing padding around
an expression; padding stays outside the node that formula restoration replaces.

A spot inspection of the resulting Markdown confirms the formerly failing
paragraph completes with its source formulas present. A repeated explanatory
clause near a cross-fragment boundary remains a translation/context-quality
observation; this fix deliberately does not rewrite model wording or claim
semantic perfection of the generated translation.
