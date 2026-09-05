# PDFCraftExtraction (`.pcex`) Format Reference

This document is the English-language reference for PDFCraftExtraction v1. It describes the public intermediate format that the current pdf-craft implementation can produce, read, and validate, as well as how each member is used by downstream rendering, translation, and PDF patching workflows.

This reference distinguishes a *canonical artifact*—a `.pcex` file written by pdf-craft—from the *current validator* implemented by `PDFCraftExtraction.open()` and `PDFCraftExtraction.validate()`. Canonical artifacts preserve all relationships described here. The current validator does not enforce every semantic relationship.

## Purpose and scope

PDFCraftExtraction is the structured document that pdf-craft extracts from a PDF. It sits between the OCR/PDF frontend and backends such as Markdown rendering, EPUB rendering, and PDF patching. It stores:

- document metadata and the format version;
- source-PDF page indexes, page dimensions in pixels, and the OCR coordinate space;
- body text, headings, equations, tables, images, and footnotes organized by chapter;
- bounding-box mappings from text blocks and assets to source-PDF pages;
- an optional table of contents and cover image.

A `.pcex` file does not contain the source PDF, OCR model responses, per-page OCR caches, failure markers, or diagnostic plots. It can therefore be copied, uploaded, stored, and transferred between machines as a self-contained input for downstream work that does not repeat OCR. Patching content back into a PDF still requires the caller to retain the source PDF separately.

The public interchange form is always a ZIP archive with a `.pcex` filename extension. An unpacked directory is only a physical representation of the archive contents; it is not a supported public input form.

## Quick reference

A canonical archive has the following layout:

```text
book.pcex                       # ZIP archive using Deflate compression
├── manifest.json               # required: format, producer, and document metadata
├── pages.xml                   # required: page geometry and coordinate space
├── chapters/                   # required; may be empty
│   ├── chapter_head.xml        # optional: content before the first formal chapter
│   └── chapter_<id>.xml        # zero or more formal chapters
├── assets/                     # required; may be empty
│   └── <sha256>.png            # zero or more content assets
├── toc.xml                     # optional: hierarchical table of contents
└── cover.png                   # optional: cover image
```

The archive root and its two subdirectories may not contain members other than those shown above. Member names are case-sensitive. The `.pcex` suffix check on the public file path is case-insensitive.

| Member | Required | Contents | Primary consumers |
| --- | --- | --- | --- |
| `manifest.json` | Yes | Version, producer, timestamp, bibliographic metadata, and language | Loader and EPUB renderer |
| `pages.xml` | Yes | DPI, page dimensions in pixels, and coordinate system | Validator and PDF patcher |
| `chapters/` | Yes | Structured chapters and source-PDF positions | Markdown/EPUB renderers, translation, and PDF patching |
| `assets/` | Yes | Content-addressed PNG files | Markdown and EPUB renderers |
| `toc.xml` | No | TOC tree and printed-TOC pages | EPUB renderer and chapter relationships |
| `cover.png` | No | Cover image | Markdown and EPUB renderers |

JSON and XML written by pdf-craft use UTF-8. XML files include an `<?xml version="1.0" encoding="UTF-8"?>` declaration. Paths inside the ZIP use `/` as their separator.

Version 1 has no `document.json` or `source-map.json`. Document metadata is centralized in `manifest.json`, page geometry in `pages.xml`, and the source-PDF position of each content block is stored directly in the chapter XML.

## Creating, saving, and resuming an extraction

### Extracting a PDF

`PDFCraft.extract_pdf()` is the standard public entry point. Its second argument selects the archive location and must end in `.pcex`:

```python
from pdf_craft import PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(...))
extraction = craft.extract_pdf("book.pdf", "output/book.pcex")
```

On success, the archive is stored at the caller-supplied `output/book.pcex` path and the return value is a `PDFCraftExtraction` ready for further processing. An existing destination is never overwritten; pdf-craft raises `FileExistsError` instead. Missing parent directories are created automatically.

