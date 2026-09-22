# GLM-OCR on Apple Silicon: implementation plan

## Status and objective

**Status:** Proposed; no backend implementation or runtime validation yet.

Enable PDF Craft to extract scanned PDFs using GLM-OCR running locally on an
Apple Silicon GPU, while preserving its structured extraction contract and
existing Markdown/EPUB renderers. Initial integration should use an explicitly
configured local service, not embed a new ML runtime into the library.

This plan is based on repository source and upstream documentation. Model
inference, memory consumption, throughput, and end-to-end extraction quality
have **not** been measured on this machine.

## Scope

### Initial deliverable

- GLM-OCR recognition through MLX-VLM on Metal.
- CPU layout detection through the GLM-OCR SDK's PP-DocLayoutV3 pipeline.
- A structured-result adapter in `doc-page-extractor` and a closed configuration
  object in PDF Craft.
- Existing extraction, asset capture, Markdown, and EPUB workflows using the new
  backend without changing the public `.pcex` format.
- Explicit setup instructions, deterministic adapter tests, and an opt-in local
  smoke test using representative pages.

### Non-goals

- Porting existing CUDA backends to MPS or changing the default OCR backend.
- Training, fine-tuning, quantization, or optimizing layout detection on Metal.
- A generic user-supplied OCR factory or adapter in PDF Craft's public API.
- Automatically launching servers, downloading models during normal tests, or
  installing MLX/PyTorch as direct mandatory PDF Craft dependencies.
- Cloud fallback, UI work, or CI-hosted model/end-to-end tests.
- Redesigning translation or PDF text placement. Geometry must remain compatible,
  but translated-PDF quality is a separate follow-up validation surface.

## Evidence and constraints

| Finding | Evidence / implication |
| --- | --- |
| GLM-OCR has an Apple Silicon deployment path | Upstream documents MLX-VLM, Metal, macOS 14+, and `mlx-community/GLM-OCR-bf16`. |
| Recognition and layout are separate | The SDK performs layout detection, region cropping, recognition requests, and result formatting; a raw MLX completion is not a complete page layout. |
| Layout can run on CPU | The SDK documents `layout_device="cpu"` / `--layout-device cpu`; verify the complete dependency stack on arm64. |
| PDF Craft consumes structured blocks | `pdf_craft/pdf/page_extractor.py` reads block kind, text/HTML, children, and pixel bounding boxes, and clips source assets. |
| Backend construction is closed and lazy | `pdf_craft/ocr_config.py` defines supported configurations; `PageExtractorNode` creates an upstream extractor only when needed. |
| Upstream owns OCR adapters | `references/architecture.md` requires adding an official construction entry point in `doc-page-extractor` before mapping it in PDF Craft. |
| Runtime isolation is advisable | The MLX deployment guide describes conflicting Transformers requirements. Its installation advice may lag current releases; verify and record compatible versions rather than copying unpinned commands. |

GLM-OCR is documented as a 0.9B-parameter model. Upstream's statement that the
model fits in 8 GB is not a memory guarantee for macOS plus both services,
layout detection, page images, and concurrent requests.

## Proposed architecture

```text
PDF Craft: PDF rendering, extraction orchestration
  -> doc-page-extractor: GLM structured-result adapter
    -> localhost GLM-OCR SDK service: layout detection on CPU
      -> localhost MLX-VLM service: GLM-OCR inference on Metal
    <- ordered structured page results
  <- normalized upstream extraction results
PDF Craft: .pcex workspace -> existing Markdown / EPUB renderers
```

Use the SDK's structured parsing endpoint, not just MLX's chat-completions
endpoint, at the adapter boundary. HTTP transport compatibility alone does not
make GLM-OCR a replacement for a DeepSeek-specific backend.

### Ownership and isolation

- **MLX environment:** recognition model and MLX-VLM server.
- **SDK environment:** layout model, CPU execution, and full parsing service.
- **PDF Craft environment:** lightweight service adapter plus existing library
  dependencies. No import of GLM/MLX implementation types into business rules.
- Users manage service lifecycles explicitly. Bind services to loopback for the
  local recipe; do not expose unauthenticated endpoints to the network.
- Submit page image bytes using the endpoint's supported encoding, not arbitrary
  server-side paths. Resolve model downloads before offline use; disable hosted
  MaaS mode explicitly and never silently send pages to a cloud service.

### Proposed public configuration

Working name: `GLMOCRServiceConfig` (final name follows upstream API review).
It addresses the **full SDK parsing service**, not the MLX model endpoint.

Candidate fields are endpoint URL, optional secret credential, and request
timeout. These are client-side settings only. Model selection, layout device,
recognition concurrency, and hosted-mode controls belong to the separately managed
SDK/MLX deployment configuration, not this public dataclass. The client cannot
enforce a server's deployment policy. Do not expose CUDA device numbers as Apple GPU selectors
or imply that PDF Craft owns model downloads for this service-backed mode.

