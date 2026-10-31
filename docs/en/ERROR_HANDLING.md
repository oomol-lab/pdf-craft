# Retry, recovery and completion

## PDF directory and chapter recovery

Printed directory pages are classified before paragraph analysis. Final chapter
anchors and IDs are assigned only after traditional/refined content is adopted;
TOC and chapters consume the same accepted heading set. Removing, demoting or
merging a heading is not a TOC failure and does not require withdrawing refinement.
Old derived TOCs are recomputed, not repaired by retaining stale IDs. Existing PCEX
archives keep their original IDs; these rules apply to new extraction only.

Real structure failures still obey the configured error policy. An approved
page-image recovery changes the adopted content before TOC construction. All-primary-
pages-degraded remains failure. Final validation still rejects inconsistent chapters,
references or resources; the implementation does not suppress those checks.

## XML fill text and formatting

EPUB and PCEX share the friendly XML fill protocol. Text content uses XML escaping:
`<`, `>` and `&` are written as `&lt;`, `&gt;` and `&amp;`. Escaped text is decoded
once, after structural tags are identified; literal prose such as `&lt;span&gt;`
must therefore be sent as `&amp;lt;span&amp;gt;`. XML named and numeric references
are supported; unknown/invalid references remain literal. This is a tolerant fill
protocol, not a general XML parser; CDATA is not a supported text escape.

Required formatting tags and IDs remain present even when a shorter translation
leaves some inline slots empty. Fill must not pad them with source text, invented
placeholders or duplicate translation. PCEX canonical full-text validation and
structural checks remain strict; formula restoration still happens after the
accepted fill loop. Generic XHTML fill does not receive PCEX-only canonical rules.

## Optional input assertions

Conversion does not run an automatic input gate. Invalid inputs fail when the
actual operation encounters the problem; existing ErrorPolicy behavior still
applies to supported page failures. Applications that want to test inputs first
can explicitly call these blocking functions, exported from `pdf_craft`:

| Function | Operation tested |
| --- | --- |
| `assert_pdf_readable(pdf_path)` | Open with Poppler and obtain a positive page count. |
| `assert_pdf_renderable(pdf_path)` | Rasterize every page with Poppler at 72 DPI. |
| `assert_pdf_editable(pdf_path)` | Read, clone, trial-write and reopen with pypdf for basic PDF editing. |
| `assert_epub_readable(epub_path)` | Read the ZIP, container/OPF, navigation, metadata and every spine document through the same parsers as EPUB translation. |

Since 2.6.3, the automatic gate and `PDFPreflightError` have been removed.
Remove old imports and catch clauses for this exception. Applications that need
upfront checks should call explicit assertions and handle the original
backend/parser exceptions.

Choose only the assertions your application needs. Success returns `None`;
failure raises the original backend/parser exception with its traceback. There
is no boolean result or dedicated error taxonomy. Assertions never modify the
input or repair it; temporary trial files are cleaned up on success and failure.
The two Poppler assertions accept an optional keyword `poppler_path=directory`.

```python
from pdf_craft import assert_epub_readable

try:
    assert_epub_readable("input.epub")
except Exception as error:
    print(f"Input assertion failed: {error}")
else:
    print("Input can be parsed")
```

Passing an assertion only confirms the operation listed above. Readability does
not promise complete PDF-to-PCEX extraction; low-resolution rendering does not
guarantee high-resolution rendering or OCR; basic editing does not guarantee
all PDF backfill operations. EPUB readability is compatibility with our input
parsers, not full EPUB standards validation or a promise of successful translation.
No assertion starts OCR, downloads models, or calls an LLM. Conversion and page
count helpers do not call these assertions automatically.


`PDFCraft` and `AsyncPDFCraft` accept the same execution policies. Calls may override the instance defaults without mutating a shared translator.

```python
from pdf_craft import PDFCraft, RetryPolicy, ErrorPolicy

craft = PDFCraft(
    retry_policy=RetryPolicy(translation=3, analysis=3, fill=3),
    error_policy=ErrorPolicy(ignore_analysis_errors=True),
)
```

Retry numbers are **additional attempts after the first**. All default to 3; 0 means one attempt, and negative or non-integer values are invalid. `analysis` governs directory/title hierarchy, metadata JSON, page repair and JEV requests. `fill` governs XML structure repair. `translation` is reserved: there is currently no semantic translation-quality repair loop. LLM transport retries remain separate (`LLM.retry_times`); JEV has no independent retry count. Normal metadata page acquisition does not consume a repair budget.

Pass `retry_policy` or `error_policy` to an operation for an override. An omitted argument inherits the instance policy. Explicit `error_policy=None` or `False` restores strict failure. With no configured policy, errors raise.

`error_policy` accepts `ErrorPolicy | None | bool | ErrorCallback`:

- `False` or `None`: raise after final failure.
- `True`: use all available recoveries.
- A synchronous or asynchronous callback: return **bool** to decide each final failure. Async decisions are awaited. There is no separate `on_error` callback.
- `ErrorPolicy`: decide by category. Unspecified fields are False.

