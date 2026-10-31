# Unreleased

- Preserve paragraph boundaries between completed quotations and new quoted turns,
  without splitting inline quoted terms or attribution. Give explicit numbered
  entries precedence over comma/hyphen continuation hints. Empty OCR text no
  longer hides the real prose predecessor; retain its source identity and geometry
  as ignored evidence, including footnote candidates and empty-page boundaries.
  Add regressions for both footnote modes and verify citation PDF writeback.

- Align JEV page review with the authoritative incoming paragraph edges instead
  of derived outgoing flags; project ready raw neighborhoods together and retain
  complete layout text and neighboring candidates as review evidence, so
  misclassified notes cannot hide the actual prose predecessor. Clarify both
  footnote-mode rubrics:
  sentence endings alone do not split paragraphs, and standalone endnotes remain
  part of the main reading flow. Keep refinement thresholds caller-configurable.

- Use partial page refinement by default (`risk_threshold=0.45`), with separate
  structural review rubrics for each footnote mode and no concrete test-answer
  examples. This limits LLM calls without promising complete error recall or a
  fixed routing percentage; explicit caller thresholds remain authoritative. Review
  unnumbered note continuations, and stop treating unresolved OCR aliases as
  confirmed cross-page definitions. Explicit caller thresholds are unchanged.
- Bind repaired cross-page note origins during ordered adoption without waiting
  for neighbouring LLM jobs; preserve standalone notes whose printed mark was
  missed and avoid duplicate formula-wrapped labels.
- Keep actual reference marks in streaming PDF content instead of `?` placeholders.
  Explicitly identified non-linear OCR diagrams can retain original artwork.
- Restrict native furniture supplementation to Poppler-visible text, so hidden
  search/OCR layers cannot reappear as translated headers or footnotes.

- Keep deliberately excluded OCR regions occupied during native furniture
  extraction, so page-refinement exclusions cannot be translated and filled
  back through the furniture path. Add both citation refinement modes to the
  paid regression with an adopted-exclusion check and manual visual review.

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
