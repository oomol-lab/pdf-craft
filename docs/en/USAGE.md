# Task size and resource usage

Since 2.6.5, task size can be queried before submitting a conversion, and remote
resource consumption can be collected independently of progress events.

## Preview task size

```python
from pdf_craft import pdf_pages_count, pcex_words_count, epub_words_count

pages = pdf_pages_count('book.pdf')
words = pcex_words_count('book.pcex', with_anchored=True, with_furniture=False)
epub_words = epub_words_count('book.epub')
```

These blocking, read-only functions do not call models or create a translated
output. PCEX accepts a `.pcex` path or an existing `PDFCraftExtraction` handle.
PCEX counts the source layer and book metadata, including image/table text when
`with_anchored=True` and independent furniture when `with_furniture=True`.
Reused headings are not counted twice. EPUB counts the actual translation source
projection, including navigation, document titles and translatable OPF metadata;
its optional `submit` selects the same projection as translation and defaults to
`SubmitKind.REPLACE`. Use the same value when starting the actual translation.

Both functions reuse translation's pre-scan. With the same input and options,
their result equals operation `STARTED.total_words`. They return an integer word
total, not units. Counting requires parsing the source content; it is not a metadata-only
lookup. Invalid inputs propagate parser/validation errors. There is no DOCX input adapter.
A scanned PDF's translation word count becomes available after extraction to PCEX.

The counting convention is [source words](TRANSLATION_PROGRESS.md#source-words),
not tokens: one Han character per word and a 20-character allowance for other
letter/number runs. Model prompts and context are not additional source words.
Use `await AsyncPDFCraft().pcex_words_count(...)` or `.epub_words_count(...)` in
async applications to run file work outside the event loop. `PDFCraft` also
provides synchronous methods with those names.

## Collect resources for one operation

```python
from pdf_craft import AsyncPDFCraft, UsageCollector

usage = UsageCollector()
try:
    extraction = await craft.extract_pdf(
        'book.pdf', 'book.pcex', options, usage=usage,
    )
finally:
    report = usage.report()
    for provider, consumed in report.items():
        effective = consumed.effective
        wasted = consumed.wasted
        print(effective.input_tokens, wasted.input_tokens)
```

Here `craft` is your configured `AsyncPDFCraft`, and `options` is your
`ExtractionOptions`. Pass `usage=usage` to model-calling Craft operations or
advanced request/transformer methods. Synchronous `PDFCraft` has the same
argument. The operation's original return value is unchanged. Nested OCR, JEV,
LLM translation/fill and analysis calls inherit the collector automatically.
Create a separate collector for each independent task; reusing one deliberately
accumulates its reports.

`report()` returns a detached `UsageReport` mapping indexed by the **original
provider configuration object**, for example `report[llm]`, `report[jev]`, or
`report[ocr_config]`. Equal-valued but distinct objects remain separate; internal
runtimes sharing one configuration accumulate together. No model names, prices,
credentials or request histories are included. Unused providers have no entry.
Changing the snapshot cannot change the collector. Concurrent tasks with shared
provider configurations remain isolated when given distinct collectors.

On return or exception propagation, owned requests have settled. `finally` can
therefore read the final observed consumption even after cancellation, a fatal
provider error, or a callback failure. During execution, a snapshot includes settled
consumption; candidates still awaiting validation are not yet classified.

## Report fields and classification

Every provider uses the same two data classes:

```python
RequestUsage(requests=0, input_tokens=0, cached_input_tokens=0, output_tokens=0)
Usage(effective=RequestUsage(), wasted=RequestUsage())
```

`input_tokens` includes `cached_input_tokens`. Charge ordinary input using their
difference, and charge cached input separately. Cache means the provider's prompt
cache, not PDF Craft's local cache. All missing/unreported token fields remain
zero; no estimates or unknown counters are added. This is observed consumption,
not a supplier invoice. A failed request can incur charges without reporting usage.

- `requests` counts actual remote business attempts, including retries and attempts
  with no reported tokens. Waiting for capacity and local cache hits do not count.
- `effective` counts responses accepted by the corresponding business operation
  after its relevant validation. Normal multi-round translation, fill, and metadata
  reading may each contribute accepted calls.
- `wasted` counts unadopted calls, including transport/empty-response retries,
  rejected structure or completeness, abandoned candidates and cancelled attempts.
  HTTP success alone does not make a response effective.

Add both parts for total known consumption. Effective consumption is not a
promise of theoretical minimum cost: an accepted repair may include extra feedback
context. Output token prices, including free output, are the caller's concern.

### Provider boundaries

LLM records streaming usage when reported, including provider cache-hit input.
Results remain tentative through protocol repair and adapter validation. Responses
rejected by a later relevant validator become wasted, without counting twice.

JEV records its reported input/output tokens; absent cache counts stay zero.
A valid low probability used to choose LLM refinement is effective. A subsequent
repair failure does not undo that decision's resource consumption.

OCR vendor requests mean **recognition submissions**, not PDF pages or all HTTP
calls. DeepSeek OCR/OCR2 can make two accepted recognition calls for one page;
each retry is another attempt. Unlimited OCR counts task submission/resubmission,
excluding authentication, polling and result downloads. Its current adapter does
not report tokens, so those fields stay zero. Recognized layouts must pass OCR
parsing before adoption. Later JEV/LLM refinement does not invalidate adopted OCR.
Local OCR and cached pages create no vendor consumption.

`OCRTokensMetering` remains available from `extract_pdf_with_metering` and one-shot
PDF conversion returns. It retains its existing page-result token summary and
budget behavior. Use `UsageCollector` for provider-separated attempts, discarded
consumption and supplier cache tokens; the two reports are not interchangeable.
