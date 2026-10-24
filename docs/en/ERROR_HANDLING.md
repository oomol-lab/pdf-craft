# Retry, recovery and completion

## Mandatory PDF preflight

Every task accepting a source PDF runs compatibility checks before OCR, translation,
content/metadata callbacks, or final output. Both pypdf and Poppler are required,
even with a custom async PDF handler. The same checks apply to the sync/async
facades, PDFExtractor, PDFTranslationPipeline and PDFPatcher.

Preflight reads every page's structure, resources and decoded content with pypdf
(strict reader mode, without a separate repair pass), renders every page at low DPI with Poppler, compares
page counts and page geometry, and writes/reopens a temporary pypdf document.
The original is never repaired, rewritten, or replaced by that trial document.
Images are released page by page. This adds startup work proportional to the PDF;
nested steps share the result only within the current task. Subsequent calls check
again. A custom handler still supplies the actual business renders after preflight.

A failure raises the public, task-fatal `PDFPreflightError`:

```python
from pdf_craft import PDFCraft, PDFPreflightError

try:
    PDFCraft().patch_pdf_with_extraction("source.pdf", "book.pcex", "output.pdf")
except PDFPreflightError as error:
    print(error.source, error.backend, error.stage, error.page_index, error.reason)
```

Its fields are `source` (input path as a string), `backend` (`pypdf`, `poppler`,
`consistency`), `stage`, `page_index` (1-based or `None`), and `reason`. Stages are
`open`, `page_read`, `page_labels`, `render`, `page_count`, `geometry`, `write`,
`reopen`, and `source_changed`. The original exception is chained when available.
An inaccessible file, unavailable backend, and backend parse/render failure retain
their own diagnostic cause; none is automatically declared a PDF standards violation.

**This exception cannot be ignored.** `error_policy=True` and error callbacks cannot
permit continuation; the policy decision callback is not invoked. No retry policy,
page fallback, or automatic source repair is applied. Cancellation remains cancellation.
Passing preflight is compatibility evidence, not a complete PDF conformance certificate
or a guarantee that later high-DPI rendering, translation, or overlay composition succeeds.


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

This also protects extraction without LLM refinement. OCR caches and previously
delivered previews are not rewritten or replayed. Partial degradation may complete;
all primary pages degrading after errors still fails. Cancellation, user callback
errors, terminal service errors and final storage/publication errors propagate.

## PDF source geometry protection

Available since 2.6.1.

Before erasing text, PCEX PDF rendering checks for text boxes that substantially
cover unrelated source content, including figures and untranslated text. It asks
`pdf_fill` about stage `source_geometry`, fallback `original_pdf_page`. Targets
identify conflicting source bboxes; affected pages include complete cross-page
paragraphs. Accepting retains those actual source PDF pages while other pages
continue. No translated layout preview is emitted for skipped replacements;
existing missing-translation previews can still carry original text with empty
lines. If all primary targets degrade, the task still fails. Rejecting or raising
from the decision callback aborts before output publication.

## Upgrading from 2.5.x

**2.6.0 changes public arguments and default failure behavior. Update existing
callers before upgrading.** A retry budget decides how many attempts an operation
may make; an error policy decides what happens after the final failure. They do
not replace one another.

| Removed argument or hook | Replacement |
| --- | --- |
| `XMLTranslator(..., max_retries=N)` or `translate_epub(..., max_retries=N)` | `retry_policy=RetryPolicy(fill=max(0, N - 1))` preserves the old total-attempt limit. The new default is 3 retries, or 4 total attempts. |
| `FootnoteRefinement(..., max_retries=N)` | `retry_policy=RetryPolicy(analysis=N)` on the Craft instance or extraction call. |
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
