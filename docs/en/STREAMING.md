# Incremental extraction and translation notifications

Operations still return complete artifacts. Callbacks deliver intermediate content;
a successful callback does not mean the entire operation has succeeded. Cancellation
or callback exceptions abort the operation. The final PCEX/EPUB is published only
after successful validation/packaging.

## Callback payloads

| Operation | Content callbacks | Payload |
| --- | --- | --- |
| PDF → PCEX extraction | `ExtractionOptions.on_body` / `on_footnote` | Independent page-processed PCEX XML `Element` snapshots |
| PCEX translation, Markdown or EPUB rendering | `on_body` / `on_footnote` | `ParagraphContent[SourceRegion]`, one whole paragraph |
| PCEX → PDF patching or translation | `on_body` / `on_footnote` | `PDFParagraphContent`, one whole paragraph with final layout |
| Existing EPUB translation | `on_body` | `EpubFragmentContent`, one atomic translation fragment |
| Book metadata extraction or translation | `on_metadata` | `MetadataContent`, one finalized book view |

The content, geometry and metadata value classes are importable from `pdf_craft`;
XML snapshots use `xml.etree.ElementTree.Element`. Content callbacks are separate
from progress events such as `on_extraction_event` and `on_translation_event`. Standard PCEX/EPUB translation scans source words and unit counts before operation
STARTED; word progress is not a guarantee that content callbacks or file publication
have caught up. See the [2.5.0 release notes](../changelog/v2.5.0.md) for the original
callback APIs and [2.5.1](../changelog/v2.5.1.md) for optional EPUB locations.

## PDF extraction

```python
from pdf_craft import AsyncPDFCraft, ExtractionOptions

async def body(element):
    print(element.tag, list(element.iter("fragment")))

async def footnote(element):
    print(element.get("id"))

extraction = await AsyncPDFCraft(pdf_options).extract_pdf(
    "book.pdf", "book.pcex",
    ExtractionOptions(on_body=body, on_footnote=footnote),
)
```

Both callbacks are optional and accept synchronous or asynchronous functions.
Enable footnote extraction with `ExtractionOptions(footnotes=True)`. Page refinement
is independent: pass `refinement=Refinement(...)` with both JEV and LLM, even when
`footnotes=False`. Disabled footnotes never emit `on_footnote` content.
`on_body` receives one closed PCEX flow item: `<text>`, `<standalone-asset>`, or
`<display-formula>`. `on_footnote` receives one closed `<ref>` definition containing
`<mark>` and `<flow>`. The XML vocabulary is specified in [PCEX_FORMAT.md](PCEX_FORMAT.md).
These are independent trees: callers can retain or mutate them without changing
the artifact. Fragment page numbers, source orders, and pixel bounding boxes are
preserved; source page geometry is recorded in the resulting `pages.xml`.
Asset hashes refer to extraction resources, not embedded image bytes.

PDF extraction is a page pipeline. Each JEV job starts when its target and available
initial neighbors are ready. Its optional LLM repair follows that page's decision,
without waiting for other JEV jobs. Neighbor context always uses immutable initial
pages. Only the target JEV score enters the LLM prompt; neighbor scores are never
disclosed, even if already available. A pending earlier OCR page outside a job's
three-page neighborhood cannot block that job: pages 2–4 can start page 3's
JEV/LLM while page 1 is pending. Ordered paragraph delivery still waits for page 1.
Cross-window footnote identities and paragraph predecessors are bound during
ordered assembly, without feeding refined neighbors back into requests.

Page results may finish out of order. Ordered incremental assembly closes complete
paragraphs and footnote definitions and immediately enqueues their callbacks. Each
channel is ordered and delivered exactly once, with no final replay. This replaces
the temporary pre-refinement previews restored by the 2.6.7 hotfix: callbacks now
represent processed pages, not an initial preview followed by a repaired revision.
Pages that need no repair use their initial result.

Final TOC, heading levels, printed-directory exclusion and chapter partitioning may
change the artifact's organization; callbacks do not mirror those global edits and
must not wait for them. Metadata is independent of the content stream. A bounded
page window and upstream admission apply backpressure; completed content and
notification backlogs use disk. Callback errors and cancellation abort the operation;
receiving content is not a task-success signal.

