# EPUB 翻译指南

pdf-craft 可以直接翻译已有 EPUB。输入已经是 EPUB 时，不需要 OCR；翻译流程会读取书籍
中的章节、目录和元数据，调用文本 LLM，并生成一个新的 EPUB 文件。它适合两类结果：只保留
目标语言的单语版本，或保留原文并追加译文的双语版本。

## 最小示例

`PDFCraft.translate_epub` 是库层的 EPUB 翻译入口。你需要准备一个 OpenAI-compatible 的
文本 LLM 配置，并指定输入文件、输出文件、目标语言和译文提交模式：

~~~python
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
~~~

`target_language` 可以使用语言代码或语言名称，例如 `"zh"`、`"en"` 或 `"Japanese"`。
`llm` 可以使用任何提供 OpenAI-compatible Chat Completions 接口的服务；`url` 是服务端点，
`model` 是模型名称，`key` 是访问密钥，`token_encoding` 用于计算输入文本的 token。

## 选择译文的呈现方式

`submit` 决定译文如何写入 EPUB。pdf-craft 提供三种模式：

| 模式 | 输出效果 | 适用场景 |
| --- | --- | --- |
| `SubmitKind.REPLACE` | 用译文替换原文 | 只需要目标语言版本 |
| `SubmitKind.APPEND_TEXT` | 将译文直接接在原文后面 | 希望在同一段落中连续阅读双语文本 |
| `SubmitKind.APPEND_BLOCK` | 将译文作为独立文本块追加到原文后 | 双语对照阅读，通常最清晰 |

章节正文会严格按照所选模式提交。目录和书籍元数据也会翻译；在 `APPEND_BLOCK` 模式下，
为了保持目录和元数据的结构可用，它们的译文会以内联方式接在原文后，而不是创建新的目录
或元数据块。

下面两个调用分别生成单语和双语 EPUB：

~~~python
# 单语：输出中只保留中文
PDFCraft().translate_epub(
    "source.epub", "translated.zh.epub",
    target_language="zh", submit=SubmitKind.REPLACE, llm=llm,
)

# 双语：原文保留，译文按独立文本块追加
PDFCraft().translate_epub(
    "source.epub", "bilingual.zh.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK, llm=llm,
)
~~~

## 配置文本 LLM

`LLM` 是声明式配置对象，实际请求由 pdf-craft 内部的运行时执行。常用字段如下：

| 字段 | 作用 |
| --- | --- |
| `key` | 服务访问密钥 |
| `url` | OpenAI-compatible API 的 base URL |
| `model` | 文本模型名称 |
| `token_encoding` | token 编码名称，例如 `o200k_base` |
| `timeout` | 单次请求超时时间（秒） |
| `temperature`、`top_p` | 采样参数；也可以传入范围，让重试时逐步调整 |
| `retry_times` | 网络或空响应等可重试错误的请求次数，默认 5 |
| `retry_interval_seconds` | 重试之间的等待时间，默认 6 秒 |
| `cache_path` | LLM 请求结果的本地缓存目录 |
| `log_dir_path` | 请求与缓存事件的日志目录 |

例如，使用其他 OpenAI-compatible 服务时只需替换端点、模型名和密钥：

~~~python
llm = LLM(
    key="your-api-key",
    url="https://your-provider.example/v1",
    model="your-model",
    token_encoding="o200k_base",
    timeout=120,
    retry_times=5,
    retry_interval_seconds=6,
)
~~~

翻译和 XML 结构修复默认共用同一个 `llm`。如果两项工作需要不同的模型或采样参数，可以
分别传入 `translation_llm` 和 `fill_llm`；至少需要提供一个 `llm`，或同时提供这两个专用
配置：

~~~python
translation_llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="translation-model",
    token_encoding="o200k_base",
    temperature=0.7,
)
fill_llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="structure-model",
    token_encoding="o200k_base",
    temperature=0.2,
)

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    translation_llm=translation_llm,
    fill_llm=fill_llm,
)
~~~

## 提示词、重试和并发

### 自定义翻译提示词

`user_prompt` 会作为额外的翻译要求传给文本 LLM。适合补充术语、语气、专名和格式方面的
要求；它不会替代 pdf-craft 用于约束输出结构的系统提示词：

~~~python
PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    user_prompt="使用正式书面语，保留人名、专业术语和脚注编号。",
)
~~~

### 重试与分组大小

- `max_retries` 控制单个翻译或结构修复任务失败后的最大重试次数，默认值为 5。
- `max_group_tokens` 控制一次提交给翻译器的文本分组大小，默认值为 2600。增大它可以
  减少请求次数，但会增加单次请求的上下文和失败重试成本。
- LLM 配置中的 `retry_times` 控制底层请求遇到网络错误或空响应时的重试；它和
  `max_retries` 处于不同层次，前者针对请求传输，后者针对翻译结构任务。

### 并发翻译

`concurrency` 控制同时处理的翻译任务数，默认值为 1。建议先使用默认值确认服务可用，
再根据供应商的速率限制逐步提高。输出顺序会保持稳定，但并发提高会增加同时进行的请求数
和服务费用：

~~~python
PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    concurrency=4,
)
~~~

## 缓存与中断恢复

给 `LLM` 设置 `cache_path` 后，成功的 LLM 响应会以内容哈希写入本地目录。相同请求再次
出现时会优先命中缓存，从而减少重复请求；翻译中断后重新运行，也可以复用已经成功的请求：

~~~python
llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
    cache_path="translation-cache",
    log_dir_path="translation-logs",
)
~~~

缓存键包含请求消息、模型、采样参数、协议版本和目标语言等信息。建议为不同书籍或不同
翻译任务使用不同缓存目录，方便管理和排查问题。翻译失败时，临时缓存文件会被清理，只有
成功的响应才会保留。

## 进度与失败回调

`on_progress` 接收一个从 `0.0` 到 `1.0` 的进度值。进度按目录、元数据和章节加权：如果
存在目录和元数据，它们各占 5%，章节占 90%；没有对应内容时，剩余权重会分配给实际存在
的部分。

`on_fill_failed` 接收 `FillFailedEvent`。它描述 XML 结构修复过程中发生的错误：

- `error_message`：错误消息。
- `retried_count`：当前已尝试的次数。
- `over_maximum_retries`：是否已经超过最大重试次数。为 `True` 时，错误可能影响最终 EPUB，
  应记录并检查输出。

~~~python
from pdf_craft import FillFailedEvent

def on_progress(progress: float) -> None:
    print(f"翻译进度：{progress:.0%}")

def on_fill_failed(event: FillFailedEvent) -> None:
    if event.over_maximum_retries:
        print(f"未恢复的结构修复错误：{event.error_message}")

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    on_progress=on_progress,
    on_fill_failed=on_fill_failed,
)
~~~

## 输入、输出与限制

- 输入必须是可读取的 EPUB 文件，输出路径应与输入路径不同。
- pdf-craft 会迁移 EPUB 压缩包中的 `mimetype`，并按书籍 spine 处理章节。
- 目录和书籍元数据会参与翻译；没有目录或元数据时，对应阶段会被跳过。
- `APPEND_BLOCK` 适用于章节正文的双语排版；目录和元数据仍使用内联追加，以保持 EPUB
  结构。
- EPUB 翻译只处理文本和相关 XML 结构，不会重新 OCR，也不会把 PDF 写回 PDF。
- 目标文件生成过程中发生不可恢复错误时，应检查 `on_fill_failed` 报告和日志目录，并使用
  新的输出路径重新运行；不要把未完成的输出当作完整译本。
