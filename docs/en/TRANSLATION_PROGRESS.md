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
| Extraction | METADATA | One book-metadata extraction: native PDF fields, or OCR evidence with native fallback. |
| Translation | CONTENT | PCEX flow/footnote entry with narrative words; EPUB body task. |
| Translation | ASSET | One independent image/table asset, including its title/content/caption fields. |
| Translation | FURNITURE | One independent pattern position, or ordinary sections on one page. |
| Translation | METADATA | Zero or one aggregate of book metadata. |
| Translation | NAVIGATION | Zero or one aggregate of EPUB navigation and document-title fields. |

PCEX headings are ordinary CONTENT units. Their final results release dependent
furniture immediately; reuse creates no additional units, words or LLM calls.
PCEX TOC is derived from headings and is not a separate translation. EPUB XHTML
`head/title` fields belong to NAVIGATION, even without a TOC or OPF metadata.
Only OPF book fields belong to the METADATA aggregate. Native-only PDF extraction
starts and completes its METADATA unit before OCR, including an empty/fallback result. Excluded pages
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
| Extraction PAGE | 0: page preparation/rendering; 1: first OCR pass (including admission wait); 2: second-pass image preparation (background color, masking, saving); 3: second OCR pass (including admission wait); 4: OCR parsing, assembly and persistence complete; 5: JEV review; 6: LLM refinement; 7: final adopted page result. `max_stage=7`. |
| Extraction TOC / METADATA | 0: analysis (possibly multiple requests); 1: final accepted or fallback result. `max_stage=1`. |
| Translation units | 0: model translation and XML-fill rounds; 1: adapter finalization when observable; 2: finalized unit. `max_stage=2`. |

JEV/refinement are conditional. Without a second scan, pages jump from 1 to 4. Reused pages can jump directly
to stage 4; pages without refinement jump to 7. No objects or optional-stage plans describe these
steps. A stage may report multiple word updates, and zero-word updates are valid.
Stage 0 deliberately includes both translation and XML-fill/validation calls; it
does not distinguish each request or imply a single serial book-wide phase.
Standard XML translation reports after each accepted or final-fallback round,
not after every failed HTTP attempt. One logical unit can require multiple rounds.

A page's OCR result is not its final extraction result. Ordered cross-page parsing
overlaps OCR. OCR-based metadata starts when the first three available front-page
records are committed (or the shorter selected input ends); `read_more` waits only
for its requested evidence, up to twelve pages. Complete and partial caches use the
same readiness rule. Existing unselected cached XML remains evidence, including
fallback XML with a failure marker; it is ready immediately because this operation
will not retry it. Selected failed caches wait for their terminal replacement.
TOC still follows OCR; page refinement follows TOC and a
cross-page analysis barrier. JEV/LLM candidates may be rolled back during
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
A consumed public XML task that produces no segments still counts as one unit;
it completes as `SKIPPED` without a model request. This includes custom source
projections that remove all segments; any declared source words are settled.

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

In 2.6.5, PAGE stages use `max_stage=7`: old OCR-persisted/JEV/LLM/final stages
2/3/4/5 are now 4/5/6/7. Update numeric stage labels in consumers. Stages 1, 2
and 3 are reported on entry, before the expensive operation or request admission;
measure elapsed time between events rather than treating stages as equal weights.
Retries remain in the same stage and do not create units or move stages backwards.
For local OCR, stage 2 covers background-color detection and masking; upstream
serialization of its request image remains inside the stage-3 recognition call.
TOC, METADATA and translation stage numbers and the event structure are unchanged.

The earlier event-system migration shipped in **2.6.4**. Applications upgrading from 2.6.3 or earlier
must update progress consumers; see the [release notes](../changelog/v2.6.4.md).

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
Character counters are replaced with source words in 2.6.4: rename `*_characters`
event fields to `*_words`, `TranslationTask.character_count` to `word_count`,
advanced `total_characters` to `total_words`, and `source_character_count` to
`source_word_count`. Values follow the source-word rules above, not character counts.

Advanced `XMLTranslator.translate_elements` users supplying a one-pass iterator or async stream
and a callback must supply pre-counted `unit_counts` and `total_words`; unknown counts
are rejected before model work. A reusable synchronous iterable can be counted directly, retaining no extra task list.
High-level PCEX/PDF/EPUB paths perform their own scalar pre-scan.

The old advanced `completed_words` offset and `emit_scope_events` / `emit_item_events` switches are removed; scoped lifecycle reporting is consistent whenever a callback is supplied.


## PCEX scheduling and delivery

Metadata, body, footnotes, independent image/table text and furniture share a single
PCEX `window` of active business jobs. Inside each admitted job the XML group window
is one; every request still uses the original shared translator executor and its
rate limits. Increasing streams never creates another executor or multiplies nested
windows. `window=1` necessarily serializes active work.

The scanner spools source jobs, pending title dependencies and completed results to
temporary SQLite storage. Units/IDs are created only on execution, not by the scan.
Ready work is admitted fairly across channels; a pending furniture/title reuse never
holds a window slot. A heading is translated once; its accepted result (including
fallback or absence) releases only its dependants. Unknown titles settle at input
completion instead of hanging. Reused original text is not a trusted translation.

Asset jobs preserve original context: nearest heading plus containing paragraph for
embedded assets, nearest surrounding body text for standalone assets, clipped to
480 characters. Only necessary source lookahead is awaited, never translated prose.
Context is not counted as words or persisted. Footnote definitions continue through
the narrative channel; this does not add asset translation inside footnotes.

Progress may interleave in execution order. Body and footnote content callbacks each
retain their own source order and use separate disk delivery, independently of the
ordered chapter writer/renderer. Slow callbacks and out-of-order results do not hold
execution slots; callback failures still cancel and settle sibling work. Renderers
may wait for finalized book metadata/furniture before consuming the spooled content.
Operation completion requires successful complete consumption/publication; closing a
partially consumed stream cancels and settles remaining jobs immediately instead of
waiting for the book to finish, and cannot fabricate completion. EPUB input retains its existing
unified task stream and ordered `EpubFragmentContent` delivery.


To obtain the same source-word total before starting translation, or inspect actual remote consumption independently of progress, see [Usage and sizing](USAGE.md).
