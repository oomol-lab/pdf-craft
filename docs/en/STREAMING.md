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
are delivered through `on_body` at EOF, with their original source positions. Empty projected
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

PCEX translation callbacks are not part of this API. Rendering PCEX to Markdown,
EPUB, or PDF remains an operation returning a complete output.
