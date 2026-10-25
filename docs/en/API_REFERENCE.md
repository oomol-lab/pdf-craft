# Public API reference

## Optional input assertions

`assert_pdf_readable`, `assert_pdf_renderable`, `assert_pdf_editable` and
`assert_epub_readable` are blocking functions exported from `pdf_craft`. They
return `None` on success and raise original exceptions on failure. They are
never called automatically by conversion or `pdf_pages_count`.
See [operations, limits and usage](ERROR_HANDLING.md#optional-input-assertions).

`pdf_pages_count(source, pdf_handler=DefaultPDFHandler(poppler_path=...))` reads
only the count through that handler. Nested default readers share the configured
Poppler installation; custom handlers need no extra configuration attributes.

This reference covers the supported library surface imported from `pdf_craft`. Most applications only need `PDFCraft`, one OCR configuration, and—when translating—an `LLM`. The lower-level rendering, transformation, and PDF patching classes are available for applications that need explicit control.

```python
from pdf_craft import AsyncPDFCraft, PDFCraft, PDFOptions
```

`PDFPreflightError` remains exported from `pdf_craft` and `pdf_craft.error` for
compatibility with 2.6.2, including its constructor and fields. Conversion and
assertions no longer raise it; assertions propagate original backend errors.

## Async and synchronous façades

`AsyncPDFCraft` is the primary integration surface for servers, notebooks, and
other asyncio applications. It exposes async counterparts of every `PDFCraft`
workflow: extraction, rendering, PCEX translation, PDF patching, EPUB
translation, and the two one-shot conversions. OCR, PDF/ZIP/image processing,
Qt layout, and other synchronous third-party libraries run in bounded execution
domains so they do not block the caller's event loop. Translation and LLM
network concurrency are native asyncio operations.

```python
craft = AsyncPDFCraft(pdf=PDFOptions(ocr=your_ocr_config))
extraction = await craft.extract_pdf("input.pdf", "book.pcex")
await craft.render_markdown(extraction, "book.md")
```

OCR and translation event callbacks may be synchronous functions or async
functions. They execute on the event-loop thread and async callbacks are
awaited. Cancelling an async task cancels native network/subprocess work and
signals cooperative blocking stages through their abort callback. The await
does not finish cancelling until that worker has unwound, so temporary
workspaces remain valid through its final writes and cleanup.
This applies to every thread-pooled boundary used for synchronous third-party
libraries, not to user extension protocols.
Async PCEX export writes a sibling temporary archive and publishes it with one
atomic replace. Cancellation before that boundary waits for the writer, removes
the temporary archive, and leaves the requested target absent; cancellation
after publication is treated as arriving after successful completion.
External tools such as `pdftotext`, Ghostscript, and LaTeX run in tracked
POSIX process groups or Windows Job Objects. Cancellation or an internal
timeout terminates the command and its descendants; successful and failed
commands also reap descendants that outlive their parent. Qt-worker
cancellation performs this cleanup before the worker itself exits.

Independent image/table translation uses the async-only
`AnchoredContentTransformer` protocol with `async def transform_assets(assets)`.
`PDFOptions.pdf_handler` accepts only `AsyncPDFHandler`, whose
`open()` returns an `AsyncPDFDocument` with awaitable `pages_count()`,
`metadata()`, `page_size()`, `render_page()`, and `close()` methods. The SDK
awaits and materializes source pages on the caller loop before entering
the synchronous OCR or Qt boundary. Only the resulting paths and metadata
cross into those execution domains.

See [retry and recovery policies](ERROR_HANDLING.md) for failure decisions and retry budgets.

`PDFCraft` provides a synchronous facade over the async implementation for
scripts. It must not be called
from a thread that already has a running event loop; doing so raises a clear
`RuntimeError` instead of nesting an event loop. Use `AsyncPDFCraft` there.

Choose the façade once, then use the same method names in that mode.
`PDFCraftExtraction` is an opaque handle: open and export it through
`PDFCraft.open_extraction()` / `export_extraction()` or their awaitable
`AsyncPDFCraft` counterparts, then pass it back to façade methods. Advanced
components are async-only and use unsuffixed methods:
`PDFExtractor.extract`, `MarkdownRenderer.render`, `EpubRenderer.render`, and
the transformer APIs. Existing-EPUB translation is likewise a façade method.
Model preloading uses the async `await predownload_models(...)` function; synchronous callers use `PDFCraft().predownload_models(...)`.

## `PDFCraft`

`PDFCraft` is the public façade for PDF extraction, document rendering, PDF patching, and EPUB translation. Creating it does not initialize OCR, so EPUB-only applications can use `PDFCraft()` with no PDF configuration.

```python
craft = PDFCraft(pdf=PDFOptions(ocr=your_ocr_config))
```

Both façades also accept `retry_policy: RetryPolicy | None = None` and
`error_policy: ErrorPolicy | None | bool | ErrorCallback = None`. Extraction,
translation, rendering and patching methods accept per-call overrides; omitted
error policies inherit the instance default, while explicit `None` or `False`
restores strict failure. These are execution arguments, not `ExtractionOptions`
fields. See [policies and the 2.6.0 migration guide](ERROR_HANDLING.md).

### PDF extraction and rendering

Both facades expose explicitly named parameters. Signatures below abbreviate
some options with `...`.

| Method | Signature and purpose |
| --- | --- |
| `open_extraction` | `open_extraction(path) -> PDFCraftExtraction` opens and validates a `.pcex` as an opaque handle. |
| `export_extraction` | `export_extraction(extraction, path) -> PDFCraftExtraction` writes a handle to a new `.pcex`. |
| `read_pages` | `read_pages(extraction) -> dict[int, PageGeometry]` reads source page geometry. |
| `read_asset` | `read_asset(extraction, asset_hash) -> bytes` reads an extraction resource. |
| `extract_pdf` | `extract_pdf(source, extraction_path, options=None, *, analysing_path=None) -> PDFCraftExtraction` extracts a PDF into a persistent `.pcex` archive. |
| `extract_pdf_with_metering` | `extract_pdf_with_metering(source, extraction_path, options=None, *, analysing_path=None) -> tuple[PDFCraftExtraction, OCRTokensMetering]` is the same extraction with OCR token accounting. |
| `render_markdown` | `render_markdown(extraction, output, assets_path=None, *, mode=RenderMode.SOURCE, translation_id=None, aborted=..., translator=None, window=1, with_anchored=True, on_body=None, on_footnote=None, on_metadata=None)` writes Markdown and optional assets from a `PDFCraftExtraction` or `.pcex` path. |
| `render_epub` | `render_epub(extraction, output, *, book_meta=None, label_language="en", table_render=..., latex_render=..., inline_latex=True, mode=RenderMode.SOURCE, translation_id=None, aborted=..., translator=None, window=1, with_anchored=True, on_body=None, on_footnote=None, on_metadata=None)` writes an EPUB. Metadata defaults to the selected content version; label_language only selects fixed labels. |
| `convert_pdf_to_markdown` | `convert_pdf_to_markdown(source, output, *, ..., translator=None, submit=SubmitKind.REPLACE, window=1, with_anchored=True, on_translation_event=None, on_metadata=None) -> OCRTokensMetering` is the one-shot PDF-to-Markdown workflow. |
| `convert_pdf_to_epub` | `convert_pdf_to_epub(source, output, *, ..., translator=None, submit=SubmitKind.REPLACE, window=1, with_anchored=True, on_translation_event=None, on_metadata=None) -> OCRTokensMetering` is the one-shot PDF-to-EPUB workflow. |

The two `convert_pdf_to_*` methods use a directory-backed extraction inside their analysis workspace, avoiding a ZIP round trip. Give `analysing_path` to retain diagnostics and `extraction_path` to additionally export a `.pcex`. This source PCEX is published after extraction; later translation or rendering failures do not roll it back. `render_epub` accepts `epub_generator.BookMeta`, `TableRender`, and `LaTeXRender` values for output customization.

### Extraction translation and PDF patching

| Method | Signature and purpose |
| --- | --- |
| `translate_extraction` | `translate_extraction(extraction, output_path, translator, *, window=1, with_anchored=True, with_furniture=False, translation_id=None, target_language=None, on_translation_event=None, on_body=None, on_footnote=None, on_metadata=None) -> PDFCraftExtraction` adds one independently identified translation while retaining the source. |
| `list_translations` | `list_translations(extraction) -> tuple[TranslationInfo, ...]` returns each translation's file-local ID, target language, and creation time. |
| `resolve_translation` | `resolve_translation(extraction, translation_id=None) -> TranslationInfo` resolves an explicit translation ID or the stable default. |
| `translate_anchored_contents` | `translate_anchored_contents(extraction, output_path, transformer) -> PDFCraftExtraction` is the lower-level image/table text transformation entry. |
| `translate_pdf` | `translate_pdf(source, extraction, output, translator, *, window=1, with_furniture=False, on_translation_event=None, error_policy=None, on_body=None, on_footnote=None, on_metadata=None)` translates PDF-writable narrative and optional furniture, then patches the result onto the source PDF. |
| `patch_pdf_with_extraction` | `patch_pdf_with_extraction(source, extraction, output, *, translation_id=None, error_policy=None, translator=None, window=1, on_body=None, on_footnote=None, with_furniture=False, on_translation_event=None, on_metadata=None)` patches existing content or an online translation without repeating OCR. LLM calls run only when `translator` is provided. |
| `translate_epub` | `translate_epub(source, output, *, target_language, submit, ..., window=1, on_body=None, on_metadata=None)` translates an existing EPUB. See [EPUB translation](EPUB_TRANSLATION.md) for its options. |

`translate_extraction()` accepts an `XMLTranslator` directly. Narrative and metadata translation always run through `NarrativeXMLTransformer`; `with_anchored` and `with_furniture` only enable or skip their corresponding standard stages. A caller-supplied opaque `translation_id` identifies the result, or pdf-craft generates a short one. The language may be supplied explicitly or declared by the translator. Duplicate IDs are rejected before translation. The stored version contains replacement text; replacement versus bilingual presentation is selected later when rendering.

`RenderMode.SOURCE` renders the original content and does not select a translation. `RenderMode.REPLACE` renders one translation with source fallback for untranslated units. `RenderMode.BILINGUAL` renders source followed by a translated body paragraph. `RenderMode.BILINGUAL_TEXT` appends translated content within each corresponding source fragment in the same text flow. All translation render modes accept an explicit file-local `translation_id`; omitting it deterministically selects the first translation. They fail when the PCEX has no translations, and an explicit missing or malformed ID is never replaced by the default. Translated metadata overrides matching source fields while missing fields retain their source values. Bilingual titles, descriptions, headings, TOC titles, and translated image/table fields include both forms.

Set `error_policy=ErrorPolicy(ignore_pdf_fill_errors=True)` to preserve original
PDF pages when writeback fails and continue with later pages. A predicate in that
field receives `FailureContext`, including structured errors and affected
locations. Synchronous and asynchronous predicates remain in the caller process,
so local functions and lambdas need not be pickleable; asynchronous decisions are
awaited on the caller's event loop. A top-level `error_policy=True` or callback
also controls other recoverable categories, including translation. The default
is strict failure. If every primary fill target falls back,
`NoUsableFillPagesError` prevents successful publication. Unusable input/output,
fatal service errors and callback errors cannot be ignored. See
[error handling](ERROR_HANDLING.md).

## `PDFCraftExtraction` and `.pcex`

`PDFCraftExtraction` is pdf-craft's structured, source-mapped intermediate format.
It is more direct than Markdown or EPUB and retains each recognized block's PDF page
and pixel-space bounding box. Public persistence and exchange always use a `.pcex`
file, which is a validated ZIP archive with this layout:

```text
manifest.json
pages.xml
chapters/chapter_head.xml
chapters/chapter_*.xml
assets/
toc.xml        # optional
cover.png      # optional
furnitures.xml  # optional
translation.xml # optional
```

`manifest.json` contains the format version, producer, creation time, and document
metadata. `pages.xml` defines the one-based OCR-pixel coordinate space, extraction
DPI, and actual pixel width and height of every extracted page. Chapter XML retains
page and bounding-box source mappings. Analysis caches such as OCR responses and
plots are deliberately excluded.

Loading validates the archive version, member paths, required files, XML roots,
page references, bounding boxes, and referenced assets. Unsupported, malformed,
corrupt, or path-unsafe archives are rejected. Back-end operations only consume the
extraction; they do not fall back to an analysis/OCR directory.

## PDF configuration

### `PDFOptions`

`PDFOptions(ocr=None, pdf_handler=None, models_cache_path=None, local_only=False, ocr_executor=None, ocr_image_url_resolver=None)` holds infrastructure that is reused across PDF extractions.

- `ocr`: one of the local or vendor OCR configuration objects below.
- `ocr_executor`: required shared capacity for vendor OCR requests. Local OCR does not use it.
- `ocr_image_url_resolver`: optional async `OCRImageURLResolver` used to turn each rendered Vendor OCR image path into an HTTP(S) URL or Base64 image Data URL. The default reads the image as a Base64 Data URL.
- `pdf_handler`: an optional `AsyncPDFHandler` implementation. User-provided executable extensions are async-only; the built-in synchronous PDF adapter remains internal to the OCR and patching execution domains.
- `models_cache_path` and `local_only`: convenience settings for the default local DeepSeek OCR configuration when `ocr` is not supplied. They must not be combined with an explicit `ocr` configuration.

### `ExtractionOptions`

`ExtractionOptions` controls one extraction operation:

| Field | Default | Meaning |
| --- | --- | --- |
| `page_indexes` | `None` | A container of one-based page numbers to process. |
| `ocr_size` | `"gundam"` | OCR preset; valid values depend on the backend. |
| `dpi` | `None` | Page-rendering DPI; the underlying default is used when omitted. |
| `max_page_image_file_size` | `None` | Maximum rendered-image size per page. |
| `max_ocr_tokens` | `None` | Cumulative OCR input-plus-output token budget. |
| `max_ocr_output_tokens` | `None` | Cumulative OCR output-token budget. |
| `includes_cover` | `False` | Retain a recognized cover image. |
| `includes_furniture` | `True` | Include native page furniture in the extracted PCEX as `furnitures.xml`; it does not translate it. |
| `extract_book_metadata` | `False` | Extract bibliographic metadata from the first OCR pages. It is persisted in PCEX and, when that PCEX is patched to PDF, corrects the output PDF's document metadata. |
| `metadata_llm` | `None` | Required LLM for `extract_book_metadata=True`; it is independent of `toc_llm`. |
| `generate_plot` | `False` | Generate plot diagnostics in the analysis workspace (not in `.pcex`). |
| `toc_assumed` | `False` | Treat the document as already having usable TOC information. |
| `toc_llm` | `None` | LLM used when TOC analysis is needed. |
| `footnotes` | `None` | `None` disables footnotes; `FootnoteOptions()` uses the traditional algorithm; adding `FootnoteRefinement` enables JEV and LLM refinement. |
| `aborted` | a callback returning `False` | A callback checked during processing to request cancellation. |
| `on_ocr_event` | no-op callback | Receives per-page `OCREvent` updates. |
| `on_body` / `on_footnote` | `None` | Receive independent PCEX XML snapshots during extraction. |
| `on_metadata` | `None` | Receives one finalized `MetadataContent`, including an empty view when appropriate. |
| `context` | `None` | Caller-owned `ExtractionContext` for page geometry and active resource reads. |

Footnote extraction has an optional refinement tier with explicit credentials:

```python
from pdf_craft import (
    ExtractionOptions,
    FootnoteOptions,
    FootnoteRefinement,
    JEV,
    LLM,
    ConcurrentExecutor,
    FixedCapacity,
)

executor = ConcurrentExecutor(FixedCapacity(4))

options = ExtractionOptions(
    footnotes=FootnoteOptions(
        refinement=FootnoteRefinement(
            jev=JEV(key="...", model="jev-latest"),
            llm=LLM("...", "https://example.com/v1", "model", "o200k_base"),
            executor=executor,
        ),
    ),
)
```

OCR and traditional footnote resolution still run first. JEV only selects pages for the LLM;
the repaired page must pass the deterministic schema and integrity checks before chapter
`FlowItem` assembly continues. Use `FootnoteOptions()` without `refinement` for the
algorithm-only tier. Use `jev_executor` and `llm_executor` instead of the common `executor`
when those services have separate provider quotas. AI refinement requires explicit executors;
there is no per-service numeric concurrency fallback.

Book-metadata extraction is deliberately opt-in. When enabled, PDF Craft lets a dedicated LLM
read the first three raw OCR pages and request further front pages in batches, up to twelve pages.
Only values with OCR evidence are accepted. PDF file metadata is used only to fill fields that OCR
did not provide; it never replaces an OCR value. The resulting PCEX metadata is consumed by EPUB
rendering and by PDF patching, which writes it to the output PDF's document-information dictionary.
If the metadata dialogue cannot be validated after its bounded repair loop, the default is to raise.
Recovery must be allowed by ErrorPolicy (for example ignore_analysis_errors=True); it then uses
the PDF-file metadata fallback and continues.

### OCR configurations

All OCR configuration objects are immutable dataclasses and are passed to `PDFOptions(ocr=...)`.

| Class | Required fields | Optional fields |
| --- | --- | --- |
| `DeepSeekOCRLocalConfig` | none | `models_cache_path`, `local_only`, `enable_devices_numbers` |
| `DeepSeekOCR2LocalConfig` | none | `models_cache_path`, `local_only`, `enable_devices_numbers` |
| `UnlimitedOCRLocalConfig` | none | `models_cache_path`, `local_only`, `enable_devices_numbers` |
| `DeepSeekOCRVendorConfig` | `base_url`, `api_key`, `model` | `temperature`, `top_p`, `max_tokens=8000`, `timeout_seconds=180` |
| `DeepSeekOCR2VendorConfig` | `base_url`, `api_key`, `model` | `temperature`, `top_p`, `max_tokens=8000`, `timeout_seconds=180` |
| `UnlimitedOCRVendorConfig` | `ak`, `sk` | `base_url="https://aip.baidubce.com"`, `poll_interval_seconds=2.0`, `timeout_seconds=180` |

See [OCR backends](OCR_BACKENDS.md) for model origin, runtime requirements, and selection guidance.

## Transformations and submission modes

`SubmitKind` determines how translated text is incorporated by one-shot
Markdown/EPUB conversion and EPUB translation:

- `SubmitKind.REPLACE`: replace source text.
- `SubmitKind.APPEND_TEXT`: append translated text to the same text flow.
- `SubmitKind.APPEND_BLOCK`: append translated content as a separate block.

Stored PCEX translations and PDF patching do not accept a submission mode. A
stored translation contains replacement text, while Markdown and EPUB rendering
choose replacement or bilingual presentation with `RenderMode`.

One-shot PDF conversion uses the same replacement-only translation layers as
`translate_extraction()`. `REPLACE` selects replacement rendering, `APPEND_TEXT`
selects `BILINGUAL_TEXT`, and `APPEND_BLOCK` selects `BILINGUAL`. The newly created
translation is selected explicitly even when older layers exist. Source and
translation fragments retain matching identities and geometry in their separate
layers; bilingual display views are temporary and are not stored as PCEX chapters.
Block presentation groups a body paragraph's source fragments before its translated
paragraph. Headings and TOC titles remain one combined title; images and tables
appear once, with translated labels and table-cell text composed in place. Footnote
definitions follow the selected text/block style and repeated reference links point
to one definition. `extraction_path` continues to export the original extraction.
EPUB generation uses `label_language="en"` for fixed labels such as Cover, Table
of Contents, Preface and Unnamed. Exact `zh` and `en` select the available label
catalogs; every other string falls back to English. Labels are never inferred
from the source or translator target, and this option neither limits nor changes
body text, translation targets, PCEX language metadata, or callback payloads.
In 2.6.2, rename the old keyword `lan` to `label_language` in `render_epub()`,
`convert_pdf_to_epub()` and `EpubRenderer.render()`; the old keyword is no longer
accepted. Defaults previously used the source language or Chinese; labels now
use English unless explicitly selected. Existing-EPUB translation submission
semantics are unchanged.

For existing calls, replace the keyword at these three entry points (the
`EpubRenderer` call runs inside an async function):

```python
# Before 2.6.2
craft.render_epub(extraction, output, lan="zh")
craft.convert_pdf_to_epub(source, output, lan="zh")
await EpubRenderer().render(extraction, output, lan="zh")

# From 2.6.2
craft.render_epub(extraction, output, label_language="zh")
craft.convert_pdf_to_epub(source, output, label_language="zh")
await EpubRenderer().render(extraction, output, label_language="zh")
```

Both `PDFCraft` and `AsyncPDFCraft` use the new keyword; await calls on the async
facade. If you did not pass `lan`, no keyword migration is needed, but the fixed
labels now default to English. Pass `label_language="zh"` to retain Chinese
labels. Translation settings such as `target_language="ko"` remain unchanged;
`label_language` does not replace `target_language`.

The pinned EPUB generator still hard-codes some output OPF/XHTML language markers
as `zh`; this separate limitation is not fixed by label selection. Do not use
`label_language` as a content-language metadata setting.

The following classes are exposed for applications that need custom structured transformations:

See [retry and recovery policies](ERROR_HANDLING.md) for failure decisions and retry budgets.

Translation workflows also accept `on_translation_event`, a callback receiving
`TranslationEvent` values. `TranslationEventKind` reports `START`, `ITEM_START`,
`ITEM_COMPLETE`, `PROGRESS`, and `COMPLETE`; `TranslationItemKind` identifies TOC,
metadata, or chapter items. Character counts are source-text character counts and
are not token counts or percentages. Item events include the current item's
completed and total source characters, while scope events include the aggregate
counts. Whole-book totals and chapter counts can be `None` while streaming input is
still arriving. The same event callback is available for
EPUB translation, extraction translation, PDF translation, and PDF conversion.

## `LLM`

`LLM` configures an OpenAI-compatible Chat Completions client used for text translation and TOC analysis:

```python
LLM(
    key,
    url,
    model,
    token_encoding,
    timeout=None,
    top_p=None,
    temperature=None,
    retry_times=5,
    retry_interval_seconds=6.0,
    cache_path=None,
    log_dir_path=None,
    total_timeout=360.0,
    cleanup_timeout=5.0,
)
```

`timeout` keeps its HTTP connect/read/write/pool timeout meaning; it does not bound a
whole streaming response. `total_timeout` bounds one LLM operation across capacity
waiting, streaming, transport retries and retry backoff. TOC JSON/schema/business
repair attempts share that same budget. The default is 360 seconds even when
`timeout=None`; only an explicit `total_timeout=None` disables the total budget.
These keyword-only options also apply to translation requests. Since PR #506 used
`timeout` for the combined budget, callers relying on that unreleased behavior
should now set `total_timeout` explicitly.

On completion, timeout or cancellation, stream close and client exit each receive
half of `cleanup_timeout` (5 seconds total by default). This is an additional
cleanup allowance after the operation deadline, not a strict wall-clock bound on
the entire PDF conversion. Cleanup timeout emits `cleanup-timeout`; cancellation
propagates and cleanup is awaited without detached background tasks. These bounds
require an event loop and transports that cooperate with cancellation. Arbitrary
extensions that block the event loop or swallow cancellation require a host-owned
process watchdog for hard termination; the library cannot forcibly kill them.
The host still owns service deadlines and failed-task state transitions.

Without `log_dir_path`, structured request/cache/retry/timeout/failure/cancellation
and success events propagate to the `pdf_craft.llm` parent logger; install a handler
there (or on the root logger). With a directory, events go to request log files.
A TOC LLM failure falls back to statistical analysis; caller cancellation propagates
instead. EPUB output alone never enables LLM requests: supply `toc_llm` explicitly.


`key`, `url`, `model`, and `token_encoding` are required. `temperature` and `top_p` may be numbers or ranges used while retrying. Successful requests can be reused through `cache_path`; `log_dir_path` records request and cache events. OCR credentials do not configure this object.

## Shared asynchronous capacity

`ConcurrentExecutor(FixedCapacity(n))` provides one event-loop-bound capacity channel for
remote operations. Pass the same executor to components that share concurrency capacity, or share a
`RateLimitScope` across separate executors to coordinate a provider quota. `run()` and `map()` share that capacity. `map()` consumes a cold
iterable of `Callable[[int], Awaitable[tuple[int, T]]]` lazily and yields `OperationResult` values
in completion order; callers use `operation_id` to associate results with their inputs. A custom
executor's `map()` returns `AsyncResultIterator`, including `aclose()`, so a caller can cancel and
settle pending operations before releasing the transport or temporary resources.

`CapacityProvider` and `CapacityLease` are public protocols for adaptive policies. A custom
provider may wait in `acquire()`, learn from the `ExecutionReport` passed to `release()`, and wake
its own waiters when capacity becomes available. It must also implement `close(error)` so a
`NonContinuableError` wakes blocked acquisitions. Ordinary
`OperationError` values are returned per operation by `map()`; a `NonContinuableError` closes the shared
executor and cancels its outstanding work. Non-rate provider retry loops remain outside the executor; rate-limit recovery is handled inside it.
Every real attempt reacquires capacity.

### Shared rate-limit scopes

`RateLimitScope(cooldown_seconds=1.0, max_wait_seconds=600.0)` coordinates temporary limits
for an account, API key, or model quota. Both arguments are optional. An executor with no explicit
`rate_limit_scope` creates its own isolated scope. Sharing a scope does not share executor
capacity, terminal errors, or cancellation boundaries.

```python
from pdf_craft import ConcurrentExecutor, FixedCapacity, RateLimitScope

limits = RateLimitScope()
ocr_executor = ConcurrentExecutor(FixedCapacity(4), rate_limit_scope=limits)
translation_executor = ConcurrentExecutor(FixedCapacity(8), rate_limit_scope=limits)
```

Normal requests pass without a rate cap. Provider adapters normalize temporary refusal into
`RateLimitedError`; the scope does not interpret HTTP codes. After a limit, all participating
executors cool down. Only one probe is admitted across the entire scope. On success, recovery
starts at one in-flight request, with starts spaced at least one second apart. Each level needs
consecutive successes equal to its in-flight limit before increasing 1, 2, 4, 8… toward normal
capacity. Requests already in flight are not cancelled, and old successes cannot thaw a newer
cooldown. A cancelled or failed probe releases its slot without counting as recovery success.

- `cooldown_seconds` supplies the fallback delay, doubled on repeated limits with a local
  60-second cap. Adapters normalize numeric and HTTP-date `Retry-After`. The base delay
  (capped at 60 seconds) is a minimum; longer provider instructions are never truncated.
  Jitter only delays admission; concurrent reports never shorten an existing cooldown.
- `max_wait_seconds` is a **per-logical-request** cumulative limit on cooldown/probe/recovery
  waiting. Ordinary capacity queues and execution time do not count. LLM transport retries
  and Unlimited OCR HTTP stages for the same page retain that budget. Existing overall request
  deadlines still apply. Requests sharing a scope do not consume each other's budgets.
- Cooling does not hold capacity. Admission and recovery spacing are checked again immediately
  before actual invocation. Delayed capacity grants cannot release a burst: a request that must
  wait again releases capacity first. Retries rejoin the queue fairly. A custom `CapacityProvider`
  needs no additional methods. Recovery uses its `concurrency` when available, otherwise observed normal concurrency (at least one);
  a shared scope uses the sum of participating executor targets.
- `run()` recovers temporary limits internally; `map()` does too, reporting an exhausted budget
  as a per-operation failure with `operation_id`. Public `RateLimitTimeoutError` subclasses
  `OperationError` and retains the limit/provider cause chain. It does not close the pool.
  Billing/quota exhaustion remains a fatal `NonContinuableError` under the first-cause rules.

Migration: temporary limits no longer consume `LLM.retry_times`, OCR vendor `retry_times`, or
JEV's `RetryPolicy.analysis`; recovery works even when those are zero. Existing
`retry_interval_seconds` parameters remain for non-rate transport errors and provider-specific
recovery. They no longer set rate-limit cooldowns. JEV retains its 0.5-second non-rate transport
retry delay. Configure the scope instead of raising transport retry budgets to handle limits.
Custom `AsyncExecutor` implementations remain responsible for their own rate-limit behavior;
use `ConcurrentExecutor` with a custom `CapacityProvider` to retain the built-in recovery.

Within one SDK operation, the first observed and recorded `NonContinuableError` is the terminal
cause, including when stages use separate executors. The SDK cancels sibling work and waits for
cleanup before propagating that original exception with its cause chain intact. Later failures,
derived cancellations, and cleanup errors do not replace it. This order is the order observed
by the SDK, not a guarantee of which provider failed first in wall-clock time. Without a recorded
fatal cause, caller cancellation or task self-cancellation still propagates as `CancelledError`.
Independent operations do not share terminal state unless they share an executor.

## PDF patching primitives

For patch layout beyond the convenience methods, use these public types:

```python
from pdf_craft import EraseOptions, PDFPatcher, PDFTranslationPipeline, PatchTextOptions, PatchTextStyle

patcher = PDFPatcher(options=PatchTextOptions(
    # Omit font_name to select one installed local family for this patch run.
    # An explicit family is preferred; Qt falls back when it is absent.
    max_font_size=14,
    min_font_size=5,
    alignment="left",
    horizontal_padding=1,
    vertical_padding=1,
    styles={"heading": PatchTextStyle(max_font_size=18, min_font_size=8)},
), erase_options=EraseOptions(padding=2))
pipeline = PDFTranslationPipeline(patcher=patcher)
```

`PatchTextOptions` controls the default Qt text style and optional `PatchTextStyle` overrides by semantic layout key (`"body"`, `"heading"`, or `"heading:2"`). Omitting (or passing an empty) `font_name` resolves one installed local family and reuses it across all unspecified styles for that patch run. `PDFPatcher.font_resolutions` reports those automatic choices and any configured-family Qt fallback without conflating them with layout errors. The first pass gives a paragraph one fitted font size and flows it through its ordered `PDFReplacement.regions` without splitting a line across boxes. A second per-page pass locally normalizes each bbox toward its semantic-level character-weighted average without changing the frozen line count or text allocation, so final bbox sizes can differ slightly. Qt handles native shaping, wrapping, fallback fonts and glyph positions; missing configured fonts do not stop a patch. If valid source geometry still cannot contain a complete paragraph at its minimum size, the patcher writes the full text from the first source bbox at that minimum, allowing it to extend past normal bbox and obstacle boundaries rather than aborting the PDF. Headlines first retain their natural rightward overflow rule, then use that same final fallback if necessary.

`render_inline_formulas=True` is the default. Chapter XML preserves inline formulas so they remain in the translation context without replacing their source formula. When Matplotlib and local TeX are available, the patcher writes them as vector PDF content; otherwise (or if an individual formula fails) it falls back to readable plain text without failing the document. Set the option to `False` to select that plain-text behavior explicitly. Vector fragments carry a plain-text PDF `/ActualText` semantic replacement, but pdf-craft does not construct a complete tagged PDF and does not guarantee reader-specific copy order relative to surrounding text. Qt keeps its native language behavior for ordinary text; only a vector formula and its immediately following visible spacing form an unbreakable atom.

The patcher merges two independent overlays over a Ghostscript-compiled, fontless visual base of each source page's non-Annotation content: a local-background RGB rectangle erasure layer and a Qt-generated PDF text layer. `EraseOptions(padding=2)` expands each source box in its OCR-pixel coordinates before sampling and painting it. The supplied `PDFHandler` (or the default Poppler handler) renders original pages only for color sampling; it is never used as an output page image. The visual base preserves the appearance without leaving ordinary source or hidden OCR text selectable, searchable, or extractable. The source `/Annots` array is reattached above translation, so links, highlights, notes, forms, and other PDF Annotations remain independently interactive. The erasure is visual only and deliberately does not restore texture, rules, formulae, or artwork. Qt/PySide6, Ghostscript, Poppler for default background sampling, and the required fonts must be available on the machine producing the PDF.

`PDFTranslationPipeline.patch()` and `PDFPatcher.patch()` are async methods. Custom `pdf_handler` providers implement `AsyncPDFHandler`; custom visual compilers implement `async def compile(source_path, target_path)`.

`PDFTranslationPipeline` is the lower-level patching component for an already translated extraction. Prefer `PDFCraft.translate_pdf()` and `PDFCraft.patch_pdf_with_extraction()` when their fixed layout policy is sufficient.

## Other useful exports

- `PDFCraftExtraction` is an opaque handle for a validated extracted or transformed document. Use `PDFCraft.open_extraction("book.pcex")` or `await AsyncPDFCraft.open_extraction(...)` to load it. Ordinary directories are intentionally not public inputs.
- `PDFExtractor`, `MarkdownRenderer`, and `EpubRenderer` are async-only component APIs behind the façade; their `extract` and `render` methods must be awaited.
- `OCRTokensMetering` exposes `input_tokens` and `output_tokens`.
- `OCREvent` and `OCREventKind` support per-page progress and diagnostics.
- `await predownload_models(...)` prepares local OCR models before an offline `local_only=True` run.
- `PDFError`, `OCRError`, `NoUsableFillPagesError`, and `InterruptedError` are exported error types for application-level handling.

For complete workflows, start with [PDF conversion and translation](PDF_TRANSLATION.md) or [EPUB translation](EPUB_TRANSLATION.md), rather than composing internal modules.


## Incremental content and metadata

Both facades explicitly declare the same typed callback and translation options.
Callbacks may be synchronous or asynchronous; asynchronous callbacks are awaited.

| Entry point | Optional callbacks | Payload |
| --- | --- | --- |
| `ExtractionOptions` | `on_body`, `on_footnote` | Independent PCEX XML `Element` snapshots |
| `render_markdown`, `render_epub`, `translate_extraction` | `on_body`, `on_footnote` | `ParagraphContent[SourceRegion]` |
| `patch_pdf_with_extraction`, `translate_pdf` | `on_body`, `on_footnote` | `PDFParagraphContent` |
| `translate_epub` | `on_body` | `EpubFragmentContent` |
| Extraction and translation | `on_metadata` | `MetadataContent` |

`ParagraphContent(text, original, regions)` contains one whole paragraph; `text`
and optional `original` are separate Markdown. `SourceRegion(page,
source_order_in_page, bbox)` combines `PageGeometry` with the original fragment
identity and integer OCR bbox. `map_bbox(target_size)` returns floats based on
actual dimensions. `PDFSourceRegion` adds final font size, lines, formula atoms
and erasure masks in source pixels. It does not expose fonts or promise identical
browser glyph shaping. `EpubFragmentContent(epubcfi, original, translated)` carries
plain text for an atomic source-EPUB fragment; `EpubTranslation` is an alias.
Since 2.5.1, its fields are in constructor order: `epubcfi: str | None`, `original: str`,
`translated: str`. Check `epubcfi is not None` before using a location.
`text/html` or missing reliable location information yields `None` while retaining
text delivery; mixed XHTML/HTML books keep reliable CFIs for other fragments.
Parsing, translation, unexpected mapping errors and callback exceptions still propagate.
This contract is the same for both facades.

`MetadataContent(metadata, original=None)` contains complete `DocumentMetadata`
values, with `DocumentAuthor` values for authors. One-shot conversion separates
`ExtractionOptions.on_metadata` (extraction) from the top-level `on_metadata`
(optional translation). Content and metadata callbacks are separate from progress
events; whole-book translation totals can be `None` while input is still arriving.

`render_markdown` and `render_epub` accept `translator`, `window`, and
`with_anchored` for online translation. `patch_pdf_with_extraction` accepts
`translator`, `window`, `with_furniture`, and `on_translation_event`;
`translation_id` selects stored content and cannot be combined with `translator`.

| Resource method | Return value |
| --- | --- |
| `read_pages(extraction)` | `dict[int, PageGeometry]`, indexed by one-based page number |
| `read_asset(extraction, asset_hash)` | Independent resource `bytes` |

`ExtractionOptions.context` accepts an `ExtractionContext` during extraction:
`context.read_pages()` returns a synchronous geometry snapshot, while
`await context.read_asset(hash)` reads bytes during the active extraction.
The context must not be shared between concurrent extractions.

The content, geometry and metadata value classes are exported from `pdf_craft`;
XML snapshots use the standard-library `xml.etree.ElementTree.Element`. Internal translation mapping,
submission and disk-storage hooks, and PDF preview transport fields are not public
extension points. See [incremental callbacks](STREAMING.md) for complete field,
ordering, lifetime and publication semantics.


See [error handling](ERROR_HANDLING.md) for strict defaults, source locations and task-wide all-fallback protection.
