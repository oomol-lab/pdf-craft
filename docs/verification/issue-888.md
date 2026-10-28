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

## Review recovery: source identity and mixed preservation boundaries

Independent review of `db578a4` found two failures not covered by the initial
suite. Both are reproduced offline with the old restoration function and the
new complete-fill tests (`review-before.log`: one failure and one error):

1. Different no-ID tags could reorder during attribute adoption, but recovery
   previously matched source children by absolute index. A `b` in source `pre`
   mode could therefore be validated as normal and later regain its protected
   source styling around incorrectly collapsed text.
2. An inclusive character range marked a normal tail as protected merely because
   it touched the preceding `pre` element's end. Correct normal tail candidates
   were rejected and moving whitespace into the protected element could pass.

Recovery and `InlineSegment.assign_attributes` now share the same child matcher:
IDs first within each tag, then same-tag occurrence order, retaining target order.
When source context exists but a response node cannot be matched, recovery fails
transactionally instead of falling back to model attributes. Existing structural
validation still owns missing/duplicate IDs and tag counts.

Gap classification now uses the actual text/tail owners and unique source
ownership at boundaries, rather than inclusive element ranges. A normal tail
next to `pre` stays normal: one, two or formatting-newline spaces collapse
without an extra fill call. A known normal gap cannot be moved into a protected
source slot. Interior protected gaps continue to require their preserved text.
This follows CSS Text 3 4.1.1's distinction between collapsible and preserved
whitespace, and the same declared transport subset as above.

New complete-fill regressions check reordered no-ID `b`/`i` nodes: the incorrect
single-space `pre` value is rejected, the corrected double-space value is
accepted, and the adopted `b` retains both the source style and both spaces.
Normal-tail tests assert one fill call and correct final source style/text.
Direct recovery tests additionally assert that unmatched contexts or misplaced
protected whitespace cannot mutate/admit the candidate.

The updated implementation has its own fresh paid run at
`pdf-craft-output/issue888-review-recovery-citation-01/`; it does not reuse the
original implementation's or reviewer's responses. Final results and inspection
are recorded in the implementation handoff and its ignored evidence files.

Review-recovery final verification: **1,187 tests passed**, **73 focused tests
passed**, Pyright **0 errors/0 warnings**, changed-file Pylint **10/10**, and
`git diff --check` clean. The new citation run exits **0** with
`automated_passed=true`, all three mask checks passed, and `failures=[]`.
All three translated previews were compared with the same pinned source fixture:
the large duplicate footnote overlay is absent. Header/folio artifacts,
continuation-label handling and formula presentation retain unrelated imperfections;
no claim of perfect OCR, translation or layout is made.

## Second review recovery: missing protected edge gaps

Review of `f6fe071` found that missing canonical edge whitespace had no existing
source/candidate owner and was therefore treated as normal padding, even inside
`pre`. Recovery now considers possible source fields when no actual owner exists.
A unique protected field restores the exact known canonical gap; multiple possible
fields require correction without mutating the candidate. Actual normal tails
retain their own context, so the previous mixed-boundary fix remains covered.

Direct regressions cover leading/trailing spaces, tabs and breaks in `pre`,
`pre-wrap`, `break-spaces`, and preserved breaks in `pre-line`. Complete fill
regressions cover a protected span inside a normal wrapper: ambiguous missing gaps
retry, the clarified response is accepted, and the final styled span retains the
canonical whitespace. Existing normal-tail tests still require one fill call.

Fresh paid evidence: `pdf-craft-output/issue888-protected-edge-citation-01/`.

Final verification after this recovery: **1,190 tests passed**, Pyright **0 errors /
0 warnings**, changed-file Pylint **10/10**, and clean diff checks. The old committed
function independently reproduces silent newline loss in all four relevant modes
(`implementation-before.log`). The new fresh paid smoke exits **0**, all three
mask checks pass, `automated_passed=true` and `failures=[]`. All six source/output
previews were inspected: no large duplicate footnote overlay; header/folio,
continuation and formula presentation imperfections remain outside this fix.
Logs and the code fingerprint are retained alongside the fresh smoke artifacts.
