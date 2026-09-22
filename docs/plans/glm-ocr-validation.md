# GLM-OCR implementation validation

## Implementation and release state

- PDF Craft branch: `feat/glm-ocr-apple-silicon`.
- Upstream adapter: `4603382`,
  [doc-page-extractor PR #104](https://github.com/Moskize91/doc-page-extractor/pull/104).
- Runtime instructions: [Apple Silicon setup](../en/GLM_OCR_APPLE_SILICON.md).
- No dependency release was invented or published. The PDF Craft branch requires
  the documented explicit upstream development checkout until an adapter release
  can be selected and the dependency minimum/lockfile updated.
- Separate delegated reviews of the upstream adapter and PDF integration found
  no blockers. Small raw-Markdown fallback and hostname-validation findings were
  addressed with focused tests. No PR is authorized for automatic merge.

## Deterministic checks

Using a worktree-local Python 3.12 environment with an explicit editable install
of the paired upstream checkout:

- Upstream `python test.py`: **38 tests passed**.
- PDF Craft focused tests (`test_glm_ocr_config.py`, `test_ocr_config.py`,
  `test_page_extractor_structured.py`): **33 passed**, plus 16 subtests.
- Changed-code Pyright and pylint passed in both repositories. PDF Craft Pyright
  must select the worktree interpreter (`--pythonpath .venv/bin/python`); invoking
  the binary alone selected a different environment and could not resolve even
  existing PIL/upstream imports. No file-wide diagnostic suppression was added.
- Active LSP checks found no code errors after correction; one repeated clean
  check was inconclusive because its server did not publish a fresh result.
- PDF Craft full `python test.py`: **551 run, 1 failure, 7 skipped**. The same
  failure occurs on exported `main` (`2cac9de`, 544 run):
  `test_native_text_is_kept_when_ocr_has_no_matching_box`. The installed Homebrew
  Poppler 26.04.0 `pdftotext -bbox/-bbox-layout` aborts with `std::out_of_range`.
  This is a baseline/environment issue, not a passing full-suite result.
- Markdown local links, fences, whitespace, and the sample SDK YAML's resolved
  CPU/retention/merge settings were checked.

Normal tests do not launch services, download weights, or require a GPU. The
full suite preceded the final small adapter/validation changes; the final focused
and upstream checks cover those changes. No end-to-end CI job was introduced.

## Local model and end-to-end observations

Host: Apple M4 Pro, 48 GB unified memory, macOS 26.5.1. Runtime versions:
MLX-VLM 0.7.2, MLX 0.32.2, Transformers 5.17.0, SDK source
`cef4d0ea120d1741f5cefe8985eee45f6c8eff1d` (declared 0.1.5).

- Metal device telemetry and successful model inference were recorded.
- Layout ran explicitly on CPU. Both SDK and MLX services used loopback only;
  MaaS was disabled. A per-process Python socket/DNS guard rejected non-loopback
  connections after models were cached; this was not a host-wide firewall test.
- A synthetic 1800 x 2600 page produced 23 structured regions, including a table,
  chart, captions, title, and footnote. Native labels and normalized coordinates
  survived the service boundary and became in-bounds pixel boxes. A sanitized
  real response is committed in the upstream test fixtures.
- A two-page PDF passed extraction, PCEX validation, Markdown and EPUB rendering.
  Asset/fragment boxes were in bounds; table/image assets and output text were
  nonempty. Re-rendering PCEX after stopping both services succeeded.
- The final smoke used a glyph-verified Arial Unicode `①` reference pair. OCR
  produced inline `\textcircled{1}` and no footnote layout, so rendered footnote
  preservation still did **not** pass. No fake reference was inserted.
- A synthetic equation was missed by layout detection. Formula mapping is unit
  tested, but real formula quality remains unproven.
- Earlier notes using plain ASCII `1` or no paired marker were omitted by the
  existing chapter/reference contract, not lost by the adapter. No renderer or
  public extraction-schema change was made to override that contract.
- Native furniture extraction was disabled for smoke tests because of the
  unrelated Poppler crash. EPUB validation was structural, not visual.

Ignored local evidence is under `pdf-craft-output/glm-validation/`, including
`REPORT.md`, `REPORT-followup.md`, `REPORT-e2e.md`, `REPORT-e2e-final.md`, responses,
service configuration, timing/device telemetry, and output artifacts. These are
machine-local evidence paths, not files required by ordinary test runs. The
runtime environments/models are under `/tmp/pdf-craft-glm-runtime`; services were
stopped after validation.

## Remaining acceptance work

1. Upstream adapter review/release and PDF Craft dependency minimum/lock update.
2. Representative real-book quality checks, including footnote-marker recognition,
   equations, blank/degraded pages, and rotated or complex layouts.
3. Visual EPUB-reader review and separate translated-PDF geometry/quality checks.
4. Long-document, lower-memory, oldest-supported-macOS, and concurrency validation.

The result is a working experimental local backend, **not** a claim that every
quality gate in the implementation plan has passed.