To collect OCR token metering data:

```python
extraction, metering = craft.extract_pdf_with_metering(
    "book.pdf", "output/book.pcex"
)
```

A one-shot conversion can retain the intermediate artifact as well:

```python
craft.convert_pdf_to_markdown(
    "book.pdf",
    "output/book.md",
    extraction_path="output/book.pcex",
)
```

During a complete conversion, `convert_pdf_to_markdown()` and `convert_pdf_to_epub()` pass an internal, unpacked workspace directly from the frontend to the backend. They write an additional `.pcex` only when `extraction_path` is provided, avoiding an unnecessary compression-and-extraction cycle for internal handoff.

### Opening and validating

```python
from pdf_craft import PDFCraftExtraction

extraction = PDFCraftExtraction.open("output/book.pcex")
extraction.validate()
```

The following forms are equivalent. Each opens, extracts, and validates the archive during construction:

```python
PDFCraftExtraction("output/book.pcex")
PDFCraftExtraction.open("output/book.pcex")
PDFCraftExtraction.load("output/book.pcex")
```

If the path is not a regular file, pdf-craft raises `FileNotFoundError`. If its suffix is not `.pcex`, pdf-craft raises `ValueError`. A directory is not a valid public input even if it contains every required member.

On success, `validate()` returns the extraction itself to support chaining. `validate(require_toc=True)` additionally requires `toc.xml`; EPUB rendering uses this mode, whereas Markdown rendering only requires the base format to be valid.

### Resuming on another machine

Machine A can perform OCR:

```python
craft = PDFCraft(pdf=PDFOptions(...))
craft.extract_pdf("book.pdf", "book.pcex")
```

After transferring the single `book.pcex` file, machine B can continue without configuring PDF or OCR infrastructure:

```python
from pdf_craft import PDFCraft, PDFCraftExtraction

extraction = PDFCraftExtraction.open("book.pcex")
craft = PDFCraft()
craft.render_markdown(extraction, "book.md")
craft.render_epub(extraction, "book.epub")  # requires toc.xml in the archive
```

These backends read only extraction members. They do not look for the original machine's analysis directory or `ocr/` cache.

### Translation, export, and rendering

Every `PDFCraft` backend that accepts an extraction can take either a `PDFCraftExtraction` object or a `.pcex` path directly:

```python
craft.render_markdown("book.pcex", "book.md", assets_path="book-assets")
craft.render_epub("book.pcex", "book.epub")

translated = craft.translate_extraction(
    "book.pcex", "book.zh.pcex", translator
)
```

`translate_extraction()` creates a new `.pcex`. It preserves the source archive's manifest, page geometry, TOC, cover, and assets, and rewrites only the chapter XML processed by the transformer. The output path must end in `.pcex` and must not already exist.

`PDFCraftExtraction.export(path)` revalidates the current object, writes a new `.pcex`, and returns an object backed by the new archive. It writes to a temporary file in the destination directory before atomically replacing the target name; an existing target is still never overwritten. Container metadata such as ZIP member timestamps is not stable format data, so two exports are not guaranteed to be byte-for-byte identical.

### Public metadata accessors

`PDFCraftExtraction` does not expose its internal extraction directory or offer a public editing interface for the chapter directory. It provides these read-only methods:

| Method | Return value |
| --- | --- |
| `page_pixel_sizes()` | A new `{page_index: (pixel_width, pixel_height)}` dictionary |
| `render_dpi()` | The positive integer DPI from `pages.xml` |
| `document_metadata()` | A shallow copy of the `document` object from `manifest.json` |
| `language()` | The `document.language` string, or `None` |
| `book_meta()` | An `epub_generator.BookMeta` constructed from document metadata |

## `manifest.json`

### Complete example