Maintain barrier regressions proving that later OCR, unrelated JEV work and final
TOC completion cannot delay ready downstream work or closed content. See the
[design rationale and tradeoffs (Chinese)](../design/pdf-extraction-streaming.md).

### Analysis cache migration

Raw OCR pages remain source evidence and can be reused under the existing OCR
cache rules. Derived TOC/chapter/furniture files are rebuilt from this operation's
adopted content; an existing `toc.xml` is not proof of validity. No version/config
fingerprint exists for those old derived files, so they are never reused as input.
Intermediate and degraded semantic results cannot overwrite OCR evidence. Existing
PCEX files retain their format and identities; re-extracting a PDF can assign new
chapter IDs because IDs now belong to the final adopted heading set.

## EPUB translation

```python
from pdf_craft import EpubFragmentContent, SubmitKind

async def translated(item: EpubFragmentContent):
    print(item.original, item.translated)
    if item.epubcfi is not None:
        print(item.epubcfi)

await craft.translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE,
    llm=llm, executor=executor, on_body=translated,
)
```

`EpubFragmentContent` is an immutable value with exactly three fields: `epubcfi`,
`original`, and `translated`. Each event describes one accepted atomic translation
fragment, not an entire chapter or an LLM request window. Context-only window
segments are not delivered. `translated` is the accepted, structurally restored
fragment's plain text; it does not include the original text in bilingual output
modes. Pure-text normalization and formula projection can differ from literal DOM
text. No HTML/XML or internal template IDs are exposed.

The range CFI addresses the **input EPUB**. UTF-16 offsets and original text/tail
positions are captured before XML rewriting. A range may span inline elements.
The callback covers spine body content, including its headings/notes; it does not
emit TOC, OPF metadata, or document-head titles. Since 2.5.1, `epubcfi` is `str | None`:
XHTML spine documents (`application/xhtml+xml`) retain precise locations, while
`text/html` documents still deliver original/translated text with `epubcfi=None`.
Missing reliable source-location information also produces `None`; no guessed CFI
is emitted. In a mixed EPUB, this applies per fragment and does not disable the
reliable locations from other documents. Check for `None` before using a location.
A CFI does not promise a stable position in the translated output, whose DOM may
have changed. Only known location unavailability is downgraded: parsing,
translation, unexpected source-mapping/offset errors, and callback exceptions
still fail the operation.

| Situation | Location and outcome |
| --- | --- |
| XHTML with reliable source information | CFI string; text callbacks continue normally |
| Parseable `text/html` or known source-location unavailability | `epubcfi=None`; text callbacks continue normally |
| Actual parsing/translation failure, unexpected mapping/offset error or callback exception | Error propagates and the operation fails |

## Scheduling and cost

Callbacks are awaited in order. Completed content queues on disk, allowing OCR
and translation requests to progress independently of a slow callback. Disk use
can grow with the document; RAM does not hold the callback backlog. Producer EOF
is followed by draining in-flight work, flushing trailing content, and delivering
all queued callbacks before successful return. Callback XML/value conversion is
skipped independently for each missing callback; EPUB source tracking and CFI
indexing are enabled only when `on_body` is supplied.

PCEX translation and rendering offer paragraph notifications below; final output remains a complete artifact.


## PCEX paragraph consumers

PCEX reading and optional translation feed one private content stream. Markdown writes staged fragments
as items arrive; EPUB converts/spools EPUB records; PDF feeds the existing Qt planner through a disk queue.
No complete translated PCEX or chapter workspace is required before rendering. PCEX translation connects
the same producer to its chapter/layer writer. Legacy custom whole-task translators keep their extension contract.

`render_markdown`, `render_epub`, and `translate_extraction` accept optional `on_body` and `on_footnote`
receiving **ParagraphContent**:

```python
from pdf_craft import ParagraphContent, SourceRegion

async def paragraph(content: ParagraphContent[SourceRegion]):
    print(content.text, content.original)  # Markdown; original is optional
    for region in content.regions:
        print(region.page.page_index, region.source_order_in_page, region.bbox)
        print(region.map_bbox(target_size=(800, 1100)))

await craft.render_epub(extraction, "book.epub", translator=translator, window=4,
                        on_body=paragraph, on_footnote=paragraph)
```

