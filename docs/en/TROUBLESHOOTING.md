# Troubleshooting

When a real conversion fails, first identify the layer: PDF reading and rendering, OCR, text LLM translation, output rendering, or PDF/EPUB patching. OCR configuration and text-LLM configuration are separate; a working OCR service does not prove that translation credentials are valid.

## Quick diagnosis

| Symptom | Check first |
| --- | --- |
| `Poppler not found in PATH` | Install Poppler and make its commands visible to the process. |
| Local OCR cannot use CUDA | NVIDIA driver, CUDA-enabled PyTorch, visible GPU, and `pdf-craft[local]`. |
| Local OCR cannot find a model | Cache path, disk space, and `local_only`. |
| Vendor OCR returns an error | Endpoint, model, credential, network access, quota, and rate limits. |
| EPUB/PDF translation reports an LLM error | Text LLM URL, model, key, and token encoding. |
| No output file appears | Parent-directory permissions and the earlier exception. |
| PDF patching rejects a package | The original PDF, page geometry, and translated package must correspond. |

## PDF and Poppler

### `Poppler not found in PATH`

pdf-craft uses Poppler to render PDF pages as images before OCR. This is required for both local and vendor OCR, so changing OCR backends does not solve the error.

Run `pdfinfo -v` in the same environment that runs Python. If it is unavailable, install Poppler, add its executable directory to `PATH`, and restart the shell or process. The [installation guide](INSTALLATION.md) includes platform-specific commands.

If a PDF fails to open or only selected pages fail, verify that it opens in an ordinary PDF reader and that the current user can read it. Password protection, corrupt cross-reference data, unusual page dimensions, and malformed PDFs can all fail during rendering.

`ExtractionOptions(ignore_pdf_errors=True)` can let a long job continue past individual PDF errors. A predicate can make that decision selectively. It is a recovery tool, not proof that skipped pages were processed correctly; inspect the resulting document.

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

Use `on_ocr_event` to record the page index, event kind, timing, and tokens. `START`, `RENDERED`, `COMPLETE`, `FAILED`, `SKIP`, and `IGNORE` distinguish a page that was not selected, reused from cache, failed to render, or failed in OCR.

`ignore_ocr_errors=True` (or a predicate) permits later pages to continue after an OCR failure. Check the corresponding output pages afterward. If an `aborted` callback requests cancellation, the underlying extraction aborts; a token budget is a separate interruption condition. Capture the actual exception and the recent OCR events when diagnosing either case.

## Vendor OCR

Vendor OCR consumes remote provider resources and does not require a local GPU. Authentication errors such as 401 or 403 usually mean the endpoint, model, and credential do not belong to the same provider account, or the key lacks permission. Do not substitute a text-LLM key for an OCR credential.

For timeouts, rate limits, or quota failures, verify endpoint reachability and check the provider account's balance, quota, and rate-limit status. Start with a one-page extraction to distinguish a bad page from a service-wide issue.

DeepSeek OCR vendor configurations use OpenAI-compatible `base_url`, `api_key`, and `model` fields. Baidu Unlimited OCR uses `ak` and `sk`, with its own polling and timeout settings. Those credential formats are not interchangeable. See [OCR backends](OCR_BACKENDS.md) for the exact configuration fields.

## Text LLM and translation

Existing-EPUB translation needs `llm`, or both `translation_llm` and `fill_llm`. PDF OCR credentials do not automatically provide a text translation model. `translation_llm` produces translated text; `fill_llm` repairs EPUB XML when necessary.

`LLM` expects an OpenAI-compatible Chat Completions endpoint. Check that `url` is the provider's base URL, `model` is available to the key, and `token_encoding` matches the model. Empty model replies are retried and eventually raise an empty-response error; correcting an invalid model name or endpoint is more useful than simply increasing retries.

For EPUB XML-repair problems, register `on_fill_failed` and pay particular attention to an event with `over_maximum_retries=True`. Lowering concurrency, shortening a custom prompt, or choosing a model that reliably returns structured content is often more effective than unlimited retries. See [EPUB translation](EPUB_TRANSLATION.md) for the callback API.

When `LLM(cache_path=...)` is used, successful requests can be reused after an interruption. Use separate cache directories for unrelated books or jobs, particularly while they may be writing concurrently. A rerun still creates a new EPUB output; it does not resume by appending to an incomplete destination file.

## Outputs, package paths, and PDF patching

`convert_pdf_to_markdown()` and `convert_pdf_to_epub()` use a temporary package directory when `package_path` is omitted, and clean it up in both success and exception paths. Pass a writable package path if you need OCR artifacts for debugging or reuse; pdf-craft will not delete that caller-owned directory.

If an output is absent, make sure its parent directory exists and is writable, then work backward to the first OCR, translation, or renderer exception. A partially created output is not a successful conversion.

PDF patching requires the original PDF plus a package extracted from that same document. The package must include the relevant page geometry, and translated text must fit the original OCR bounding boxes. Patch output is rendered from page images, so it does not preserve source vector text, annotations, or links. `APPEND_BLOCK` is unsupported for PDF patching because it cannot add free-flowing content to a fixed page.

## What to include in a bug report

Provide enough context to reproduce the failure without exposing secrets:

- operating system, Python version, and pdf-craft version;
- OCR backend and whether it is local or vendor;
- for local OCR: GPU model and `torch.cuda.is_available()` result;
- input page or chapter count, and the workflow stage that failed;
- full exception type and message, redacted of credentials and private content;
- relevant `package_path`, model-cache path, or LLM-cache path choices.

Do not attach API keys, complete private documents, model caches, or unredacted logs to a public issue.
