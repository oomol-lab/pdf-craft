# Retry, recovery and completion

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

A file is not proof of success. `AllFallbackError` (including the existing `NoUsableOCRPagesError` and `NoUsableFillPagesError` specializations) rejects tasks whose primary targets all failed and recovered. Successful metadata cannot mask a wholly untranslated body. Cached results and valid unchanged translations count as successes; genuinely absent targets are not failures. Optional analysis can all recover while the primary extraction succeeds. Cancellation, callback exceptions, unrecoverable service errors, failed recovery and unavailable input/output cannot be ignored. `ErrorCallbackError` preserves the callback exception as its cause.
