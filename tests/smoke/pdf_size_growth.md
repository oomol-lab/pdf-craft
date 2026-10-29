# PDF size growth paid regression

Run from the pdf-craft repository:

```shell
poetry run python -m pdf_craft_tool.smoke.pdf_size_growth \
  --output pdf-craft-output/pdf-size-NEW-RUN
```

This opt-in gate uses the same real OCR, refined footnotes, translation and PDF
fill pipeline as the existing citation paid regression. It is not a unit test
or an entry in the default CI suite. The input is bundled; no customer token,
full book, service repository or temporary reproduction directory is required.
Credentials come from the worktree `.env` and the selected LLM profile.

## Fixture provenance

`tests/assets/pdf/strategic-justice-size.pdf` contains PDF pages 150–152
(printed pages 132–134) of the user-supplied *Strategic Justice* PDF. Original:
341 pages, 15,744,863 bytes, SHA-256
`3da90a002a9ebb855eae765ecaf5eb7d27382732171a3e9c19b4cf92410ab8ea`.
The three-page fixture is 582,298 bytes, SHA-256
`248f733685f35263026d1d8c6afff07adfa23f0e3d9bcec8793cbe3d3884b3c4`.

Created with pypdf 6.21.4, without rasterizing, Ghostscript rewriting or merging
page content during trimming:

```python
from pypdf import PdfWriter

writer = PdfWriter()
writer.append(original_pdf, pages=[149, 150, 151], import_outline=False)
writer.compress_identical_objects()
writer.write(fixture_pdf)
```

The fixture contains exactly three page objects, so it does not conceal the
whole source book in an annotation reference. It retains the original vector
content and shared color resources. It reproduces content/resource expansion;
it is not a reproduction of the original page-5 dangling link or every cause
of the full-book OOM.

## Acceptance

Require a fresh output directory and the exact pinned fixture. The production
pipeline runs with `strict=True`: no accepted recovery, original-page fallback
or missing translation can count as a clean pass. On completion require:

- Three output pages, each containing extractable Chinese text.
- Output size at most 873,447 bytes: **150% of the input**, ratio at most 1.5.
- Pixel-identical preservation of the first-page chart in the fixed 100-DPI
  preview crop (220, 100, 405, 245); missing artwork fails even if size passes.
- Manual comparison of all source/output previews before claiming full visual
  acceptance. Chinese text checks alone do not prove complete translation.

The thresholds are fixture-specific, allowing font embedding overhead. They
must not be generalized into a size limit on users' PDF files. Size failure
returns a nonzero exit status and retains `size-checks.json`; upstream failures
retain a report with `pipeline_completed=false` and `automated_passed=false`.
Ordinary offline tests exercise the acceptance oracle without model calls.

## Initial reproduction

On the unfixed composition code at `e6fd914` (pypdf 6.21.4), two independent
fresh paid runs reproduced the signal without sharing OCR or LLM caches:

| Run | Full pipeline time | Output bytes | Output/input |
| --- | ---: | ---: | ---: |
| Initial probe | 59.630 s | 11,042,570 | 18.964× |
| Final smoke entry | 61.891 s | 11,043,814 | 18.966× |

The final entry exited 1 with `pipeline_completed=true`: page count and Chinese
text checks passed, while both size checks failed. The initial probe used the
same paid pipeline but predated the gate's acceptance wrapper. Local evidence
is in ignored `pdf-craft-output/size-growth-paid-01/` and
`pdf-craft-output/size-growth-paid-02/`; these are evidence paths, not runtime
dependencies. Ordinary unit tests and CI remain green while the opt-in paid
size gate correctly rejected both unfixed runs.

As a diagnostic only, applying pypdf content-stream compression and identical
object deduplication to the first output reduced it to 3,557,520 bytes (6.109×).
It still had three pages and Chinese text on every page. That intermediate
6.109× result was rejected as excessive; it is not a production fix,
visual-equivalence proof or evidence that
the final-merge memory peak has been reduced. No post-processing is performed
by this gate, so it cannot hide the production bug.


## Current fix and acceptance

The default fill no longer converts the source page's text to outlines. It keeps
source fonts and graphic objects and deletes each original character whose box
overlaps or touches an actual padded erase rectangle, including invisible OCR
text. Deleted character advances become PDF positioning instructions, preserving
unmasked character positions. Shared/nested Forms are edited per invocation;
associated marked-content replacement text is removed. Visual Stamps are
flattened without rewriting the other page artwork.

pdfminer.six (MIT) supplies font/CMap geometry and pypdf writes the original
encoded character bytes. No PyMuPDF dependency or full-page rasterization is
introduced. Text used as a clipping path currently raises a page-composition
error if hit: silently deleting its clipping effect would change graphics.
Existing error policy applies; strict smoke rejects any such fallback.

Page-local writers, stream compression, resource deduplication and released
window readers remain. Annotation references are filtered before cloning and
rebound without importing hidden page trees. pypdf is upgraded to 6.21.4.
The previous concealed-vector pruning implementation is superseded: graphics
are no longer pruned to meet this gate.

The size gate does not prove complete visual correctness or measure full-book
peak RSS. In particular, retaining three pages with Chinese text cannot detect
missing source artwork: compare all three previews, including the first-page
chart. Existing paragraph/footnote layout issues are separate from this gate.

The accepted character-removal run (`size-growth-text-removal-02`) produced
**814,760 bytes / 582,298 bytes = 1.399215× (139.9%)**, in **54.537 seconds**.
All three pages contain translated Chinese, and the source chart survives.
The previous `text-removal-01` run is **rejected** despite passing automated
size checks: visual review caught missing source artwork, fixed before run 02.
The preview still exhibits paragraph/font-size and footnote layout issues; this
result establishes the size regression fix, not complete layout acceptance.

The retained chart crop is pixel-identical to the source at 100 DPI. The paid
gate now checks that crop automatically; it rejects the blank-artwork run 01.
