# OCR Backend Guide

Choose one OCR configuration for each PDF extraction. Local backends run models on a CUDA-capable NVIDIA GPU; vendor backends send pages to a remote service. Use local OCR for local or offline processing after models are cached. Use vendor OCR when you do not have CUDA or prefer managed compute.

DeepSeek OCR and DeepSeek OCR 2 are from [DeepSeek](https://github.com/deepseek-ai/DeepSeek-OCR); [Unlimited OCR](https://github.com/baidu/Unlimited-OCR) is from Baidu.

| Configuration | Model | Runtime |
| --- | --- | --- |
| `DeepSeekOCRLocalConfig` | DeepSeek OCR | Local GPU |
| `DeepSeekOCR2LocalConfig` | DeepSeek OCR 2 | Local GPU |
| `UnlimitedOCRLocalConfig` | Unlimited OCR | Local GPU |
| `DeepSeekOCRVendorConfig` | DeepSeek OCR | OpenAI-compatible remote service |
| `DeepSeekOCR2VendorConfig` | DeepSeek OCR 2 | OpenAI-compatible remote service |
| `UnlimitedOCRVendorConfig` | Unlimited OCR | Baidu remote service |

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

`UnlimitedOCRVendorConfig` takes Baidu `ak` and `sk`; its `base_url` defaults to `https://aip.baidubce.com`. It also accepts `poll_interval_seconds`, `timeout_seconds`, `retry_times`, and `retry_interval_seconds`. The DeepSeek vendor configurations expose the same retry controls.

```python
from pdf_craft import UnlimitedOCRVendorConfig

ocr = UnlimitedOCRVendorConfig(ak="your-access-key", sk="your-secret-key")
```

## Convenience defaults

When `PDFOptions` has no explicit `ocr`, `models_cache_path` and `local_only` configure the default local DeepSeek OCR setup. Do not combine either convenience field with an explicit `ocr` configuration; that is rejected because the ownership of those settings would be ambiguous.

OCR recognizes pages only. Text translation uses a separate LLM configuration; see the PDF and EPUB guides.

Vendor OCR renders PDF pages serially, then submits only the remote OCR operations through
`ocr_executor`. Skipped pages and ignored failures are terminal page outcomes and do not hold up
the remaining extraction pipeline. When either cumulative OCR token budget is set, vendor requests
settle one page at a time so the next request receives the exact remaining budget.
