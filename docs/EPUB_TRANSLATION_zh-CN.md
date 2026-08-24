# EPUB 翻译指南

pdf-craft 可以把已有 EPUB 翻译为单语版本或双语对照版本，并尽量保留原书的章节结构、目录、
插图和排版。翻译需要一个 OpenAI-compatible 文本 LLM；OCR 配置不参与已有 EPUB 的翻译。

## 最小示例

```python
from pdf_craft import LLM, PDFCraft, SubmitKind

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
)

PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
)
```

`target_language` 使用目标语言名称或代码，例如 `"zh"`、`"en"` 或 `"Japanese"`。

## 选择译文呈现方式

`submit` 决定译文如何写入 EPUB：

| 模式 | 输出效果 | 适用场景 |
| --- | --- | --- |
| `SubmitKind.REPLACE` | 用译文替换原文 | 只需要目标语言版本 |
| `SubmitKind.APPEND_TEXT` | 将译文紧接在原文后面 | 希望在同一段内阅读双语文本 |
| `SubmitKind.APPEND_BLOCK` | 将译文作为独立文本块追加在原文后 | 双语对照阅读，推荐 |

目录和书籍元数据也会按相同模式处理。使用 `APPEND_BLOCK` 时，为了保证目录结构可用，
目录和元数据中的译文会以内联方式附在原文后。

## 调整翻译行为

`translate_epub` 支持传入自定义提示词、重试次数和并发数：

```python
PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    user_prompt="使用正式书面语，保留人名、术语和脚注编号。",
    max_retries=5,
    concurrency=4,
)
```

从 `concurrency=1` 开始；确认 LLM 服务的并发和速率限制允许后，再逐步提高。翻译顺序会
在输出中保持不变。`max_retries` 控制单个翻译或结构修复失败后的最大重试次数。

## 缓存与恢复

为 LLM 配置 `cache_path` 后，已完成的翻译会写入本地缓存。翻译中断或失败后，以相同配置
再次运行可以复用缓存，减少重复请求：

```python
llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
    cache_path="translation-cache",
)
```

建议为不同书籍或不同翻译任务使用不同的缓存目录，方便管理和排查问题。

## 使用两个 LLM

翻译过程既需要翻译文本，也需要在必要时修复 EPUB XML 结构。默认情况下，`llm` 同时承担
两项工作；如果希望分别调优，可以提供 `translation_llm` 和 `fill_llm`：

```python
translation_llm = LLM(
    key="your-api-key", url="https://api.openai.com/v1",
    model="gpt-4.1-mini", token_encoding="o200k_base", temperature=0.7,
)
fill_llm = LLM(
    key="your-api-key", url="https://api.openai.com/v1",
    model="gpt-4.1-mini", token_encoding="o200k_base", temperature=0.2,
)

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    translation_llm=translation_llm,
    fill_llm=fill_llm,
)
```

## 观察进度和失败

`on_progress` 接收介于 `0.0` 与 `1.0` 之间的进度值。`on_fill_failed` 会在 XML 修复失败
或重试时收到 `FillFailedEvent`；当 `over_maximum_retries` 为 `True` 时，该错误已经超过
最大重试次数，可能影响最终 EPUB。

```python
from pdf_craft import FillFailedEvent

def on_progress(progress: float) -> None:
    print(f"翻译进度：{progress:.0%}")

def on_fill_failed(event: FillFailedEvent) -> None:
    if event.over_maximum_retries:
        print(f"未恢复的结构修复错误：{event.error_message}")

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    llm=llm, on_progress=on_progress, on_fill_failed=on_fill_failed,
)
```
