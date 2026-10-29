# Unreleased

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
