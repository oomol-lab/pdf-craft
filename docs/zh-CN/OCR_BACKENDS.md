# OCR backend 配置指南

本指南面向需要在库层配置 OCR 的用户。README 已经给出选择方向：没有合适的本地 GPU
时使用 vendor OCR；希望在本机运行模型时使用 local OCR。本指南进一步说明六种配置对象
的差异、字段和运行约束。

## 先决定运行位置

pdf-craft 的 OCR backend 分成两类：

- **local OCR**：模型下载到本地，并直接使用本机 NVIDIA GPU。适合希望数据留在本机、
  已经具备 CUDA 环境，或需要离线运行的场景。
- **vendor OCR**：页面发送到远程供应商服务，由远端资源完成识别。适合没有 CUDA、
  不希望安装本地模型运行时，或希望把 GPU 运维交给供应商的场景。它需要网络、服务
  凭据，并会产生供应商侧的请求成本。

一次 OCR 运行只配置一个 backend。模型归属与运行方式可以分开选择：DeepSeek OCR 和
DeepSeek OCR 2 来自 [DeepSeek](https://github.com/deepseek-ai/DeepSeek-OCR)，
[Unlimited OCR](https://github.com/baidu/Unlimited-OCR) 来自百度。

## 六种配置对象

| 配置对象 | 模型 | 运行位置 | 适用条件 |
| --- | --- | --- | --- |
| `DeepSeekOCRLocalConfig` | DeepSeek OCR | 本机 GPU | 有 CUDA，使用 DeepSeek OCR 本地模型 |
| `DeepSeekOCR2LocalConfig` | DeepSeek OCR 2 | 本机 GPU | 有 CUDA，使用 DeepSeek OCR 2 本地模型 |
| `UnlimitedOCRLocalConfig` | 百度 Unlimited OCR | 本机 GPU | 有 CUDA，使用 Unlimited OCR 本地模型 |
| `DeepSeekOCRVendorConfig` | DeepSeek OCR | 远程服务 | 使用 OpenAI-compatible OCR endpoint |
| `DeepSeekOCR2VendorConfig` | DeepSeek OCR 2 | 远程服务 | 使用 OpenAI-compatible OCR 2 endpoint |
| `UnlimitedOCRVendorConfig` | 百度 Unlimited OCR | 远程服务 | 使用百度 Unlimited OCR 服务 |

库 API 通过 `PDFOptions(ocr=...)` 接收配置对象，不读取环境变量。下面的例子都可以传给
`PDFCraft(pdf=PDFOptions(...))`，再用于 `convert_pdf_to_markdown`、`convert_pdf_to_epub`
或 `extract_pdf`。

## Local OCR 配置

三种 local 配置的字段相同：

| 字段 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `models_cache_path` | 路径或 `None` | `None` | 模型缓存目录；`None` 使用上游模型库的默认缓存位置 |
| `local_only` | `bool` | `False` | 只使用本地已有模型；设为 `True` 后禁止运行时下载 |
| `enable_devices_numbers` | 整数可迭代对象或 `None` | `None` | 指定可使用的 GPU 编号；`None` 使用默认设备映射 |

### 基本配置

```python
from pdf_craft import DeepSeekOCRLocalConfig, PDFCraft, PDFOptions

ocr = DeepSeekOCRLocalConfig(
    models_cache_path="models-cache",
)
craft = PDFCraft(pdf=PDFOptions(ocr=ocr))
craft.convert_pdf_to_markdown("input.pdf", "output.md")
```

local OCR 需要支持 CUDA 的 PyTorch、可用的 NVIDIA GPU、足够显存和模型文件。首次运行
通常会下载模型；下载完成后，可以切换到离线模式：

```python
ocr = DeepSeekOCRLocalConfig(
    models_cache_path="models-cache",
    local_only=True,
)
```

离线模式要求对应模型已经完整存在于缓存目录。缓存目录可以在多个运行中复用，但不同
模型或不同来源的缓存应分开管理。

### 指定 GPU

在多 GPU 设备上，可以显式指定可用设备编号：

```python
ocr = DeepSeekOCRLocalConfig(
    models_cache_path="models-cache",
    enable_devices_numbers=[0, 1],
)
```

这里的编号由上游 OCR 运行时解释，通常对应当前进程可见的 CUDA 设备。指定不存在或不可用
的设备会在模型加载或 OCR 开始时失败。

### 三种 local 模型的 preset

`ocr_size` 是提取选项，不是 OCR 配置对象字段。可选值包括 `tiny`、`small`、`base`、
`large` 和 `gundam`，但不同模型支持的 preset 不完全相同：

- Unlimited OCR local 支持 `base` 和 `gundam`。
- DeepSeek OCR 2 local 的已验证路径使用 `base`；显式指定 `tiny` 会在提取前快速失败。
- 选择其他 backend 时，应以该 backend 的实际支持范围和模型资源为准。

例如：

```python
from pdf_craft import ExtractionOptions

options = ExtractionOptions(ocr_size="base")
craft.extract_pdf("input.pdf", "package", options)
```

### 预下载模型

如果希望把模型下载和实际转换分开，可以使用 `predownload_models`：

```python
from pdf_craft import DeepSeekOCRLocalConfig, predownload_models

predownload_models(
    ocr=DeepSeekOCRLocalConfig(models_cache_path="models-cache"),
    revision=None,
)
```

转换时使用同一个 `models_cache_path`，并在确认模型完整后设置 `local_only=True`。

## Vendor OCR 配置

Vendor OCR 不要求本地 CUDA，但会把页面发送到远程服务。请根据供应商的 endpoint、凭据
和模型文档填写配置；pdf-craft 只负责把配置传给对应的 OCR backend。

### DeepSeek OCR 与 DeepSeek OCR 2

这两个 vendor 配置字段相同：

| 字段 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `base_url` | `str` | 必填 | OpenAI-compatible 服务的基础 URL |
| `api_key` | `str` | 必填 | 服务访问密钥 |
| `model` | `str` | 必填 | 服务端模型名 |
| `temperature` | `float` 或 `None` | `None` | 传给服务的采样温度 |
| `top_p` | `float` 或 `None` | `None` | 传给服务的 nucleus sampling 参数 |
| `max_tokens` | `int` | `8000` | 单次请求的最大输出 token 数 |
| `timeout_seconds` | `int` | `180` | 单次请求超时时间 |

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

ocr = DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr",
    timeout_seconds=180,
)
craft = PDFCraft(pdf=PDFOptions(ocr=ocr))
```

使用 DeepSeek OCR 2 时，将配置对象和服务端模型名替换为 OCR 2 对应值：

```python
from pdf_craft import DeepSeekOCR2VendorConfig

ocr = DeepSeekOCR2VendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr2",
)
```

### 百度 Unlimited OCR

Unlimited OCR vendor 配置使用百度服务凭据：

| 字段 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `ak` | `str` | 必填 | 百度 Access Key |
| `sk` | `str` | 必填 | 百度 Secret Key |
| `base_url` | `str` | `https://aip.baidubce.com` | 百度服务基础 URL |
| `poll_interval_seconds` | `float` | `2.0` | 轮询异步任务的间隔 |
| `timeout_seconds` | `int` | `180` | OCR 任务超时时间 |

