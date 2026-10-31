# Source-box overlap no longer rejects whole PDF pages

## Behavior

VGE #921 takes over the engine investigation from
[pdf-craft-service #268](https://github.com/oomol/pdf-craft-service/issues/268).
An eligible source text box used to trigger `source_geometry` when its area
exceeded four times another source object's area and covered over 80% of that
object. The guard then closed over paragraph ownership, preserving every
connected page without attempting replacement. These ratios do not establish
that the source geometry is incorrect.

The guard, its SQLite preflight and its private page-preservation hook are
removed. Trusted narrative and furniture translations proceed through ordinary
layout and composition. Layout obstacles, missing-translation coverage,
validation and composition error policies, cancellation, atomic publication and
all-primary-fill-failed accounting remain in effect. No replacement ratio or
new whole-page overlap gate is introduced.

This supersedes the source-geometry policy described in the historical
[issue-820 investigation](issue-820.md) and
[OCR redaction follow-up](ocr-redaction-1.2.2.md); their recorded historical
observations are unchanged.

## Retrieved production evidence

On 2026-10-31, all four incidents in the original production triage window were
located through the `oomol-developer` / `prod` SLS connection. Their complete
diagnostic archives were downloaded using the `prod-developer` STS connection.
They contain the original PDF and the pre-translation PCEX. No production job,
paid OCR or paid translation was rerun.

The source inputs, archives, logs, checksums, local replay script and visual
comparisons are retained privately at:

```text
/Users/taozeyu/Downloads/pdfcraft-investigations/issue-268-source-geometry/
```

Do not commit or upload these customer documents to a public repository. The
directory's `inventory.json` identifies retained files by byte size and SHA-256.
The representative files are `original.pdf`, `prepared.pdf`, `source.pcex` and
`diagnostics.zip`. Original and prepared PDF bytes match in this incident:

```text
prepared.pdf  7,055,495 bytes
b7e2b3e9cbdf8f6c6ab86022d3cbe1d5371e34ad740471d9a74526f55ea0155b

source.pcex  237,164 bytes
96d2859ca5f49c7e35c92d0901edef230d04980493243872e617841ce41b529b

diagnostics.zip  15,006,783 bytes
c5cf4ec90be85e0d5dd6ed21f2bf1640dea91316454b89edd6791b4f9326928c
```

Replaying the old guard against the original source identities, including
furniture, reproduced these scopes:

| Job | Directly hit pages | Preserved page closure | Final job outcome |
| --- | --- | --- | --- |
| `7eb50410-c46d-4578-8d20-12ae05410b36` | 31, 36, 37, 81 | 31, 32, 36, 37, 81 | Failed: balance exhausted |
| `200aae16-7ee0-4915-b393-6c124fe77534` | 40 | 40 | Failed: balance exhausted |
| `b81ef8f7-2c81-427b-97e8-6c4d8e72502f` | 40 | 40 | Succeeded |
| `f98dd342-570b-4c4e-8a77-400d72983ce3` | 7, 8, 166, 175 | 7, 8, 165, 166, 175 | Failed: balance exhausted |

Do not attribute the three balance failures to the geometry notice. These
notices were deliberately constructed `ValueError` recovery requests, not
unexpected Python crashes. No final customer output was obtained or evaluated
for the three failed jobs.

## Representative geometry and local composition

For the 100-page representative input, the five rejected boxes were:

| Page | Source bbox in OCR pixels | Writer |
| --- | --- | --- |
| 31 | `(1861,597,3046,1561)` | Narrative |
| 36 | `(1848,242,3036,1606)` | Narrative |
| 36 | `(1845,1606,3033,2208)` | Narrative |
| 37 | `(1934,1096,3016,1198)` | Narrative |
| 81 | `(2283,189,3020,235)` | Furniture |

All eleven covered objects participating in those comparisons were furniture
text. Page 32 was added only through cross-page paragraph ownership. Ignoring
furniture during reproduction would incorrectly produce no conflicts.

`replay.py` uses the original PDF and PCEX with unmodified source boxes and
identities. In a separate private workspace, it gives affected text a `LOCAL
BODY:` or `LOCAL FURNITURE:` prefix and explicit coverage, retaining the original
text to exercise realistic layout lengths. This is a deterministic composition
probe, not a historical or newly generated model translation. Other narrative
units retain missing coverage and do not become fill targets.

The public `AsyncPDFCraft.patch_pdf_with_extraction` entry completed under a
strict error policy. All 45 narrative probes produced actual line previews on
pages 31, 32, 36, 37 and 81; 47 furniture probes were also submitted. The 852
other narrative previews correctly contain no layout. The final PDF has 100
pages. This proves removal of the blocking behavior without paid requests.

All five `comparison-<page>.png` images were opened and inspected: original
page, original with triggering boxes in red and covered furniture in blue, and
the locally recomposed page. Text is now written on the formerly preserved
pages, including closure-only page 32. Marginal handwriting and page artwork
outside erasure rectangles remain visible. Duplicate native furniture overlaps
the OCR narrative in parts of pages 31, 36 and 37, and the probe exposes those
overlaps. It does not establish translation quality or resolve extraction
duplication. Inaccurate erase rectangles can still cover other content; this
change does not add content-aware erasure or claim otherwise.

Replay from the repository environment with:

```bash
.venv/bin/python /Users/taozeyu/Downloads/pdfcraft-investigations/issue-268-source-geometry/replay.py
```

The remaining three archives were used to reproduce the old scopes; their final
PDFs were not recomposed. Cloud-service dependency adoption and production
verification remain follow-up integration work; service #268 stays open.

## Regression checks

`tests/test_pdf_source_safety.py` now checks the requested behavior through
actual PDF composition: strict-policy cached and repeated rendering, live
translation with a local response stub, a furniture writer over narrative, and
transitively connected cross-page paragraphs. It verifies written text and real
previews, while missing coverage still has no invented placement.

All four new regression methods fail against the original committed guard with
`Text source bbox substantially covers unrelated source content`; they pass
with the change. Actual fill-failure recovery remains covered by
`tests/test_pdf_translation_recovery.py` and the patcher tests.

Checks on Python 3.11.15:

- Related source-overlap, translation-recovery, incremental-layout, text-layout
  and patcher tests: **91 passed**.
- Full pytest run: **1,376 passed, 1 skipped**.
- The concurrently merged OCR byte-limit suite: **17 passed** separately.
- Pyright with `--pythonpath .venv/bin/python`: **0 errors, 0 warnings**.
- Pylint over `pdf_craft tests`: **10.00/10**, exit 0.
- `git diff --check`: clean.