```json
{
  "format_version": 1,
  "producer": {
    "name": "pdf-craft",
    "version": "2.0.0"
  },
  "created_at": "2026-09-05T03:20:00.000000+00:00",
  "document": {
    "title": "Example Book",
    "description": null,
    "publisher": "Example Press",
    "isbn": null,
    "authors": ["Example Author"],
    "editors": [],
    "translators": [],
    "modified": "2026-08-20T12:00:00+08:00",
    "language": "en"
  }
}
```

The top-level value must be a JSON object. Unlisted top-level fields are not allowed.

| Field | Type | Required | Meaning and constraints |
| --- | --- | --- | --- |
| `format_version` | integer | Yes | The only currently supported value is `1` |
| `producer` | object | Yes | Identifies the software that created the archive; must contain exactly `name` and `version` |
| `created_at` | string or null | No | Archive creation time; a string must be a parseable ISO 8601 datetime |
| `document` | object | Yes | Document-level metadata; must contain exactly the nine fields in the next section |

The canonical `format_version` value is the JSON number `1`. The current implementation directly compares the decoded JSON value with the Python integer `1`, without a separate JSON type assertion. Producers must not rely on language-specific behavior such as booleans comparing equal to integers.

When pdf-craft writes an archive, `producer.name` is always `pdf-craft`, and `producer.version` is the installed pdf-craft package version. It falls back to `unknown` when the installed version cannot be determined. The current validator permits other producers, but both `name` and `version` must be non-empty strings.

pdf-craft always writes `created_at` as the current time with a UTC offset. The current reader also accepts a missing or `null` `created_at`. A string value must be accepted by Python's `datetime.fromisoformat()`, but the format does not require the timestamp to use UTC.

### The `document` object

All nine fields are required. A single-value field with no value uses `null`; a contributor field with no members uses an empty array. Fields may not be omitted or added.

| Field | Type | Meaning |
| --- | --- | --- |
| `title` | string or null | Book title; if PDF metadata is readable but has no title, pdf-craft uses the source filename without its extension |
| `description` | string or null | Document description |
| `publisher` | string or null | Publisher |
| `isbn` | string or null | ISBN; the format imposes no further lexical constraints |
| `authors` | string[] | Authors, preserving array order |
| `editors` | string[] | Editors, preserving array order |
| `translators` | string[] | Translators, preserving array order |
| `modified` | string or null | Document modification time; a string must be an ISO 8601 datetime |
| `language` | string or null | Document language identifier |

With the default PDF reader, a missing `/ModDate` does not produce a `null` `modified` field. The reader begins with the current UTC time at the moment metadata is read and replaces it only when `/ModDate` exists and its year, month, day, hour, minute, and second can be parsed successfully. If `/ModDate` is missing, empty, too short, or invalid, the manifest therefore retains that current UTC timestamp. The current parser takes the first 14 date/time digits and labels the result as UTC; it does not interpret a PDF timezone offset that may follow them. `modified` is written as `null` only when metadata extraction as a whole raises `PDFError` and the extractor cannot obtain any `BookMeta`.

The format does not currently restrict `language` to a fixed set of language codes, but the EPUB renderer supports only `zh` and `en`. During EPUB rendering, an explicit `lan` argument takes precedence, followed by this field, with `zh` as the final default. An explicit `book_meta` argument likewise takes precedence over the `BookMeta` derived from the manifest.

For an ordinary PDF extraction, pdf-craft writes the PDF's bibliographic metadata but does not infer a language, so `language` is usually `null`. Translating an extraction does not automatically update the manifest or language.

## `pages.xml`

### Complete example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<pages index_base="1" coordinate_space="ocr_pixels" render_dpi="300">
  <page index="1" width="2480" height="3508" />
  <page index="2" width="2480" height="3508" />
  <page index="3" width="2480" height="3508" />
