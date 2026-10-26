# Extraction and translation progress

`ExtractionOptions.on_extraction_event` and `on_translation_event` receive the same
`ProgressEvent` protocol. Callbacks may be synchronous or asynchronous. Content
callbacks (`on_body`, `on_footnote`, `on_metadata`) still deliver content, not progress.

## Three events, two scopes

`ProgressEventKind` has only `STARTED`, `PROGRESS`, and `COMPLETED`.
`operation` is `OperationKind.EXTRACTION` or `TRANSLATION`. A missing `unit_id`
denotes the operation; an integer denotes one logical work unit. IDs start at zero,
are unique across kinds within that operation, and are allocated only when work starts.
They are not source page numbers or persistent document identities.

Operation `STARTED` contains a **non-optional `unit_counts` mapping**, including zero
for inactive kinds. Translation also provides exact `total_words`. The pre-scan
accumulates counts and word totals only; it does not allocate units, IDs, stage
objects, a book-wide task plan, or retain the book's text. A unit is created on demand.
Completed units do not remain in an in-memory progress registry. XML units are
admitted when an execution group begins, rather than while inputs are pre-scanned
or prefetched. Asset counts and identities do not depend on request batch size. Pending page and
out-of-order EPUB state can live on temporary disk across existing batch barriers.

| Operation | UnitKind | Logical work counted |
| --- | --- | --- |
| Extraction | PAGE | One selected source page, through final accepted extraction. |
| Extraction | TOC | One directory analysis, including statistical analysis/cache reuse. |
| Extraction | METADATA | One book-metadata extraction when `extract_book_metadata=True`. |
| Translation | CONTENT | PCEX flow/footnote entry with narrative words; EPUB body task. |
| Translation | ASSET | One independent image/table asset, including its title/content/caption fields. |
| Translation | FURNITURE | One independent pattern position, or ordinary sections on one page. |
| Translation | METADATA | Zero or one aggregate of book metadata. |
| Translation | NAVIGATION | Zero or one aggregate of EPUB navigation and document-title fields. |

PCEX headings translated ahead of the body are the same CONTENT units: consuming
cached headings or reusing them in furniture does not create more units or words.
PCEX TOC is derived from headings and is not a separate translation. Native-only
PDF metadata delivery does not create a metadata extraction unit. Excluded pages
are absent from PAGE counts. Cache reuse of a selected page still completes a PAGE.
For opaque custom whole-chapter transformers, a CONTENT unit is a whole chapter;
only their observable adapter boundaries can report progress. Opaque whole-book batch
translators report each chapter unit when its returned result is accepted; internal
execution start times are unavailable. No IDs are allocated for those chapters in advance.

Unit `STARTED` contains `unit_kind`, `unit_id`, `max_stage`, and, for translation,
`total_words`. PAGE events also carry the real one-based `page_index`.
Unit `PROGRESS` carries `stage` and optional `unit_completed_words`; translation
also carries operation-wide `completed_words`. All word counters are cumulative,
not deltas. Totals come from the start events, rather than being repeated in every update.

## Integer stages

`max_stage` is the final stage **index**, not a duration or a percentage denominator.
Stages belong to units and concurrent units may be at different stages.

| Unit | Stage numbers |
| --- | --- |
| Extraction PAGE | 0: admitted/rendering; 1: rendered/OCR; 2: OCR result persisted; 3: JEV review; 4: LLM refinement; 5: final adopted page result. `max_stage=5`. |
| Extraction TOC / METADATA | 0: analysis (possibly multiple requests); 1: final accepted or fallback result. `max_stage=1`. |
| Translation units | 0: model translation and XML-fill rounds; 1: adapter finalization when observable; 2: finalized unit. `max_stage=2`. |

JEV/refinement are conditional. Reused pages can jump directly to stage 2; pages
without refinement jump to 5. No objects or optional-stage plans describe these
steps. A stage may report multiple word updates, and zero-word updates are valid.
Standard XML translation reports after each accepted or final-fallback round,
not after every failed HTTP attempt. One logical unit can require multiple rounds.

