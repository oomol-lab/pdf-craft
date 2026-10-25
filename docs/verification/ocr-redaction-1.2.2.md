# Page-sized footer OCR box: local regression

## Incident and change

DEV task `3a269966-d46c-401c-b3e5-634813befea2` translated a six-page PDF
into Thai, with refined footnotes and replace rendering. A page-number `4` on
page 4 had OCR bbox `(0,0,2474,3497)`. The PDF source-geometry guard rejected
that box and its paragraph closure preserved original pages 4–6.

`doc-page-extractor` 1.2.2 clips later-stage boxes against cumulative actual
redactions. pdf-craft now requires that version (including its local extra),
retains the exact masks alongside prepared vendor requests, and calls the helper
before constructing structured output in both asynchronous vendor paths.
Local OCR already uses the upstream extraction loop. First-stage OCR and raw
vendor diagnostics are unchanged. Erasure masks use Pillow inclusive endpoints;
layout boxes remain half-open.

## Retained real-evidence replay

Replayed all 20 previously captured two-pass OCR responses using locally
installed doc-page-extractor 1.2.2 and the updated pdf-craft vendor preparation
and response parser. These were actual captured responses, not generated model
responses. Recomputed second-pass input images matched the retained input PNGs
pixel for pixel in all 20 runs.

Actual inclusive masks: `(0,0,2481,2339)` and `(285,2339,2188,3157)`.
All 20 results retained text `4`, with corrected bbox `(0,3158,2474,3497)`.
The real digit's pixels occupy `(1228,3247,1249,3278)` and remain fully inside.

Separately replayed the historical PCEX through `unsafe_source_geometry`:

| Historical PCEX | Rejected boxes | Preserved pages |
| --- | --- | --- |
| Original | Page 4 `(0,0,2474,3497)` | 4, 5, 6 |
| Only the bad fragment bbox corrected | None | None |

No fragment was deleted in the corrected copy. This verifies removal of the
identified geometry fallback trigger. It is not a fresh full-book paid OCR,
translation, or final-PDF validation, and does not prove all conversion failures
are resolved. Original evidence and uploaded documents were not modified.

## Automated coverage

The vendor concurrency suite exercises both budgeted and unbudgeted async OCR
paths with a page-sized second-pass box, checking corrected layouts and
structured output. Upstream tests additionally cover the captured page geometry,
four-stage accumulated masks, resized inputs, mask-edge inclusivity, empty boxes,
and randomized exhaustive verification of largest-rectangle selection.