Each event covers one whole logical paragraph/heading, including all its source boxes across pages.
Footnotes emit one event per paragraph. Standalone assets/formulas do not pretend to be paragraphs.
`text` is the selected text or translation as Markdown; `original` is separate Markdown (None for source-only).
Bilingual presentation is never premerged in the event. Final files still honor the requested RenderMode.
EPUB's Markdown callback is a display branch, never EPUB's generation input. It has no CFI; EPUB **input**
translation retains its existing EpubFragmentContent contract. PDF extraction XML callbacks remain unchanged.

SourceRegion contains immutable PageGeometry, source_order_in_page (the PCEX source_order identity,
not an array index), and the original integer OCR-pixel bbox `(left, top, right, bottom)`.
The origin is top-left, with x increasing rightward and y downward. Its map_bbox(target_size) returns floats using
actual image width/height ratios. render_dpi is the requested DPI; page-image limits can reduce effective DPI.
Mapping assumes the same crop/orientation and does not infer rotation, cropping, or padding.

### PDF layout previews

PDF patching callbacks receive PDFParagraphContent, extending ParagraphContent with a list of
PDFSourceRegion values. Each region extends SourceRegion with final font_size, lines (text, x, baseline),
formulas and erasures. A paragraph remains one event even across pages; final page typography may delay it.
All layout coordinates and font sizes are floating-point **source OCR pixels**, while original bbox stays integer.
Unused/preserved regions have empty lines and font_size=None. Failed paragraphs are not reported as accepted layouts.
Lines come from the actual Qt layout, not another wrapping algorithm. Formula vector atoms carry latex, PDF bytes,
x/baseline/descent, and scale_x/scale_y into source pixels. Text beside a formula is split into runs
with separate x coordinates, preserving the reserved gap without exposing internal formula proxies.
Erase masks contain actual sampled RGB colors and pixel rectangles. Painting translated text onto an unerased source
would leave the original visible. There is no font_id or font distribution; client-selected fonts provide an approximate
preview, not a promise of identical glyph shaping. Events are neither full-page PDFs nor completed files.

All four operations await asynchronous callbacks. Each channel is ordered independently. Callback backlog and
out-of-order completed document contexts live on disk; a slow first request does not pin every source DOM or idle
available request capacity. Missing callbacks skip Markdown preview conversion, public event construction, and PDF
layout export. Exceptions/cancellation terminate work and preserve existing final outputs. Markdown resources and
text use staged replacement with rollback (multiple paths cannot become atomically visible together).

Use `await craft.read_pages(extraction)` to obtain an independent mapping from
one-based page indexes to immutable `PageGeometry` values (`page_index`, `width`,
`height`, `render_dpi`, `coordinate_space`). Use `await craft.read_asset(extraction,
asset_hash)` for resource bytes, without depending on materialization paths.
The synchronous facade provides equivalent synchronous methods.

During PDF extraction pass a caller-owned `ExtractionContext` through
`ExtractionOptions(context=context)`. `context.read_pages()` is a synchronous
in-memory snapshot; geometry for delivered content is available before its
callback. `await context.read_asset(hash)` copies resources while extraction is
active. Geometry remains accessible afterwards; asset access expires with the
extraction call, so use the completed PCEX for subsequent reads. Do not share a
context between concurrent extractions.

Final EPUB/PDF files are submitted whole. Markdown text and assets are staged;
publication rolls back existing output on a replacement failure, but a file and
asset directory cannot be made atomically visible together across paths.


## One book metadata notification

Optional `on_metadata` receives an immutable `MetadataContent`. Its `metadata` is a complete
`DocumentMetadata`, including fields not translated. Its `original` is the complete source
metadata for translation, or `None` for extraction. `DocumentMetadata` and `DocumentAuthor`
are exported from `pdf_craft`; callers never need to merge translation overlays.

```python
from pdf_craft import ExtractionOptions, MetadataContent

async def metadata(content: MetadataContent):
    print(content.metadata.title, content.metadata.authors, content.metadata.language)

extraction = await craft.extract_pdf(
    "book.pdf", "book.pcex", ExtractionOptions(on_metadata=metadata),
)
await craft.render_markdown(extraction, "book.md", translator=translator,
                            on_metadata=metadata)
```

