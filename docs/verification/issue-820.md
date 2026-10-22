# Cross-page anchors and extraction recovery (#820)

## Deterministic reproduction

`tests/assets/analysis/anchored_cross_page/` retains the relevant shape of the
six-page DeepSeek-OCR report: a sentence ends on one page, a figure starts the
next page, then the sentence continues. The fixture is small OCR XML, without
model credentials or generated binary artifacts.

The failure used to have two layers: the joiner moved the figure after the
merged paragraph, and page projection then placed it back between text layouts
without reconciling continuation semantics. Compilation propagated an incoming
text edge onto the image. Both refined reconstruction and its traditional
fallback raised `Asset layouts cannot carry continuation gaps`.

The fixed paragraph is one ordered `TextFlowItem` with a page-1 fragment,
page-2 image and page-2 fragment. Image/table anchors have no continuation flags;
text predecessors skip them. Display formulas still end a continuation sequence.
Projection, refinement validation, model instructions and restoration share
this rule. Explicit refinement splits are not re-inferred by final assembly.
The same handling applies to footnote text.

`tests/test_anchored_page_recovery.py` covers this shape, multiple same/cross-page
assets, formulas, footnotes, identity/failed refinement, codec/source geometry,
model boundary context, and public extraction failure paths. The latter include
live production, analysis preparation and finalization, precise/conservative
recovery scope, cross-page dependency closure, image/placeholder recovery,
strict decisions, callback errors, cancellation, storage errors, late all-fallback
accounting and unchanged OCR caches/previews.

## Last recovery boundary

The extraction orchestrator owns image recovery; the footnote model adapter
cannot render a PDF page. It separately requests `analysis/page_image` after
an unusable traditional result. Rendering failure requests
`pdf_render/page_placeholder`. Surviving contiguous source runs are rebuilt
without connections across replaced pages. Their chapter records are spooled
to disk; no second whole-book chapter list is accumulated. Unknown or unusable
scope information conservatively expands recovery, rather than trusting a
broken graph. Each surviving run is attempted once. Original OCR cache files
are not rewritten and old content callbacks are not replayed.

Re-rendered fallback images use the existing OCR pixel dimensions when known,
so pages.xml, furniture coordinates and source geometry retain one basis.
Asset write/publication errors are not misclassified as render failures.
Final accounting includes OCR-successful pages that later degraded. If no
primary page survives, extraction raises `NoUsableOCRPagesError` rather than
publishing an all-fallback result as success.

The real run also exposed an existing first-item race in the shared translation
adapter when furniture supplied a pretranslation mapping. `TextFlowItem` was
imported in the output loop, after a producer could use the uninitialized
closure. It is now a module import; a first-item regression covers it.

## Real-resource run / independent review command

From a configured worktree, use a **new** output directory:

```sh
.venv/bin/python -m pdf_craft_tool.smoke.deepseek_refined \
  /private/tmp/pdf-craft-260-deepseek-ocr-report/DeepSeek-OCR.pdf \
  --output pdf-craft-output/deepseek-refined-review-NEW-RUN
```

The private tool uses the current worktree source and `.env` profiles, obtains
OOMOL LLM configuration in memory when configured, verifies the exact original
PDF hash/size/page count, and calls real DeepSeek OCR vendor, JEV and LLM
resources. It enables refined footnotes, TOC LLM, cover/furniture and extraction
previews; disables book metadata extraction; uses RetryPolicy(8,6,12) and
recoverable error callbacks. It runs extraction → PCEX → zh-CN replacement
translation → PDF, then renders every source/output page for visual review.
No old OCR cache is reused. The tool deliberately leaves visual acceptance
pending: inspect all six page pairs rather than accepting page count alone.
Do not run this paid workflow as an ordinary unit test.

Implementation run on 2026-10-22:

