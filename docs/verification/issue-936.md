# Downstream paid-smoke regressions (#936)

The source run is `paid-full-723f884-5fgqkhft` from main `723f884`.
This change covers translation transport, generic XML submission, canonical fill,
and ordinary PDF single-line normalization. OCR/form interpretation and JEV replay
are handled separately.

## Offline checks

```sh
poetry run python -m pytest tests/test_downstream_smoke_regressions.py tests/test_request_batch.py tests/test_structured_translation.py tests/test_canonical_math_allocation.py tests/test_pdf_windowed_layout.py tests/test_xml_repair_loop.py
poetry run python -m pytest -q
poetry run pyright --pythonpath .venv/bin/python pdf_craft tests
poetry run pylint pdf_craft tests
```

The small regression cases preserve the important failure shapes without shipping
private smoke output: empty table cells with text in their tails; malformed and
partial envelopes; more canonical formulas than source slots; mixed reference and
literal formulas; a cosmetic gap across slots belonging to one semantic unit;
and the page/box geometry of footnote 41. Unknown expression IDs still fail.
Unchanged names/quotations are accepted without a semantic quality retry loop.

Translation envelope retries consume `RetryPolicy.translation`, resend the
original source, and leave accepted siblings alone. With `LLM.failure_log_path`
configured, exhausted units expose `failure_log_id` and retain their actual
batched request/response even when the response cache is rolled back. Whitespace
only units preserve their original XML without provider work.

## Local real-provider evidence

Implementation evidence and scripts are in the worktree's ignored directory
`pdf-craft-output/issue-936/`. They reuse the saved PCEX inputs, not OCR.
`probe.py` translates the 38 Unlimited narrative units using real production
translation/fill and records exchanges plus validation feedback. `asset-probe.py`
replays the original asset `(1, 32, -1)` without network and compares XML structure.
`paid-downstream.py` exercises the native Little Prince EPUB and writes citation
and PDF-size outputs from saved PCEX with fresh request caches. Failure captures
from intermediate investigations remain available as evidence, not passing runs.

The source data remains at the absolute directory documented in the VGE issue.
These private materials and generated PDFs are deliberately excluded from Git.
The full paid matrix is reserved for Origin acceptance; targeted paid success
is not a guarantee that arbitrary model outputs will always pass.

Successful implementation runs: `after/result.json` (38 Unlimited units),
`prince4/result.json` and `prince4/readability.json` (complete readable EPUB),
`citation/result.json` and `pdf-size/result.json` (final PDF outputs), and
`offline-asset-structure-diff.json` (zero structural differences).
A copy of the evidence is preserved alongside the original smoke data at:

```
/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/pdf-craft-output/paid-full-723f884-5fgqkhft/issue-936-verification
```