Without LLM metadata extraction, PDF extraction notifies once after reading native PDF metadata,
before OCR, and writes that same value into the manifest. With LLM extraction enabled, it waits
for the final field-wise merge with native fallback, without an intermediate notification.
Allowed LLM failures use the fallback; an empty result still notifies with `DocumentMetadata()`.
Native PDF metadata is read consistently even without a content callback; registering
only a progress callback does not change the manifest. Public snapshots are constructed
only for `on_metadata`. OCR metadata begins with committed front-page evidence and may
overlap later OCR; only the final merge is notified.

PCEX translation and online Markdown/EPUB/PDF translation rendering notify once from the shared
metadata stage. EPUB input translation also supports `on_metadata` for OPF book metadata, not
navigation or individual document head titles. Unmapped EPUB extensions remain in the output;
the callback is a standardized book view rather than an OPF copy.
Even in EPUB append modes, callback `metadata` combines translated fields with unchanged
fields, while `original` stays separate. The EPUB file still uses the selected writeback mode.
For one-shot PDF conversion, `ExtractionOptions.on_metadata` observes extraction, while the
separate top-level `on_metadata` observes the optional translation phase.
When `extraction_path` is supplied, the source PCEX is published after extraction;
a later translation or rendering failure does not roll back that completed extraction.

Async callbacks are awaited. Callback exceptions and cancellation abort the operation without
publishing a partial output. Notification does not mean the final artifact has committed.
No public snapshot is built when the callback is absent.

`EpubTranslation` remains a compatibility alias of `EpubFragmentContent`, not a separate type.
The rename does not change fragment granularity, CFI, whitespace or newline handling.

Translation progress uses cumulative source words; see [the event and counting contract](TRANSLATION_PROGRESS.md).


PCEX translation admits ready metadata/body/footnote/asset/furniture jobs through one
shared `window`; title-dependent furniture waits on disk, outside execution slots.
Heading completion releases its own dependants, including fallback. Asset context is
original text, clipped to 480 characters, and requires only source lookahead. Body and
footnote callbacks are independently ordered; they can precede ordered renderer
consumption. Progress reports actual work and may interleave. See
[progress and scheduling](TRANSLATION_PROGRESS.md#pcex-scheduling-and-delivery).

PCEX translation waits for successful input completion before deciding that a queue is
empty. Title-dependent furniture waits for the title result (including explicit
fallback); body and footnote delivery each drain all pending content before their
channel ends. A failed or cancelled input scan is not normal EOF. Successful
completion includes callback delivery as well as the final artifact; silently
omitted content is not a fallback.

## Local completion and PDF layout latency (2.6.7)

PAGE stage 7 / COMPLETED means page OCR, JEV, optional LLM refinement and necessary validation have finished; it does not wait for paragraph assembly, TOC or publication. Only operation completion promises the final artifact. Subsequent recovery does not reopen completed page units.

Accepted EPUB atomic fragments enter ordered delivery immediately, without waiting for the chapter or next chapter. A unit split across groups, or contained by an unfinished atomic owner, still waits for its own complete outcome; rejected partial translations are never previewed.

PDF patching consumes ready body, note and furniture results. Furniture jobs are page fragments or shared templates, translated once and joined only by dependent pages. Metadata is joined at publication, not before layout. Whole paragraphs retain first-pass cross-bbox layout; each paragraph is fitted once when ready, with frozen contributions spooled per page. A page waits only for paragraphs/furniture directly touching it, not the transitive connected component. A cross-page callback waits for all of its own final regions; final PDF page order is assembled afterward. Body and note notifications each retain source order without a shared cross-channel delivery barrier.

Words, content and publication are independent observations: none is delayed to synchronize with another. Necessary atomic/page dependencies and slow user callbacks may still delay notifications. Custom whole-element translator extensions keep their explicit batch contract; they do not acquire the standard XMLTranslator's streaming guarantee.

Independent PDF pages do not inherit font sizes from whichever page finishes first. Headings use body references on their own page, or the configured headline fallback when none exists. Legacy ordered batch layout retains its preceding-body rule.