</pages>
```

The root element must be `<pages>` and must have exactly three attributes:

| Attribute | Fixed value/type | Meaning |
| --- | --- | --- |
| `index_base` | `1` | All source-PDF page indexes in the archive are 1-based |
| `coordinate_space` | `ocr_pixels` | Bounding boxes use pixel coordinates from the OCR page bitmap |
| `render_dpi` | positive integer | Page-rendering DPI requested during extraction; defaults to `300` when not specified |

Each direct child must be a `<page>` with exactly these attributes:

| Attribute | Type | Meaning and constraints |
| --- | --- | --- |
| `index` | positive integer | Source-PDF page index; unique within the file |
| `width` | positive integer | Width of that page's OCR bitmap in pixels |
| `height` | positive integer | Height of that page's OCR bitmap in pixels |

A `<page>` may not contain child elements, and its canonical form has no text. The current validator does not inspect page text nodes. The page list may be empty or sparse—for example, a selected-page extraction records only pages for which geometry was obtained—but every page referenced by a chapter element must be present. Physical element order does not affect page-index semantics; pdf-craft writes pages in ascending `index` order.

### Coordinates and bounding boxes

Every `det` attribute in chapter XML has this form:

```text
left,top,right,bottom
```

All four values are decimal integers. The origin is the top-left corner of the OCR page bitmap; x increases to the right and y increases downward. The boundaries identify the top-left and bottom-right corners using PIL crop-box semantics. They must satisfy:

```text
0 <= left < right <= page.width
0 <= top  < bottom <= page.height
```

`pages.xml` is therefore the only data source within an extraction that downstream code may use to interpret page indexes and bounding boxes. An analysis workspace may still contain cached OCR page dimensions, but backends do not fall back to them.

## `toc.xml`

### Complete example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<toc page_indexes="2">
  <item id="1" page_index="3" order="0" level="0">
    <item id="2" page_index="8" order="1" level="1" />
  </item>
</toc>
```

`toc.xml` is optional for the format as a whole, but EPUB rendering requires it. An empty TOC is written as `<toc page_indexes="" />`.

The root element must be `<toc>`. Its required `page_indexes` attribute is a comma-separated list of source-PDF page indexes that were recognized as printed table-of-contents pages and consequently excluded from chapter content. The value is an empty string when there are no such pages.

Both the root element and an `<item>` may contain any number of direct `<item>` children. Nesting expresses the TOC hierarchy. Every `<item>` must have the following attributes, each parseable as an integer:

| Attribute | Meaning |
| --- | --- |
| `id` | TOC item ID; canonical artifacts allocate IDs from `1` and match them to the chapter root `id` and `chapter_<id>.xml` |
| `page_index` | 1-based source-PDF page index containing the heading |
| `order` | 0-based position of the heading in that page's OCR layout |
| `level` | 0-based global TOC level; `0` is the highest level |

Together, `page_index` and `order` identify the heading layout from which the TOC item was generated. `level` also contributes to Markdown and EPUB heading depth, while XML nesting expresses the parent-child relationship.

The current v1 validator checks the root element, the integer list in `page_indexes`, all child element names, and the four required integer attributes of every item. It does not yet check that pages exist in `pages.xml`, that IDs are unique, that `level` agrees with nesting depth, or that an ID actually corresponds to a chapter. Producers must still preserve those relationships.

## `chapters/`

### Filenames and reading order

The directory may contain only two kinds of regular XML file:

- `chapter_head.xml`, which is optional and stores content before the first formal TOC chapter;
- `chapter_<decimal digits>.xml`, representing a formal chapter whose numeric component normally equals its chapter/TOC item ID.

Subdirectories, symbolic links, and other files are not allowed. The chapter directory may be empty. Readers process `chapter_head.xml` first, followed by the remaining chapters in ascending integer order of their filename's numeric component—not in ZIP member order or lexicographic order.

Canonical producers derive a filename directly from the chapter `id`, without leading zeros. The current validator checks only the filename pattern. It neither verifies that the filename number, chapter `id`, and TOC `id` are equal nor rejects distinct spellings that map to the same integer.

