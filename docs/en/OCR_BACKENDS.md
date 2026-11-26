# OCR Backend Guide

Choose one OCR configuration for each PDF extraction. Local backends run models on a CUDA-capable NVIDIA GPU; vendor backends send pages to a remote service. Use local OCR for local or offline processing after models are cached. Use vendor OCR when you do not have CUDA or prefer managed compute.

DeepSeek OCR and DeepSeek OCR 2 are from [DeepSeek](https://github.com/deepseek-ai/DeepSeek-OCR); [Unlimited OCR](https://github.com/baidu/Unlimited-OCR) is from Baidu; [MiniCPM-OCR](https://github.com/OpenBMB/MiniCPM-OCR) is from OpenBMB and ModelBest.

| Configuration | Model | Runtime |
| --- | --- | --- |
| `DeepSeekOCRLocalConfig` | DeepSeek OCR | Local GPU |
| `DeepSeekOCR2LocalConfig` | DeepSeek OCR 2 | Local GPU |
| `UnlimitedOCRLocalConfig` | Unlimited OCR | Local GPU |
| `DeepSeekOfficialOCRConfig` | DeepSeek official OCR | Official multipart API |
| `DeepSeekOCRVendorConfig` | DeepSeek OCR | OpenAI-compatible remote service |
| `DeepSeekOCR2VendorConfig` | DeepSeek OCR 2 | OpenAI-compatible remote service |
| `UnlimitedOCRVendorConfig` | Unlimited OCR | Baidu remote service |
| `MiniCPMOCRVendorConfig` | MiniCPM-OCR 1.0 | ModelBest API or self-hosted MiniCPM-OCR service |

Pass a configuration through `PDFOptions(ocr=...)`; the library does not load `.env` files.

## Local backends

All three local configurations accept `models_cache_path`, `local_only`, and `enable_devices_numbers`.

```python
from pdf_craft import DeepSeekOCRLocalConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(ocr=DeepSeekOCRLocalConfig(
    models_cache_path="models-cache",
    enable_devices_numbers=[0],
)))
```

`local_only=True` prevents a missing model from downloading. Use it only after the model is present in the selected cache. Device numbers are interpreted by the upstream OCR runtime and normally correspond to CUDA devices visible to the process.

`ocr_size` belongs to `ExtractionOptions`, not the OCR configuration. Unlimited OCR local supports `base` and `gundam`. DeepSeek OCR 2 local is validated with `base`; explicit `tiny` is rejected before extraction.

## Vendor backends

DeepSeek vendor configurations take `base_url`, `api_key`, and `model`; they also accept `temperature`, `top_p`, `max_tokens` (default `8000`), and `timeout_seconds` (default `180`).

```python
from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity,
    PDFCraft, PDFOptions,
)

craft = PDFCraft(pdf=PDFOptions(
    ocr=DeepSeekOCRVendorConfig(
        base_url="https://example.com/v1",
        api_key="your-api-key",
        model="deepseek-ocr",
    ),
    ocr_executor=ConcurrentExecutor(FixedCapacity(8)),
))
```

### Official DeepSeek OCR API

Use `DeepSeekOfficialOCRConfig()` with a DeepSeek API key to submit rendered
page images to `https://api.deepseek.com/v1/tools/ocr`. The API selects the model.

```python
import os

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOfficialOCRConfig, FixedCapacity, PDFCraft, PDFOptions,
)

craft = PDFCraft(pdf=PDFOptions(
    ocr=DeepSeekOfficialOCRConfig(os.environ["DEEPSEEK_API_KEY"]),
    ocr_executor=ConcurrentExecutor(FixedCapacity(1)),
))
craft.convert_pdf_to_epub("book.pdf", "book.epub")
```

The official backend uploads the rendered page image directly and does not call
`ocr_image_url_resolver`. It uses the existing vendor timeout and retry controls. See the [official OCR API](https://api-docs.deepseek.com/guides/ocr/).

For the repository's local CLI, set `PDF_CRAFT_DEEPSEEK_OCR_OFFICIAL_API_KEY` in
the project `.env` and select `--ocr-mode deepseek-ocr-official` on `pdf convert`.

### Baidu Unlimited OCR

`UnlimitedOCRVendorConfig` takes Baidu `ak` and `sk`; its `base_url` defaults to `https://aip.baidubce.com`. It also accepts `poll_interval_seconds`, `timeout_seconds`, `retry_times`, and `retry_interval_seconds`. The DeepSeek vendor configurations expose the same retry controls.

```python
from pdf_craft import UnlimitedOCRVendorConfig

ocr = UnlimitedOCRVendorConfig(ak="your-access-key", sk="your-secret-key")
```

### MiniCPM-OCR

`MiniCPMOCRVendorConfig` sends each rendered page to a MiniCPM-OCR service and
uses the layout regions in its document-parse response. Both the ModelBest API
and the official self-hosted Docker image expose this through an
OpenAI-compatible chat completions endpoint.

```python
from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, MiniCPMOCRVendorConfig,
    PDFCraft, PDFOptions,
)

craft = PDFCraft(pdf=PDFOptions(
    ocr=MiniCPMOCRVendorConfig(
        base_url="https://api.modelbest.cn/v1",
        api_key="your-api-key",
    ),
    ocr_executor=ConcurrentExecutor(FixedCapacity(8)),
))
```

`model` defaults to `MiniCPM-OCR-1.0`. The example uses the China site; use
`https://api.modelbest.ai/v1` for the global site. For a self-hosted service,
point `base_url` at the service and leave `api_key` empty; set `model` to the
name the service advertises, such as `minicpm-ocr` for the official Docker
image:

```python
ocr = MiniCPMOCRVendorConfig(
    base_url="http://127.0.0.1:8000",
    model="minicpm-ocr",
)
```

The service must run the complete MiniCPM-OCR document parsing pipeline
(layout detection, region recognition, and Markdown assembly). A plain
vision-model endpoint that only recognizes one crop per request does not return
page layout regions and is rejected with a clear error. Orientation correction
is disabled for these requests so the returned boxes stay in the rendered
page's coordinate space. Very large page images may exceed a service's request
size limit; lower `dpi` or set `max_page_image_file_size` in
`ExtractionOptions`. The official services expect the default Base64 Data URL;
use an image URL resolver only when your deployment accepts HTTP(S) image URLs.
Token usage and cumulative OCR token budgets rely on the service reporting
`usage`; the self-hosted Web Demo reports zero, so budgets cannot limit that
deployment.

### Supplying image URLs

By default, Vendor OCR reads each rendered PNG and sends it as a Base64 Data URL. Applications
whose provider accepts or requires remote image URLs can supply an `OCRImageURLResolver`. The
resolver receives the rendered image `Path` and returns either an HTTP(S) URL or a Base64 image
Data URL:

```python
from pathlib import Path

from pdf_craft import (
    ConcurrentExecutor, DeepSeekOCRVendorConfig, FixedCapacity,
    OCRImageURLResolver, PDFCraft, PDFOptions,
)

async def resolve_ocr_image(path: Path) -> str:
    return await image_service.create_url(path)

resolver: OCRImageURLResolver = resolve_ocr_image
craft = PDFCraft(pdf=PDFOptions(
    ocr=DeepSeekOCRVendorConfig(
        base_url="https://example.com/v1",
        api_key="your-api-key",
        model="deepseek-ocr",
    ),
    ocr_executor=ConcurrentExecutor(FixedCapacity(8)),
    ocr_image_url_resolver=resolver,
))
```

The resolver is asynchronous and runs on the caller's event loop. Each logical OCR image is
resolved lazily once, and its URL is reused across transport retries. A
second-stage footnote image is a separate logical image and is resolved independently.

DeepSeek OCR and MiniCPM-OCR send the returned value through `image_url.url`. Unlimited OCR sends Base64 Data
URLs through `file_data` and HTTP(S) URLs through `file_url`. Uploading, signing, expiry,
cleanup, caching, and storage-specific concurrency belong to the application that provides the
resolver.

## Convenience defaults

When `PDFOptions` has no explicit `ocr`, `models_cache_path` and `local_only` configure the default local DeepSeek OCR setup. Do not combine either convenience field with an explicit `ocr` configuration; that is rejected because the ownership of those settings would be ambiguous.

OCR recognizes pages only. Text translation uses a separate LLM configuration; see the PDF and EPUB guides.

Vendor OCR renders PDF pages serially, then submits only the remote OCR operations through
`ocr_executor`. Skipped pages and ignored failures are terminal page outcomes and do not hold up
the remaining extraction pipeline. When either cumulative OCR token budget is set, vendor requests
settle one page at a time so the next request receives the exact remaining budget.

Retries release `ocr_executor` capacity before waiting and reacquire it for every HTTP attempt.
Unlimited OCR token acquisition, task submission, each poll, and result download are separate
remote operations; poll intervals therefore do not occupy a provider slot, and retrying a poll or
download does not submit the page again. Expired access tokens are refreshed before retrying the
current operation, while a provider task-processing failure resubmits that page. HTTP 429 is
retried as rate limiting unless the provider body reports exhausted quota. Payment, exhausted
quota, and authentication or permission failures close the shared executor; other page failures
remain eligible for `ErrorPolicy.ignore_ocr_errors`. This also applies when an Unlimited OCR query reports a
failed task whose `task_error` says the quota is insufficient. The resulting
`OCRError` keeps the original provider exception and HTTP response in its exception cause chain.


See [error handling](ERROR_HANDLING.md) for strict defaults, source locations and task-wide all-fallback protection.

Temporary limits now use the executor's `RateLimitScope`, independently of vendor
`retry_times` and `retry_interval_seconds`, which retain their non-rate recovery roles.
See [shared rate-limit scopes](API_REFERENCE.md#shared-rate-limit-scopes).

Model service calls also support operation-level `request_headers`; see
[scope and resource-download boundaries](API_REFERENCE.md#operation-request-headers).
