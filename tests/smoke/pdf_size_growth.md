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
- Output size at most 1,164,596 bytes: **200% of the input**, ratio at most 2.0.
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


## Fix and 200% acceptance

The decisive change removes source vector fills completely concealed by an
existing opaque erasure rectangle, before merging that rectangle and translated
text. A one-point inset preserves antialiased mask edges. Bounding boxes include
Bezier control points and the current transformation matrix; strokes, clipping
paths, images, text, shadings, and Form XObjects are not pruned. Unsupported
interleaving and malformed graphics-state stacks are left unchanged.

Page-local writers prevent merged paths from accumulating in the source reader.
Streams (including ICC resources) are compressed before window assembly; final
resources are deduplicated and window readers released. Annotation edges are
filtered before cloning, and destinations are rebound for translated and raw
pages without importing hidden source page trees. pypdf is upgraded to 6.21.4.

The first fresh paid run with these fixes produced **769,045 bytes (1.320707×)**
in **64.820 seconds**, with all three pages and translated text present. The
200% limit is a fixed-fixture acceptance criterion, not a general guarantee for
every PDF. This smoke does not measure the full-book peak RSS or prove the
original 4 GiB instance can run the whole book.

A separate same-output diagnostic pruned only the original paths under the
existing masks: its 771,734-byte PDF rendered identically to the unpruned output
at 100 DPI on all three pages (pixel comparison, not merely page counts).
The initial 0.02-point inset had a two-pixel difference; the final one-point
inset removed that difference. No rasterization or image-quality reduction is
used to meet the size limit.


A second independent fresh paid run produced **771,370 bytes (1.324700×)** in
**57.768 seconds**, also passing the 200% gate with all pages and Chinese text.
Both runs used the final production code and separate OCR/LLM cache directories:
`pdf-craft-output/size-growth-under-2x-01/` and
`pdf-craft-output/size-growth-under-2x-02/`. Offline verification: **1,275 tests
passed**, pyright passed, and pylint passed (10.00/10).