```python
from pdf_craft import UnlimitedOCRVendorConfig, PDFCraft, PDFOptions

ocr = UnlimitedOCRVendorConfig(
    ak="your-access-key",
    sk="your-secret-key",
)
craft = PDFCraft(pdf=PDFOptions(ocr=ocr))
```

## `PDFOptions` 的配置边界

`PDFOptions` 还提供 `models_cache_path` 和 `local_only` 两个便捷字段，用于在不显式
传入 `ocr` 时创建默认的 `DeepSeekOCRLocalConfig`。如果已经传入了 `ocr` 配置对象，不能
同时再传 `models_cache_path` 或 `local_only`，否则会抛出 `ValueError`。

推荐明确选择一种写法，不要混用：

```python
# 推荐：显式使用一个完整 OCR 配置对象。
PDFCraft(pdf=PDFOptions(ocr=DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr",
)))

# 便捷写法：不传 ocr 时，使用默认的 DeepSeek local OCR 配置。
PDFCraft(pdf=PDFOptions(
    models_cache_path="models-cache",
    local_only=True,
))
```

## 常见运行边界

- local OCR 依赖本地 CUDA、GPU 显存和模型缓存；没有这些条件时选择 vendor OCR。
- `local_only=True` 不会替你下载缺失模型；请先完成模型下载。
- vendor OCR 依赖网络和有效凭据，`base_url`、模型名及凭据错误会在请求阶段失败。
- `ocr_size` 由提取选项控制，不能用来改变 vendor/local backend；backend 由 OCR 配置对象决定。
- 翻译所需的文本 LLM 与 OCR backend 是两套独立配置，本指南不讨论翻译配置。
