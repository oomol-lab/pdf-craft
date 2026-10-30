# Unreleased

- Preserve first-pass footnote candidates for body-only page refinement, allowing
  wrongly classified body text to be restored and clearly identified footnotes
  in the body to be excluded without an extra OCR pass or citation output.
  Keep raw evidence and source identities, including cache and fallback paths.

- Replace nested `FootnoteOptions` / `FootnoteRefinement` with independent
  `ExtractionOptions.footnotes: bool` and `refinement: Refinement | None`.
  Refinement still requires both JEV and LLM with explicit executors. Body-only
  page refinement reuses the streaming pipeline without extra footnote OCR,
  citations, references or footnote callbacks, including on cached OCR inputs.

- Prevent intersecting neighbouring OCR boxes from being treated as empty
  expansion space during PDF fitting and page-local font normalization.
  Extend the three-page paid regression with an independent written-text
  overlap check; manual source/output inspection remains required.
- Render inline TeX atoms as vector glyph contours with their requested
  optical size and existing ActualText semantics, avoiding full Type1 font
  programs for a few formula glyphs at each fitted size.

- Add process-local `call_id` to request observations so callers can correlate
  retries without conflating independent provider calls or OCR polling operations.

- Add constructor-level `on_request` observations for LLM, JEV and OCR vendors.
  Every transport attempt, including retries and failures, reports timing,
  outcome, available HTTP status and received token usage independently of
  `UsageCollector`. Unlimited OCR control requests are distinguished from model
  submissions. See [request observations](../en/REQUEST_OBSERVATIONS.md).
- Honor ExtGState font objects/sizes during PDF character removal and keep
  Hidden/NoView visual Stamps invisible. Preserve source fonts/graphics and
  resource compression instead of returning to whole-page outlining.
- Clarify the PDF backend boundary: pure reads/rendering use Poppler; editing
  and its necessary font/content-stream reads may use editing helper components.
- Include cause/context traceback locations in recoverable failure diagnostics,
  with cycle protection and without source lines or local variables.