Decide how the service config fits existing `LocalOCRConfig`, `VendorOCRConfig`,
`OCRConfig`, and `OCRMode` aliases during phase 1, before implementing the upstream
factory. Audit consumers rather
than treating a new local HTTP service as an existing CUDA-local configuration.
Existing configuration names and default selection must remain compatible.

## Implementation phases

### 1. Validate the upstream runtime and result contract

- [ ] Select released SDK/MLX-VLM/model revisions and compatible Python versions;
      record macOS version, chip, unified memory, and exact package versions.
- [ ] Start and health-check MLX-VLM in its isolated environment, then recognize
      a small page. Record device/backend telemetry demonstrating Metal execution;
      successful inference alone does not prove GPU acceleration.
- [ ] Start the SDK service with CPU layout detection and hosted mode disabled,
      pointing it to the verified MLX endpoint. Health-check it before parsing.
- [ ] Call its parsing service with an encoded page image and capture sanitized
      JSON for text, multi-column content, headings, footnotes, images, tables,
      and formulas. Use redistributable or synthetic documents only. Save a
      versioned fixture with request metadata, service launch configuration,
      model/package revisions, and coordinate conventions for phase-2 tests.
- [ ] Establish response schema, page ordering, coordinate units/origin, resize
      transforms, region labels, text/HTML representation, image-region handling,
      and whether polygons require conversion to enclosing rectangles.
- [ ] Inspect the installed/released `doc-page-extractor` adapter and result
      interfaces. Confirm the exact factory, staging, error, and token contracts.
- [ ] Settle public configuration naming, alias classification, and capability
      semantics before implementing the upstream factory.

**Exit gate:** a reproducible all-local page parse with usable layout geometry
and a documented mapping to the upstream extraction contract. If structured
geometry is unavailable, stop: plain Markdown is not sufficient for this backend.

### 2. Implement the adapter in doc-page-extractor

- [ ] Add a service configuration and official extractor factory there, following
      that repository's own guidance and tests.
- [ ] Map SDK results into its existing structured blocks and extraction results.
      Preserve reading order and convert all coordinates into the exact input
      image's pixel space before PDF Craft consumes them.
- [ ] Map titles, body text, captions, tables, formulas, images, and footnotes
      explicitly. Decide treatment of headers/footers and unknown labels without
      silently losing meaningful content.
- [ ] Preserve HTML/text distinctions, child content, and source image alignment.
      Reject malformed/degenerate geometry rather than inventing positions.
- [ ] Specify one-stage versus multi-stage behavior so `includes_footnotes` does
      not cause duplicate recognition or duplicate text. Return footnote labels
      compatible with the existing filtering behavior.
- [ ] Preserve cancellation, bounded timeouts/retries, empty-page behavior, and
      error propagation. Do not turn a failed page into successful empty output.
- [ ] Define token accounting and limits. If the service lacks usage or limit
      controls, document/reject unsupported guarantees rather than fabricate usage.
- [ ] Add fixture-driven and mocked-HTTP tests; release a compatible upstream
      version before integrating its factory into PDF Craft.

**Exit gate:** upstream adapter tests pass without models/network, and one real
SDK response passes through the adapter with correct content and geometry.

### 3. Integrate the configuration in PDF Craft

Likely changes, subject to a targeted consumer audit:

| Location | Responsibility |
| --- | --- |
| `pdf_craft/ocr_config.py` | Frozen service configuration, validation, configuration/mode unions. |
| `pdf_craft/__init__.py` | Public export of the new configuration. |
| `pdf_craft/pdf/page_extractor.py` | Lazy upstream factory construction; preserve existing normalization. |
| `pyproject.toml`, `poetry.lock` | Minimum compatible upstream release and resolved dependency update. |
| `tests/test_ocr_config.py` | Public configuration behavior and unchanged defaults. |
| `tests/test_page_extractor_structured.py` | Factory wiring, structured results, geometry, and footnote regressions. |

- [ ] Keep renderers and the public extraction schema unchanged. If that becomes
      impossible, stop and revise scope rather than expanding the backend change.
- [ ] Preserve import-time behavior without MLX, SDK, model weights, or CUDA.
- [ ] Make download/load-model operations fail clearly for externally managed
      service configurations, consistent with existing service-backed behavior.
- [ ] Use fresh analysis directories when comparing OCR backends: cached page XML
      and `done` markers must not make an old result look like a new model run.
- [ ] Document standalone library setup first. Only extend `pdf_craft_tool` if
      needed for explicit local testing; it is not a prerequisite for library use.

**Exit gate:** deterministic integration tests pass and existing configurations
retain their behavior, with no default model/network requirement.

### 4. Validate real output and document setup