### Complete chapter example

This example includes body text, an inline equation, an image, and a footnote reference:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<chapter id="1" level="0">
  <body>
    <paragraph ref="title" level="0">
      <block page_index="3" order="0" det="180,210,2260,360">Chapter One</block>
    </paragraph>
    <paragraph ref="text">
      <block page_index="3" order="1" det="180,410,2260,620">The energy is <inline_expr kind="$">E=mc^2</inline_expr>.<ref id="3-1" /></block>
    </paragraph>
    <asset ref="image" page_index="3" det="400,700,2080,1800" hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">
      <caption>Figure 1: Illustration</caption>
    </asset>
  </body>
  <references>
    <ref id="3-1">
      <mark>1</mark>
      <paragraph ref="text">
        <block page_index="3" order="5" det="180,3200,2260,3370">Footnote text.</block>
      </paragraph>
    </ref>
  </references>
</chapter>
```

The image in this example also requires:

```text
assets/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png
```

### `<chapter>`

The root element must be `<chapter>`.

| Attribute | Required | Type and meaning |
| --- | --- | --- |
| `id` | No | Integer TOC item ID; omission identifies the head chapter |
| `level` | No | Integer chapter level; the internal value is `-1` when omitted, while a canonical formal chapter normally uses its TOC item's 0-based level |

Every chapter must contain a discoverable `<body>`. The canonical order is `<body>` followed by an optional `<references>`. Direct children of `<body>` appear in document order and may be `<paragraph>` or `<asset>` elements.

### `<paragraph>`

```xml
<paragraph ref="text" level="1">
  <block page_index="4" order="2" det="180,500,2260,760">Content</block>
  <block page_index="5" order="0" det="180,200,2260,430">Continuation on the next page</block>
</paragraph>
```

| Attribute | Required | Meaning |
| --- | --- | --- |
| `ref` | Yes | OCR layout-type string |
| `level` | No | 0-based heading level within the chapter; defaults internally to `-1` |

Backends treat both `ref="title"` and `ref="sub_title"` as headings, and `ref="text"` as regular body text. The format reader preserves other `ref` strings; renderers generally handle them as ordinary paragraphs.

For a heading paragraph, `level="0"` identifies the chapter's main heading, with larger values indicating successively deeper headings within the chapter. Non-heading paragraphs normally omit `level`. The final Markdown heading level also incorporates the chapter's `level` and is capped at six levels.

A paragraph contains zero or more `<block>` elements. A paragraph merged across pages or OCR layout regions contains multiple blocks, so source positioning belongs to each block rather than to the paragraph.

### `<block>`

| Attribute | Required | Type and meaning |
| --- | --- | --- |
| `page_index` | Yes | Positive source-PDF page index that must exist in `pages.xml` |
| `order` | Yes | Integer; the block's 0-based position in the page's OCR layout |
| `det` | Yes | `left,top,right,bottom`, bounded by the corresponding page |

A block is the smallest mapping unit between text and a source-PDF location. Its contents use XML mixed content: ordinary text can occur in the element's `.text` or a child's `.tail`, interspersed with the inline equations, footnote references, and HTML wrapper elements defined below. Element order is content order.

PDF patching processes blocks only in paragraphs whose `ref` is `text` or `sub_title`. It uses `page_index`, `det`, `order`, and the page geometry from `pages.xml` to position replacement text. Other renderers still consume all paragraph content.

### `<asset>`

```xml
<asset
  ref="table"
  page_index="6"
  det="220,600,2200,1700"
  hash="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
>
  <title>Table 1</title>
  <content><table><tr><td>A</td></tr></table></content>
  <caption>Data source</caption>
