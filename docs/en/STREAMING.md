# Incremental extraction and translation notifications

Operations still return complete artifacts. Callbacks deliver intermediate content;
a successful callback does not mean the entire operation has succeeded. Cancellation
or callback exceptions abort the operation. The final PCEX/EPUB is published only
after successful validation/packaging.

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
Enable footnote extraction with `FootnoteOptions` to recognize footnotes.
`on_body` receives one closed PCEX flow item: `<text>`, `<standalone-asset>`, or
`<display-formula>`. `on_footnote` receives one closed `<ref>` definition containing
`<mark>` and `<flow>`. The XML vocabulary is specified in [PCEX_FORMAT.md](PCEX_FORMAT.md).
These are independent trees: callers can retain or mutate them without changing
the artifact. Fragment page numbers, source orders, and pixel bounding boxes are
preserved; source page geometry is recorded in the resulting `pages.xml`.
Asset hashes refer to extraction resources, not embedded image bytes.

These notifications describe the traditional cross-page analysis before global
TOC exclusion, heading-level finalization, punctuation normalization, and optional
footnote refinement. They are not a replay of the finalized chapter files. Unknown
footnote ownership remains preserved in the final narrative; these unresolved items
are delivered through `on_body` in source reading order after page ownership closes,
without an out-of-order EOF appendix. Empty projected
fragments keep their source identities. The complete archive remains the authority
for final structure, definitions, and resource validation.

## EPUB translation

```python
from pdf_craft import EpubTranslation, SubmitKind

async def translated(item: EpubTranslation):
    print(item.epubcfi, item.original, item.translated)

await craft.translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE,
    llm=llm, executor=executor, on_body=translated,
)
```

`EpubTranslation` is an immutable value with exactly three fields: `epubcfi`,
`original`, and `translated`. Each event describes one accepted atomic translation
fragment, not an entire chapter or an LLM request window. Context-only window
segments are not delivered. `translated` is the accepted, structurally restored
fragment's plain text; it does not include the original text in bilingual output
modes. Pure-text normalization and formula projection can differ from literal DOM
text. No HTML/XML or internal template IDs are exposed.

The range CFI addresses the **input EPUB**. UTF-16 offsets and original text/tail
positions are captured before XML rewriting. A range may span inline elements.
The callback covers spine body content, including its headings/notes; it does not
emit TOC, OPF metadata, or document-head titles. Precise CFI callbacks require XHTML
spine documents (`application/xhtml+xml`); `text/html` inputs remain supported
without this callback, but callback requests reject them rather than inventing
reader-dependent locations. A CFI does not promise a stable position in the
translated output, whose DOM may have changed.

## Scheduling and cost

Callbacks are awaited in order. Completed content queues on disk, allowing OCR
and translation requests to progress independently of a slow callback. Disk use
can grow with the document; RAM does not hold the callback backlog. Producer EOF
is followed by draining in-flight work, flushing trailing content, and delivering
all queued callbacks before successful return. Callback XML/value conversion is
skipped independently for each missing callback; EPUB source tracking and CFI
indexing are enabled only when `on_body` is supplied.

PCEX translation writing has no content callback. Rendering may provide content
notifications as described below, while final output remains a complete artifact.


## PCEX content consumers and public geometry

PCEX reading and optional translation feed a shared private content stream.
The existing flow items and reference definitions retain source identities and
geometry. `translate_extraction` consumes the same translated stream to append
a complete translation layer and coverage; it has no public content callbacks.
Legacy custom whole-task translators keep their original extension contract.

`render_markdown`, `render_epub`, `patch_pdf_with_extraction`, and `translate_pdf`
accept optional `on_body` and `on_footnote`. Their independent PCEX XML snapshots
use the extraction callback schema: a body flow item or a full reference
definition. Source page/bbox information is unchanged by translation. Each
channel is ordered; there is no combined ordering between channels. No callbacks
means no additional user XML packaging. A callback failure or cancellation
terminates the operation; callbacks do not imply successful final-file creation.

Markdown/EPUB rendering accepts `translator`, `window`, and `with_anchored` for
translation during reading. PDF patching accepts `translator` and `window`, or
use `translate_pdf`. Do not combine a translator with a stored `translation_id`.
New translation defaults to replacement rendering. Bilingual Markdown/EPUB is
also supported; translation-time callbacks still deliver replacement content,
while direct stored-layer rendering reports its selected display view. These
callbacks describe content, not output-format bytes or final PDF glyph layout.

Out-of-order LLM results are spooled to disk without blocking admission behind
a slow first request. Two-stage vendor OCR can advance individual pages to
stage two before all first passes finish. Unresolved footnote source is emitted
in body reading order, not appended after later pages at EOF.

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