- [ ] Run the local smoke matrix below with explicit user-selected fixtures.
- [ ] Compare OCR blocks and overlays against source pages before checking final
      Markdown/EPUB; separate model errors from adapter and renderer errors.
- [ ] Record cold start separately from warm recognition, layout time, total
      time/page, peak memory, failures, and concurrency settings. Start serially.
- [ ] Record limitations and reproducible versions; avoid unsupported throughput,
      memory, or quality guarantees.
- [ ] Update `docs/en/OCR_BACKENDS.md`, relevant installation/API guides and their
      maintained counterparts. Clearly distinguish SDK and MLX endpoint URLs.
- [ ] Explain download/offline setup, CPU layout execution, service lifecycle,
      local-only privacy settings, troubleshooting, and cache isolation.

**Exit gate:** accepted representative Markdown/EPUB outputs on an Apple Silicon
Mac, a reproducible setup, and documented limitations.

## Verification strategy

### Deterministic tests (normal development / existing CI)

Cover factory selection and lazy imports; malformed/empty service responses;
label mapping and reading order; resized/normalized/polygon coordinates; assets;
table HTML/formula text; footnote inclusion without duplicates; timeout and HTTP
errors; cancellation and token-limit semantics. Keep fixtures small and free of
credentials, model files, and third-party private content.

Use focused tests first, then the repository's normal gates as appropriate:

```sh
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

Run `poetry build` when dependencies/packaging change. Upstream adapter checks run
in its own repository. Do not duplicate tests already covered by a composite gate.

### Opt-in local smoke matrix (not CI)

| Fixture | Acceptance check |
| --- | --- |
| Single-column text with heading | Correct text order and heading classification. |
| Two-column page with footnotes | Columns remain ordered; footnotes are neither omitted when enabled nor duplicated. |
| Table and display formula | Text/HTML/LaTeX survives mapping and has valid source regions. |
| Image with caption | Image crop and caption correspond to the source page. |
| Blank and degraded-scan pages | Explicit, intelligible behavior; no fabricated successful extraction on failure. |
| Short multi-page document | Complete Markdown and readable EPUB; reusable `.pcex` renders without OCR services running. |

Compare bounding-box overlays visually against originals. Keep generated outputs
in worktree-local ignored directories, not source control. Offline validation
must run with outbound network access blocked after required models are cached,
while permitting loopback traffic, and record successful parsing under that policy.
No resident service is started as part of normal library tests.

## Risks and unresolved decisions

1. **Runtime/version compatibility:** verify actual released wheels and arm64 CPU
   layout support; upstream documentation contains version-sensitive instructions.
2. **Geometry and region taxonomy:** results may use normalized coordinates or
   coarser categories. Preserve original layout metadata before lossy formatting;
   revise the adapter approach if captions/footnotes cannot be mapped reliably.
3. **Resource pressure:** the full pipeline costs more than model weights alone.
   Bound concurrency and measure before considering batching or quantization.
4. **Service contract stability:** select an explicit supported SDK version and
   fixture-test response parsing; do not depend on undocumented shapes.
5. **API naming/classification:** settle service-mode aliases and capability
   behavior without breaking existing public imports or convenience defaults.
6. **PDF write-back:** region-level OCR boxes may be too coarse for high-quality
   translated PDFs. Defer claims until dedicated layout checks pass.
7. **Upstream availability:** PDF Craft integration depends on an accepted/released
   `doc-page-extractor` factory. Do not ship a hidden local site-packages patch.

## Definition of done

- A user on a documented Apple Silicon setup can perform local GLM-OCR extraction
  and produce Markdown/EPUB with no CUDA or cloud OCR dependency.
- Backend output conforms to existing source-geometry and structured-content
  contracts; no renderer or `.pcex` format changes are required.
- Existing OCR configurations/defaults and lightweight library imports still work.
- Deterministic tests, upstream adapter checks, and explicit local smoke evidence
  are recorded for the exact implemented versions.
- Documentation states requirements, setup, limitations, and privacy behavior.
- Implementation receives focused review; any implementation PR remains unmerged
  until explicitly approved.

## Sources

- [GLM-OCR Apple Silicon deployment guide](https://github.com/zai-org/GLM-OCR/blob/main/examples/mlx-deploy/README.md)
- [GLM-OCR SDK, CPU layout option, and service documentation](https://github.com/zai-org/GLM-OCR/blob/main/README.md)
- [MLX-VLM GLM-OCR model usage](https://github.com/Blaizzy/mlx-vlm/blob/main/mlx_vlm/models/glm_ocr/README.md)
- [PDF Craft architecture boundaries](../../references/architecture.md)
- [PDF Craft conversion pipeline](../../references/conversion-pipeline.md)
- [Development and worktrees](../../references/development-and-worktrees.md)

Upstream `main` links are discovery references, not reproducible dependency pins;
phase 1 must record the versions actually validated.