</asset>
```

| Attribute | Required | Meaning |
| --- | --- | --- |
| `ref` | Yes | Must be `image`, `table`, or `equation` |
| `page_index` | Yes | Positive source-PDF page index that must exist in `pages.xml` |
| `det` | Yes | The asset's bounding box on that page, bounded by the page dimensions |
| `hash` | No | A 64-character lowercase hexadecimal name referring to `assets/<hash>.png` |

An `<asset>` may contain zero or one `<title>`, `<content>`, and `<caption>`, in that order. All three use the same mixed-content model and HTML wrappers as a block. They may contain `<inline_expr>`, but not a footnote `<ref>`.

Backends interpret each `ref` as follows:

| `ref` | `content` | Purpose of `hash` and its PNG |
| --- | --- | --- |
| `image` | Usually empty | The image itself; renderers omit the image body when no hash is present |
| `table` | An HTML table when OCR recovered its structure | A screenshot of the table; EPUB falls back to it when usable HTML is unavailable |
| `equation` | The equation body in LaTeX | An optional screenshot of the equation region; normal Markdown/EPUB equation rendering uses the body |

Whenever `hash` is present, the current validator requires the corresponding PNG for every asset type. `title` and `caption` are renderable text before and after the asset. Markdown can render a table with structured `content` and no hash. The current EPUB renderer first requires a table hash even if it ultimately uses the HTML table content, so it omits a hashless table.

### Inline equations: `<inline_expr>`

`<inline_expr>` may occur in blocks and in an asset's title, content, or caption:

```xml
<inline_expr kind="\(">x^2+y^2</inline_expr>
```

The required `kind` attribute accepts only:

| `kind` | Meaning/restored Markdown delimiters |
| --- | --- |
| `text` | Ordinary text carried by an expression node |
| `$` | `$ ... $` inline equation |
| `$$` | `$$ ... $$` display equation |
| `\(` | `\( ... \)` inline equation |
| `\[` | `\[ ... \]` display equation |

The element text is the content inside the delimiters and does not include the delimiters themselves. An unknown `kind` causes chapter decoding to fail.

### HTML wrapper elements

Text mixed content may nest the following HTML elements to preserve Markdown/HTML structure recovered by OCR:

```text
div p blockquote details summary figure figcaption
h1 h2 h3 h4 h5 h6
b i strong em small mark s strike abbr cite dfn kbd samp var code pre tt
q bdo ins del sup sub span
ol ul li dl dt dd
table thead tbody tfoot tr td th caption
img picture source video
a br hr time wbr ruby rt rp
```

These elements may in turn contain ordinary text, other allowed HTML wrappers, and any payload allowed by the surrounding context: `inline_expr`, plus `ref` within a block. HTML element names are matched case-insensitively and decoded to their canonical lowercase names.

When pdf-craft creates these nodes from OCR Markdown, it filters attributes and URL schemes through a GFM-style allowlist; a canonical producer does not emit event-handler attributes, for example. The archive reader currently recognizes HTML wrappers by element name only and does not filter their attributes again. XML attributes on a recognized HTML element are preserved and emitted by renderers. Opening a third-party `.pcex` must therefore not be treated as HTML sanitization; rendered output still needs the security policy appropriate to its host environment.

### Footnotes and references

In a body block:

```xml
<ref id="3-1" />
```

points to a definition with the same ID in that chapter's `<references>`:

```xml
<references>
  <ref id="3-1">
    <mark>1</mark>
    <paragraph ref="text">...</paragraph>
    <asset ref="image" ...>...</asset>
  </ref>
</references>
```

The ID must have the exact form `<page_index>-<order>`, with both components parseable as integers. Here, `order` is the 1-based reference sequence assigned as pdf-craft extracts footnotes from that page; it is not a body block's 0-based OCR layout order.

Every reference definition must contain a `<mark>` with text, preserving the footnote marker that appeared in the body, such as `1`, `*`, or `①`. It may then contain any number of paragraphs or assets representing the footnote body. Paragraph blocks inside a reference retain their own page index, OCR order, and bounding box.

Every `<ref>` in body text must resolve to a definition in the same chapter or the chapter is invalid. A reference body may not nest another footnote `<ref>`. The canonical encoder writes only definitions that are actually referenced by body text, sorts them by `(page_index, order)`, and renumbers them uniformly during Markdown and EPUB rendering.

The current decoder builds an ID map but does not separately reject duplicate definitions; producers must ensure that reference IDs are unique within a chapter. The page-index component embedded in a definition ID is not currently cross-checked with `pages.xml`, although page indexes and bounding boxes on its child layouts are validated normally.

## `assets/`

Every asset filename must match exactly:

```text
[0-9a-f]{64}.png
```

A canonical producer first encodes the cropped region as PNG, then names it with the lowercase hexadecimal SHA-256 digest of the complete file bytes. Identical bytes reuse the same file, so multiple asset elements may refer to one hash.

The directory may contain only regular files—no subdirectories, symbolic links, or other extensions. The current validator checks filename syntax and verifies that every chapter `<asset hash="...">` has a corresponding file. It does not recompute the file content's SHA-256 digest or confirm that the file decodes as PNG. Unreferenced assets with valid names are currently allowed.

## `cover.png`

The cover is an optional root member. With `ExtractionOptions(includes_cover=True)`, pdf-craft attempts to save the original page image obtained while OCR processes the first page. If no usable image is available, it does not create this member.

The Markdown renderer copies the cover into the output assets directory but does not automatically insert cover syntax into the Markdown body. The EPUB renderer uses it as the EPUB cover. The current validator only requires this member to be a regular file; it does not validate its PNG contents.

## Cross-file invariants

A canonical `.pcex` must preserve the following relationships:

| Source | Target or constraint |
| --- | --- |
| Any chapter element with `page_index` | The page index must exist in `pages.xml` |
| `det` on the same element | The bounding box must fit within the corresponding `<page>` dimensions |
| `<asset hash="H">` | `assets/H.png` must exist, and H must be 64 lowercase hexadecimal characters |
| `<ref id="P-O">` in a block | A definition with that ID must exist in the same chapter's `<references>` |
| `toc/item@id` | Should correspond to both `chapter_<id>.xml` and its `<chapter id>` |
| `toc/item@page_index,@order` | Should identify the source location of the chapter's opening heading block |
| `chapter_head.xml` | Its `<chapter>` should omit `id` |
| `chapter_<id>.xml` | Its `<chapter id>` should equal the number in the filename |

The current validator enforces the first three relationships and body-reference resolution. TOC/chapter IDs, filename/chapter IDs, and the page component embedded in a reference-definition ID remain producer responsibilities.

## Archive and security constraints

A `.pcex` is a regular ZIP archive, written by pdf-craft using Deflate compression. It has no additional magic number, MIME member, archive-wide signature, or archive-level checksum. Format identification relies on both the `.pcex` filename and the ZIP contents.

The archive is unencrypted ZIP. The format itself provides no password protection, access control, or other confidentiality mechanism. It can contain the complete OCR text, bibliographic metadata, page and bounding-box mappings back to the source PDF, image/table/equation assets, and a cover. Protect a `.pcex` with the same sensitivity as its source document when copying, uploading, storing, or sharing it, and apply appropriate storage permissions and transport encryption outside the format.

Before extraction, the loader checks:

- the ZIP structure and member CRCs for corruption;
- duplicate ZIP member names;
- that member paths are relative, normalized POSIX paths;
- the absence of `..`, backslashes, absolute paths, and symbolic links;
- that only defined root members, chapter files, and asset files are present.

It then extracts into a temporary directory and validates the contents. Before `open()` returns, it creates and retains a validated snapshot; later reads no longer depend on the source archive's contents. The object returned by `export()` materializes the newly written archive lazily on first use. Temporary directories and materialization timing are implementation details and must not be discovered or relied upon by callers.

The current implementation imposes no limit on archive size, expanded size, or compression ratio, and it has no content signature. For an untrusted source, callers should enforce file-size and provenance restrictions before passing the archive to pdf-craft. Format versioning provides structural compatibility only, not authenticity or tamper protection.

## v1 validation details

`PDFCraftExtraction.open()` immediately performs these checks:

1. The path is an existing regular file with a `.pcex` suffix.
2. The ZIP is readable, paths are safe, member names are unique, and the member set is supported.
3. `manifest.json` and `pages.xml` exist and satisfy their field constraints.
4. After extraction, `chapters/` and `assets/` exist and contain only regular files with valid names.
5. Optional `toc.xml` and `cover.png` members have the correct member type.
6. Every chapter XML and the optional TOC XML parse successfully, have the expected root, and expose decodable core fields.
7. Chapter page references, bounding boxes, and hashed asset references are valid.

The current v1 validation contract does not include:

- correspondence between TOC page indexes and `pages.xml`;
- uniqueness or correspondence among TOC IDs, chapter filenames, and chapter root IDs;
- bounds or sequential continuity for `order` and `level`;
- uniqueness of reference-definition IDs or existence of the page encoded in an ID;
- agreement between asset contents and the SHA-256 digest in the filename;
- validity of PNG file contents;
- detection of unreferenced assets;
- every unknown attribute, duplicate optional child, or semantically unused extra structure in chapter XML;
- ZIP-bomb limits, digital signatures, or source authentication.

Chapter XML is currently checked by an object decoder rather than an XSD. Required fields, decoded payloads, and page mappings are validated, but some unread extra structures may be ignored and may disappear after transformation. When generating `.pcex` files independently, do not treat these unchecked cases as extension points or permission to violate the format. Backends guarantee interpretation only for structures listed in this reference.

## Analysis-workspace boundary

When `analysing_path` is provided, the workspace for a PDF extraction may resemble:

```text
analysing/
├── extraction/                 # internal, unpacked PDFCraftExtraction representation
│   ├── manifest.json
│   ├── pages.xml
│   ├── chapters/
│   ├── assets/
│   ├── toc.xml
│   └── cover.png
├── ocr/                        # diagnostics/checkpoint cache; not part of PDFCraftExtraction
└── plots/                      # optional diagnostics; not part of PDFCraftExtraction
```

To avoid a pointless ZIP round trip, a complete conversion uses `analysing/extraction/` directly inside the process. This does not make a directory a public input format. For a separately invoked backend, users must still pass a `.pcex` file or an already opened `PDFCraftExtraction` object.

Backends must not depend on `ocr/`, `plots/`, or any other analysis data. For long-term storage or transfer across processes or machines, use `extraction_path` to obtain a `.pcex` instead of retaining the complete analysis directory.

## Additional requirements for PDF patching

A `.pcex` stores text positions but neither source-PDF pages nor a hash or other identity for the source PDF. When calling:

```python
craft.patch_pdf_with_extraction(
    "original.pdf", "translated.pcex", "translated.pdf"
)
```

the caller should supply the same source PDF from which the extraction was produced. Before patching, the current implementation confirms that:

- `pages.xml` is not empty;
- no page index recorded by the extraction exceeds the input PDF's page count;
- every paragraph block in every chapter can obtain its page geometry from `pages.xml`.

It cannot prove that the input PDF and extraction came from the same source file. If page counts match but page contents or ordering differ, text may still be written to the wrong locations. Rendering Markdown or EPUB does not require the source PDF.

## Version compatibility

The current format version is `1`. The reader accepts only `format_version: 1` in `manifest.json` and does not attempt a best-effort downgrade for unknown versions. A v1 reader also rejects a new optional ZIP member or manifest field, so structural extensions must ship with a new format version and corresponding reader support.

Applications that only need downstream rendering or translation should let `PDFCraftExtraction.open()` perform version and integrity checks. A ZIP that can merely be extracted is not necessarily a usable PDFCraftExtraction.