The fields are `ignore_analysis_errors`, `ignore_translation_errors`, `ignore_fill_errors`, `ignore_ocr_errors`, `ignore_pdf_render_errors`, and `ignore_pdf_fill_errors`. The former per-component `max_retries`, JEV `retry_times`, extraction ignore switches, PDF `ignore_errors`, `ignore_translated_error`, and `on_fill_failed` arguments are removed.

```python
async def allow_partial(failure):
    print(failure.operation, failure.stage, failure.error.type, failure.error.message)
    for target in failure.targets:
        print(target.member_path, target.unit_id, target.page_indexes, target.regions)
    print(failure.fallback, failure.affected)
    return failure.category.value == "analysis"

craft = PDFCraft(error_policy=allow_partial)
```

Every decision receives a detached `FailureContext`: task ID, operation, category, stage, serializable `FailureError`, targets, affected locations and proposed fallback. Backend, model, stage index and context locations are supplied when known. Targets are operands of the failed operation, not guessed fault locations; affected locations describe recovery impact. A paragraph has all source regions, including all pages. Regions reuse `SourceRegion` and `PageGeometry`; use `map_bbox` with the actual display image size. A page-render failure may have no geometry. EPUB locations include the archive member, fragment identity, original text and reliable CFI; HTML may have `epubcfi=None`. Metadata fields and reusable furniture templates have identities even without a unique page. These locations do not depend on `on_body` being installed. A service-wide or batch failure identifies its complete scope rather than blaming the first fragment.

Recoveries: analysis uses statistical/native/algorithmic results; translation or XML fill leaves **no accepted translation**; OCR uses page images; failed PDF rendering uses a placeholder when no image exists; PDF fill preserves original pages. PCEX coverage explicitly records preserved content; it is not a translation. Replacement mode displays the source where a translation is missing, and bilingual modes show that source once. EPUB leaves failed source fragments untouched.

A file is not proof of success. `AllFallbackError` (including the existing `NoUsableOCRPagesError` and `NoUsableFillPagesError` specializations) rejects tasks whose primary targets all failed and recovered. Successful metadata cannot mask a wholly untranslated body. Cached results and valid unchanged translations count as successes; genuinely absent targets are not failures. Optional analysis can all recover while the primary extraction succeeds. Cancellation, callback exceptions, unrecoverable service errors, failure of the final recovery and unavailable input/output cannot be ignored. `ErrorCallbackError` preserves the callback exception as its cause.

## Last-resort PDF extraction recovery

Available since 2.6.1.

If chapter analysis or footnote reconstruction has inconsistent structure, extraction
first uses a validated traditional result. If unavailable, the `analysis` policy is
asked about stage `chapter_recovery`, fallback `page_image`. `targets` identify known
failed pages; `affected` includes dependent paragraphs, definitions and references.
Unknown failures conservatively affect the batch. Accepting `algorithm_result` does
not authorize `page_image`. Pages are rendered directly from the source PDF, bypassing
the broken graph. Rendering failures separately ask `pdf_render` for `page_placeholder`.
Rejecting either decision propagates the error.

This also protects extraction without LLM refinement. OCR caches are not rewritten. Content callbacks deliver the adopted result once, after recovery. Partial degradation may complete;
all primary pages degrading after errors still fails. Cancellation, user callback
errors, terminal service errors and final storage/publication errors propagate.

## Overlapping PDF source boxes

Source-box overlap alone does not skip PDF replacement on a page or its
connected paragraphs. The former area-ratio check introduced in 2.6.1 no longer
emits `pdf_fill/source_geometry/original_pdf_page`; accepted translations proceed
through normal layout and composition. Layout obstacles still apply. Erasure
uses source rectangles, so inaccurate boxes can still cover figures or unrelated
text; removing the check does not repair those boxes. Actual validation, layout
and composition failures continue to follow the error policy, and failure of all
primary fill targets still fails the operation.

## Upgrading from 2.5.x

**2.6.0 changes public arguments and default failure behavior. Update existing
callers before upgrading.** A retry budget decides how many attempts an operation
may make; an error policy decides what happens after the final failure. They do
not replace one another.

| Removed argument or hook | Replacement |
| --- | --- |
| `XMLTranslator(..., max_retries=N)` or `translate_epub(..., max_retries=N)` | `retry_policy=RetryPolicy(fill=max(0, N - 1))` preserves the old total-attempt limit. The new default is 3 retries, or 4 total attempts. |
| `FootnoteRefinement(..., max_retries=N)` | Migrate to `Refinement(...)` and set `retry_policy=RetryPolicy(analysis=N)` on the Craft instance or extraction call. |
| `JEV(..., retry_times=N)` | `RetryPolicy(analysis=N)`, shared with the other analysis operations. |
| `ExtractionOptions(ignore_pdf_errors=...)` | `ErrorPolicy(ignore_pdf_render_errors=...)` passed as the execution's `error_policy`. |
| `ExtractionOptions(ignore_ocr_errors=...)` | `ErrorPolicy(ignore_ocr_errors=...)` passed as the execution's `error_policy`. |
| PDF translation/patching `ignore_errors=...` | `error_policy=ErrorPolicy(ignore_pdf_fill_errors=...)`. |
| `XMLTranslator(..., ignore_translated_error=...)` | Set `ErrorPolicy.ignore_translation_errors` and/or `ignore_fill_errors` explicitly. The old argument did not enforce strict XML-fill failure. |
| `on_fill_failed` and `FillFailedEvent` | A decision callback in `error_policy` or `ErrorPolicy.ignore_fill_errors`; it receives `FailureContext` only for a final failure, and must return a bool. |