- Original report: `/private/tmp/pdf-craft-260-deepseek-ocr-report/report.md`.
- SHA-256: `dd5cec9ee94ebf4fa755a9e8d6155d83156df50af0294495910f5ac2d3cde5ea`;
  1,234,259 bytes, six pages.
- Output: `pdf-craft-output/deepseek-refined-implementation-3/translated.pdf`.
- Sanitized summary: same directory `summary.json`; decisions: `failures.json`.
- Full local run log: `/private/tmp/vge820-real-run3.log`.
- Visual evidence: `previews/source-{1..6}.png`, `translated-{1..6}.png`, and
  implementation comparison images `comparison-{1..6}.png`.
- The process completed. All six comparison images were opened and inspected:
  no missing, duplicated or wholly blank pages; Figure 4 and the table remain.
  PCEX `chapter_11.xml` contains fragment(page=5,order=7), asset(page=6,image),
  fragment(page=6,order=1) in one text node.
- Local quality observations: two fill failures (13 attempts each, pages 1/3)
  preserve source English; a page-3 paragraph is unusually small; page 1 has
  minor residual author-block marks. Page 4's Figure 2 area is blank: the raw
  vendor OCR marks folio `4` with a full-page bbox `(0,0,2474,3497)`, and that
  source participates in a numeric paragraph with page 5's folio. Its PDF
  replacement erases the illustration region. These observations are recorded,
  not hidden by page-count checks. The requested gate tolerates local model/OCR
  quality variation and does not require every region to be translated.

The implementation run is evidence, not a substitute for Review's independent
fresh real-resource run and visual inspection. Generated PDFs, caches, logs,
previews and credentials remain outside version control.

## Automated verification

On the implementation worktree:

- `.venv/bin/python -m pytest -q --disable-warnings`: **986 passed** (64 warnings).
- `.venv/bin/python -m unittest -q tests.test_anchored_page_recovery`: **20 passed**.
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python pdf_craft tests pdf_craft_tool/smoke/deepseek_refined.py`:
  **0 errors / 0 warnings**.
- `.venv/bin/python -m pylint pdf_craft tests pdf_craft_tool/smoke/deepseek_refined.py`:
  **10.00/10, exit 0**; existing informational Qt extension introspection notices.
- `git diff --check`: clean.

## Review follow-up: asset-only intermediate pages

Review identified a recovery-scope omission in `fd7a74d`: a logical paragraph
`fragment(page1) → image(page2) → fragment(page3)` was reconstructed correctly,
but its graph links only included pages 1 and 3. The raw-page probe discarded
the text predecessor at page 2, so neither path closed over the complete unit.

The graph probe now collects transparent asset pages between continued text
nodes and includes them in the same dependency edge. The raw-page probe keeps
the last text page and all intervening image/table-only pages until the next
text determines the continuation. Empty pages, headings and display formulas
still stop the run. Caption-only asset pages do not erase the text predecessor.

`tests/test_asset_page_recovery_scope.py` adds five regression methods with
image, table and multiple consecutive asset-page scenarios. Each text endpoint
and intermediate asset page is exercised as the failure target. Both graph and
raw scopes must include the entire paragraph; actual `recover_chapters` output
and the public extraction API without refinement must replace all those pages
with images while retaining the following independent paragraph. A display
formula negative case ensures unrelated pages are not included.

The deterministic baseline/fixed probe is stored locally at
`pdf-craft-output/issue820-scope-followup/probe.json`: ten target/scenario pairs
reproduce the pre-fix omission and yield the full component after the fix.
This follow-up changes recovery scope only; it does not change the successful
conversion path. Review's independent real-resource six-page run and visual
evidence from `pdf-craft-output/deepseek-refined-review-20261022/` remain
available. No new model calls were made for this scope-only correction.

Follow-up checks: full pytest **991 passed** (64 warnings); focused scope and
anchor/recovery tests **25 passed**; pyright **0 errors / 0 warnings**; pylint
**10.00/10**; `git diff --check` clean.
