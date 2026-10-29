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
- Output size at most 4 MiB and output/input ratio at most 8.
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
size gate intentionally stays red until the composition implementation is fixed.

As a diagnostic only, applying pypdf content-stream compression and identical
object deduplication to the first output reduced it to 3,557,520 bytes (6.109×).
It still had three pages and Chinese text on every page. This supports the size
budget; it is not a production fix, visual-equivalence proof or evidence that
the final-merge memory peak has been reduced. No post-processing is performed
by this gate, so it cannot hide the production bug.