A page's OCR result is not its final extraction result. Ordered cross-page parsing
overlaps OCR. Current metadata and TOC analysis follow OCR; page refinement follows
TOC and a cross-page analysis barrier. JEV/LLM candidates may be rolled back during
global validation. PAGE completion waits until final recovery/validation and successful
publication, so it never needs retraction. Events do not change these dependencies.

## Completion, fallback, and errors

Unit `COMPLETED` carries `UnitOutcome.PROCESSED`, `REUSED`, `FALLBACK`, or `SKIPPED`.
**Every planned unit must complete if the operation continues and returns normally.**
Accepted ignore/fallback, skipped work, cached results and preserved source all settle
its count and words. Retrying a request neither creates another unit nor charges
its source words again. A completion result is not a quality score or a billing rule.

Operation `COMPLETED` follows all units and the successful output boundary. The PDF
extractor waits for PCEX export; translation facades wait for their target output.
One-shot PDF conversions complete extraction at the validated workspace boundary,
then optionally translate and render. Output failure, cancellation, fatal service
errors, and callback errors propagate; they do not fabricate operation completion.
A failed operation may already have completed some units or even processed all words.
An intentionally partially consumed low-level content stream has no operation completion.

No percentages or stage weights are supplied. For example, a client can retain only
aggregate completed counts, and live-unit stages if desired:

```python
from collections import Counter
from pdf_craft import ProgressEventKind

completed = Counter()
expected = {}

def report(event):
    global expected
    if event.unit_id is None and event.kind == ProgressEventKind.STARTED:
        expected = dict(event.unit_counts)
        completed.clear()
    elif event.unit_id is not None and event.kind == ProgressEventKind.COMPLETED:
        completed[event.unit_kind] += 1
        print(completed[event.unit_kind], expected[event.unit_kind])
```

Use `on_extraction_event=report` in `ExtractionOptions`, or `on_translation_event=report`.
Each callback consumer must distinguish operation completion from unit completion.
`OCRTokensMetering` remains available on metered extraction returns; tokens, timings,
and errors are not part of this minimal progress payload. ErrorPolicy reports failures.

## Source words

`WordCounter`, `count_text_words`, and `MAX_WORD_LENGTH` share one implementation:
Han characters each count as one word; other consecutive Unicode letters/numbers
count as `ceil(length / 20)` words. Punctuation and whitespace separate words and
are not counted. Combining marks continue a word without increasing its length.
Continuous Kana/Hangul use the same long-word rule; this is not linguistic segmentation.
XML inline tags and arbitrary input chunks do not split words; paragraph/field
boundaries do. Context prompts, temporary formula tokens, and reused headings are
not additional work. Counts depend on source text, not output or tokenizers.

```python
from pdf_craft import WordCounter, count_text_words
assert count_text_words('hello world 中文') == 4
counter = WordCounter()
counter.feed('hel')
assert counter.feed('lo中文') == 3
counter.boundary()
```

## Breaking migration

Replace root `OCREvent`/`OCREventKind` and `TranslationEvent`/`TranslationEventKind`/
`TranslationItemKind` imports with `ProgressEvent`, `ProgressEventKind`, `OperationKind`,
`UnitKind`, and `UnitOutcome`. Replace `ExtractionOptions.on_ocr_event` with
`on_extraction_event`. Native OCR events remain private adapter facts for parsing
and token metering, not the public extraction protocol.

Old `START`/`ITEM_START` become scoped `STARTED`; `ITEM_COMPLETE`/`COMPLETE` become
scoped `COMPLETED`. Old page failure/cache/ignore event kinds become completion
outcomes; excluded pages no longer create work. `item_id` is replaced by transient
integer `unit_id`, `item_completed_words` by `unit_completed_words`, and item totals
by unit `STARTED.total_words`. Removed chapter/TOC flags are replaced by `unit_counts`.
Character counters were already replaced with source words in the preceding change.

Advanced `XMLTranslator.translate_elements` users supplying a one-pass iterator or async stream
and a callback must supply pre-counted `unit_counts` and `total_words`; unknown counts
are rejected before model work. A reusable synchronous iterable can be counted directly, retaining no extra task list.
High-level PCEX/PDF/EPUB paths perform their own scalar pre-scan.

The old advanced `completed_words` offset and `emit_scope_events` / `emit_item_events` switches are removed; scoped lifecycle reporting is consistent whenever a callback is supplied.
