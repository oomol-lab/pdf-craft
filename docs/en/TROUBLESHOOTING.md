# Troubleshooting

## Input compatibility

Use the [optional input assertions](ERROR_HANDLING.md#optional-input-assertions)
when you want to diagnose reading, rendering or editing before conversion.
Inspect the original exception and traceback: missing executables or libraries
indicate installation problems, while parse/render errors indicate that the
backend could not perform that operation. The SDK does not repair the input.

When a real conversion fails, first identify the layer: PDF reading and rendering, OCR, text LLM translation, output rendering, or PDF/EPUB patching. OCR configuration and text-LLM configuration are separate; a working OCR service does not prove that translation credentials are valid.

## Quick diagnosis

| Symptom | Check first |
| --- | --- |
| `Poppler not found in PATH` | Install Poppler and make its commands visible to the process. |
| PDF patching cannot start or reports Ghostscript | Install Ghostscript and make `gs` visible to the process. |
| Inline formulas are plain text instead of vectors | Install Matplotlib and local TeX, or accept the intentional fallback. |
| Local OCR cannot use CUDA | NVIDIA driver, CUDA-enabled PyTorch, visible GPU, and `pdf-craft[local]`. |
| Local OCR cannot find a model | Cache path, disk space, and `local_only`. |
| Vendor OCR returns an error | Endpoint, model, credential, network access, quota, and rate limits. |
| EPUB/PDF translation reports an LLM error | Text LLM URL, model, key, and token encoding. |
| No output file appears | Parent-directory permissions and the earlier exception. |
| PDF patching rejects an extraction | The original PDF, `pages.xml`, and translated `.pcex` must correspond. |

## PDF and Poppler

### `Poppler not found in PATH`

pdf-craft uses Poppler to render PDF pages as images before OCR and to sample local background colors while patching translated PDFs. The default PDF handler uses it with both local and vendor OCR, so changing OCR backends does not solve a missing Poppler installation. A custom `AsyncPDFHandler` supplies its own rendering.

Run `pdfinfo -v` in the same environment that runs Python. If it is unavailable, install Poppler, add its executable directory to `PATH`, and restart the shell or process. The [installation guide](INSTALLATION.md) includes platform-specific commands.

If a PDF fails to open or only selected pages fail, verify that it opens in an ordinary PDF reader and that the current user can read it. Password protection, corrupt cross-reference data, unusual page dimensions, and malformed PDFs can all fail during rendering.

`PDFCraft(error_policy=ErrorPolicy(ignore_pdf_render_errors=True))` can let a long job continue past individual page-render errors. Import `ErrorPolicy` from `pdf_craft`. A predicate in that field receives `FailureContext` and returns whether to recover; it does not receive a bare `PDFError`. Recovery is not proof that the page was processed correctly; inspect the output. All requested pages falling back still fails the task. See [error handling](ERROR_HANDLING.md).

## Local OCR

### Missing local runtime or no CUDA

The base package intentionally does not install local GPU dependencies. Install the local extra in the environment that runs the code:

```bash
python -m pip install 'pdf-craft[local]'
```

Then check the GPU in that same interpreter:

```bash
nvidia-smi
python -c "import torch; print(torch.cuda.is_available())"
```

`torch.cuda.is_available()` must be true for local OCR. If it is false, verify that you installed a CUDA-enabled PyTorch build rather than a CPU-only wheel, that the NVIDIA driver sees the GPU, and that the process/container has access to it. On a machine without usable CUDA, switch to a vendor configuration rather than repeatedly changing local OCR parameters.

### Model download, cache, and offline use

The first local run may download a model. Confirm that `models_cache_path` is writable, the machine has sufficient disk space, and the process can reach the model host. `local_only=True` forbids downloads; it is valid only after the entire model already exists in the selected cache.

If a download was interrupted, remove or isolate the incomplete model cache entry and download again, or use a new cache directory. Use `predownload_models(...)` before deploying an offline environment. Do not combine `PDFOptions(models_cache_path=..., local_only=...)` with an explicit `ocr=` configuration; put those settings on the local OCR config instead.

### Preset, memory, and page scope

OCR presets differ by backend. Unlimited OCR local supports `base` and `gundam`. DeepSeek OCR 2 local is validated with `base`; an explicit `tiny` preset is rejected before model execution and should be changed to `base`.

For out-of-memory errors or very slow runs, first process a small sample with `ExtractionOptions(page_indexes={1})`, then lower `dpi` or choose a suitable OCR preset. `page_indexes` uses one-based page numbers. Reducing the page range is usually a better diagnostic than assuming the input file is damaged.

`max_ocr_tokens` limits cumulative OCR input and output tokens; `max_ocr_output_tokens` limits output tokens only. Hitting either limit stops later pages rather than producing a complete document. Higher limits can increase vendor charges or local resource use.

### Find the page that failed

Use `on_extraction_event` to record unit identity, page index, integer stage and final outcome. See [the unified progress protocol](TRANSLATION_PROGRESS.md). ErrorPolicy supplies failure details; metered returns supply tokens.

`ErrorPolicy(ignore_ocr_errors=True)` (or a predicate in that field) permits later pages to continue after a recoverable OCR failure. Pass it as `error_policy` to the façade or operation. Predicates receive `FailureContext`, including source-page locations and a structured error summary. Check the corresponding output pages afterward. If an `aborted` callback requests cancellation, the underlying extraction aborts; a token budget is a separate interruption condition. Capture the actual exception and the recent OCR events when diagnosing either case.

## Vendor OCR

Vendor OCR consumes remote provider resources and does not require a local GPU. Authentication errors such as 401 or 403 usually mean the endpoint, model, and credential do not belong to the same provider account, or the key lacks permission. Do not substitute a text-LLM key for an OCR credential.

For timeouts, rate limits, or quota failures, verify endpoint reachability and check the provider account's balance, quota, and rate-limit status. Start with a one-page extraction to distinguish a bad page from a service-wide issue.

DeepSeek OCR vendor configurations use OpenAI-compatible `base_url`, `api_key`, and `model` fields. Baidu Unlimited OCR uses `ak` and `sk`, with its own polling and timeout settings. Those credential formats are not interchangeable. See [OCR backends](OCR_BACKENDS.md) for the exact configuration fields.

## Text LLM and translation

Existing-EPUB translation needs `llm`, or both `translation_llm` and `fill_llm`. PDF OCR credentials do not automatically provide a text translation model. `translation_llm` produces translated text; `fill_llm` repairs EPUB XML when necessary.

`LLM` expects an OpenAI-compatible Chat Completions endpoint. Check that `url` is the provider's base URL, `model` is available to the key, and `token_encoding` matches the model. Empty model replies are retried and eventually raise an empty-response error; correcting an invalid model name or endpoint is more useful than simply increasing retries.

See [retry and recovery policies](ERROR_HANDLING.md) for failure decisions and retry budgets.

When `LLM(cache_path=...)` is used, successful requests can be reused after an interruption. Use separate cache directories for unrelated books or jobs, particularly while they may be writing concurrently. A rerun still creates a new EPUB output; it does not resume by appending to an incomplete destination file.

## Outputs, extraction paths, and PDF patching

`convert_pdf_to_markdown()` and `convert_pdf_to_epub()` use a temporary analysis
directory by default and clean it up after success or failure. Pass `analysing_path`
to retain diagnostics, or `extraction_path` to export a portable `.pcex`. Back-end
operations accept `.pcex`, not an analysis or ordinary extraction directory.

If an output is absent, make sure its parent directory exists and is writable, then work backward to the first OCR, translation, or renderer exception. A partially created output is not a successful conversion.

PDF patching requires the original PDF plus a `.pcex` extracted from that same
document. `pages.xml` must include the relevant page geometry; missing data is not
recovered from OCR caches or by re-rendering. Body text must fit the original OCR
bounding boxes. Patching requires Ghostscript: it creates a fontless visual base of
source non-Annotation content, so original and hidden OCR text cannot interfere with
selection, search, or extraction. The Qt layer is the translated selectable text;
the original `/Annots` array is reattached above it so links, highlights, notes, and
forms remain interactive. Erasure uses a padded local RGB background color rather
than texture reconstruction. `APPEND_BLOCK` is unsupported.

Inline formulas default to vector PDF rendering when Matplotlib and local TeX are
both available. They intentionally fall back to readable plain text if either
runtime is absent or one formula fails, so a missing TeX setup does not fail the
whole PDF. Set `PatchTextOptions(render_inline_formulas=False)` to request the same
plain-text mode explicitly.

## What to include in a bug report

Provide enough context to reproduce the failure without exposing secrets:

- operating system, Python version, and pdf-craft version;
- OCR backend and whether it is local or vendor;
- for local OCR: GPU model and `torch.cuda.is_available()` result;
- input page or chapter count, and the workflow stage that failed;
- full exception type and message, redacted of credentials and private content;
- relevant `analysing_path`, `extraction_path`, model-cache path, or LLM-cache path choices.

Do not attach API keys, complete private documents, model caches, or unredacted logs to a public issue.


See [error handling](ERROR_HANDLING.md) for strict defaults, source locations and task-wide all-fallback protection.
