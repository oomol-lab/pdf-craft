# Unreleased

- Improve paragraph joining with guarded vertical-column seams, numbered-entry
  boundaries, quotation boundaries and measured first-line indentation. Preserve
  source text, geometry and incremental delivery; uncertain observations keep the
  existing path. Empty OCR text retains its source identity as ignored evidence
  without hiding the actual prose predecessor.
- Skip OCR for strictly white rendered pages and white vendor footnote second-pass
  images without inventing requests or usage. Preserve first-pass content and old
  valid caches; near-white ink continues through OCR.
- Adopt local JEV review for body-only refinement: incoming edges and ownership
  are checked in batches of at most four named questions, with complete raw
  neighborhoods and page-level maximum-risk aggregation. Keep the dedicated
  footnote-enabled rubric and its separate internal working point. Incomplete
  reviews follow the error policy and never count as passing pages.
- Remove `Refinement.risk_threshold`; decisions are fixed internally. Configuration
  after `jev` and `llm` is keyword-only. Existing callers must remove threshold
  arguments and name optional settings such as `max_output_tokens`.
- Bind cross-page note origins during ordered adoption without waiting for
  neighbouring LLM jobs. Preserve unnumbered notes, actual printed reference
  marks and explicitly identified non-linear source diagrams; unresolved aliases
  are not confirmed cross-page definitions.
- Restrict native furniture supplementation to Poppler-visible text and apply
  shared external-text cleaning, preventing hidden OCR text from becoming visible
  furniture and invalid control characters from suppressing valid native text.
- Document structural-review principles, dynamic-repair constraints and final
  verification results, including request equivalence between the frozen local
  protocol and the body-only extraction runtime.

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