Analysis failures in directory/title inference and metadata extraction no longer
silently recover by default. Opt into `ignore_analysis_errors=True` to allow
their statistical/native fallbacks, or use a callback for selective recovery.
The same category permits failed JEV evaluations and footnote repairs to retain
the algorithmic result; a JEV service failure is not a low quality score.

Old error predicates received exceptions. New predicates receive a context whose
`error` is a serializable summary, not the original exception. Use
`failure.category`, `failure.error.type`, `failure.error.code` and the source
locations instead of `isinstance(failure, OCRError)`:

```python
from pdf_craft import ErrorPolicy, PDFCraft, RetryPolicy

async def allow_selected_pages(failure):
    pages = {page for target in failure.targets for page in target.page_indexes}
    return bool(pages) and 1 not in pages

craft = PDFCraft(
    retry_policy=RetryPolicy(analysis=3, fill=3),
    error_policy=ErrorPolicy(
        ignore_analysis_errors=True,
        ignore_ocr_errors=allow_selected_pages,
    ),
)
```

`False` raises; `True` performs the proposed recovery. If a callback raises or
does not return a bool, the task fails. A top-level `error_policy=True` allows all
recoverable categories, not cancellation, fatal service errors or total fallback.
Content callbacks such as `on_body` and `on_footnote` keep their existing payloads.
PCEX remains format v4; there is no archive-format migration in this release.

Temporary rate limits do not consume the retry counts above. Executor-owned `RateLimitScope`
coordinates cooling and recovery. Its per-request waiting budget raises `RateLimitTimeoutError`
with the provider cause chain; the corresponding business ErrorPolicy then decides recovery.
See [shared rate-limit scopes](API_REFERENCE.md#shared-rate-limit-scopes).

## Recoverable model formatting

XML fill restores unambiguous whitespace from the known translation before
adoption. Cosmetic formatting alone need not trigger another model request;
ambiguous ownership, changed wording and protected whitespace remain validated.
See [LLM acceptance and whitespace](LLM_ACCEPTANCE.md) for the shared policy,
HTML/CSS references and supported transport subset.


`FailureError.frames` contains serializable filename/function/optional line locations, first for the outer exception and then its cause chain (or unsuppressed implicit context). Cycles stop traversal. It excludes source lines and frame locals; filenames may be absolute.

Vendor OCR HTTP failures expose `FailureError.http_status`, `provider_code`,
`provider_type`, and `request_id` independently of the human message. These values
come from the JSON `error.code`/`error.type` and response headers; absent or malformed
fields remain `None`. `image_url_unsafe` is a provider code, not a pdf-craft enum.
`exception_chain` holds detached exception details with per-cause tracebacks. Human
messages and `causes` may contain private input: consumers must choose a suitable
redaction policy before logging or sending them to analytics. Structured reasons do
not alter fallback decisions, retry classification or document usability checks.


## Exhausted LLM repair evidence

Set the keyword-only `LLM(..., failure_log_path="analysis/llm-failures")` to
retain full interaction evidence only when a protocol repair loop exhausts its
integrity-validation budget. The default `None` disables it. This is independent
of response caching and ordinary `log_dir_path` events, and also works without
`analysing_path`. Both Craft facades share the same implementation.

The exception and `FailureContext.error.failure_log_id` identify
`<failure_log_path>/<failure_log_id>/`. Read `summary.json` for the stage, model,
targets when known, attempt count, final error and `complete` flag;
`attempts.jsonl` contains every repair attempt's actual messages, effective
parameters, response and validation feedback. `transports.jsonl` separately
records transport requests/responses (including transport retries) and available
provider request IDs. The loop ID does not replace a provider's `request_id`.

Successful loops, including repaired ones, remove temporary evidence. A callback
that accepts a fallback still receives the ID and leaves the exhausted loop's
files available for a diagnostics archive. Cancellation and ordinary transport
failure clean temporary records rather than creating a protocol-exhaustion log.
Disk errors issue a `pdf_craft.llm.failure_log` warning and do not replace the
business result; a retained summary may have `complete=false`, or no ID may be
available if publication failed. Records are written incrementally, with only the
current attempt in memory. Keys, Authorization values and credential-bearing URLs
are redacted, but the remaining document text is private: restrict access and
archive/expire these directories according to the host's retention policy.
