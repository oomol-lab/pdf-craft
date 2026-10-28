# Shared whitespace semantics and LLM acceptance (#888)

## Standard and scope

The shared policy follows the CSS Text 3 `white-space` table and document-
whitespace processing, and HTML default rendering for `pre`/`textarea`:

- https://www.w3.org/TR/css-text-3/#white-space-property
- https://www.w3.org/TR/css-text-3/#white-space-processing
- https://html.spec.whatwg.org/multipage/rendering.html

CSS collapses spaces/tabs and collapsible segment breaks across inline boundaries;
this is a presentation transformation, not lossless DOM serialization. Our
transport uses a space for normal segment breaks, one of the UA-defined choices
in CSS Text 3, without CJK language heuristics. Protected modes retain breaks;
NBSP and other Unicode separators are never folded by a Unicode-wide `\s+`.
Full CSS selector/cascade processing, bidi layout and visual line wrapping are
outside the declared subset. See the bilingual `LLM_ACCEPTANCE.md` for exact
inheritance, explicit-style, XML preservation-hint and unsupported-mode behavior.

`transformer/whitespace.py` supplies common context and normalization rules.
Friendly XML uses the existing block/inline registry, indents block-only
containers and does not add inline separators. Segmentation keeps source mode
context. Temporary fill hints transport that context while original attributes
are restored on adoption. XML namespace spelling for `xml:space` is supported
by the friendly encoder/decoder instead of failing on ElementTree's expanded key.

## Acceptance and ambiguity

The earlier tests intentionally demanded a second model request for a trailing
space and for removable spaces at anchor/fragment boundaries. Those assertions
are replaced by **one-request** assertions while retaining their final text,
geometry, image/anchor and PDF replacement checks. These are intentional changes
to the acceptance contract, not deletion of result-fidelity checks.

Source identities and structure are validated first. Whitespace recovery aligns
unchanged non-document-whitespace characters to the known first-stage translation.
Normal paragraph edge padding and uniquely owned gaps are reconstructed without
another request. A gap within one field is unique; a boundary gap uses its
existing owner or a unique source owner. The existing expression protocol keeps
restored padding outside formula content. Missing gaps with ambiguous styling/
source ownership still request correction. All changes are applied only after
recovery succeeds; wording, punctuation and special whitespace changes fail.

The original untranslated text is never used to supply translated words.
The general acceptance constitution is in `references/failure-and-completion.md`
and the English/Chinese user documents: an algorithmically recoverable,
unambiguous response should not cost another LLM request for cosmetic compliance.

## Offline validation

- Six-mode normalization, CRLF, special Unicode spaces, context inheritance,
  block/inline formatting and XML preservation round-trip are covered.
- Real fill-path tests verify one call for recoverable normal whitespace, two
  calls for ambiguous styled allocation and for incorrectly collapsed protected
  inline content, and source style/spacing preservation after adoption.
- Formula, anchor, cross-page source/PDF text and strict punctuation/word checks
  remain enabled. Shared recovery is used for ordinary text and formula gaps.
- Final focused suite: 69 tests passed. Pyright reports zero errors; changed-file
  Pylint scores 10/10. Full-suite results are recorded in the implementation
  handoff and the ignored evidence directory.

## Independent paid implementation run

```sh
PDF_CRAFT_LLM_DEFAULT_PROVIDER=oomol \
.venv/bin/python -m pdf_craft_tool.smoke.citation_footnotes \
  --output pdf-craft-output/issue888-implement-citation-01
```

The fresh run exits 0. `citation-checks.json` reports `automated_passed=true`,
`pipeline_completed=true`, and all three mask checks pass. The three-page English
PDF is generated with **no accepted fallback**. OCR/JEV/LLM resources are real;
response caches/output directories are new. The local CLI uses `oo llm config`
in memory according to the selected skill, without persisting credentials.

All three source/output preview pairs were inspected. The originally reported
large duplicate footnote overlay is absent. Residual folio/reference marks,
cross-page continuation placement and uneven spacing on page 2 remain visible;
this is targeted whitespace-regression acceptance, not a claim of perfect OCR,
translation semantics or full layout quality.

Full model traces, analysis, mask evidence, PCEX, PDF and previews are ignored
artifacts under that run directory. The run's `implementation-evidence.json`
records the current code fingerprint and visual inspection. Review must compare
the implementation against the official standards and independently execute a
new-directory citation gate on the reviewed source; this run cannot substitute
for the reviewer's acceptance.
