# EPUB 翻译指南

pdf-craft 可以直接翻译已有 EPUB。输入已经是 EPUB 时，不需要 OCR；翻译流程会读取书籍
中的章节、目录和可翻译的元数据，调用文本 LLM，并生成一个新的 EPUB 文件。它适合两类结果：只保留
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

章节正文会严格按照所选模式提交。目录和可翻译的书籍元数据也会翻译；在 `APPEND_BLOCK` 模式下，
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
| `retry_times` | 网络或空响应等可重试错误的额外重试次数，默认 5（首次请求之外最多再试 5 次） |
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

- `max_retries` 只控制 XML 结构修复循环允许的最大总尝试次数，默认值为 5；因此默认最多
  包含 4 次重试。它不限制文本翻译请求，后者由 LLM 的 `retry_times` 控制。
- `max_group_tokens` 控制一次提交给翻译器的文本分组大小，默认值为 2600。增大它可以
  减少请求次数，但会增加单次请求的上下文和失败重试成本。
- LLM 配置中的 `retry_times` 控制文本翻译请求遇到网络错误或空响应时的额外重试次数；默认
  值 5 表示首次请求加最多 5 次重试（最多 6 次尝试）。它和 `max_retries` 处于不同层次。

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

给 `LLM` 设置 `cache_path` 后，成功的翻译请求响应会以内容哈希写入本地目录。相同翻译请求
再次出现时会优先命中缓存，从而减少重复请求；翻译中断后重新运行，也可以复用已经成功的
翻译请求。XML 结构修复请求明确不使用这个缓存，因此不会命中或写入其中；恢复时仍会重新
生成输出 EPUB，而不是从中间输出文件继续写入：

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
成功的翻译响应才会保留。

## 进度与失败回调

`on_translation_event` 接收底层 `TranslationEvent` 事件，报告翻译范围以及 TOC、metadata、
chapter item 的开始、完成和源文本字符统计。字符统计不是 token，也不是固定比例。相同回调
也适用于 package 和 PDF 翻译流程。

`on_fill_failed` 接收 `FillFailedEvent`。它描述 XML 结构修复过程中发生的错误：

- `error_message`：错误消息。
- `retried_count`：当前已尝试的次数。
- `over_maximum_retries`：是否已经达到尝试上限并耗尽修复机会。每次结构修复失败都会先收到
  一个 `False` 事件；最后一次耗尽上限的事件为 `True`，此时错误可能影响最终 EPUB，应记录
  并检查输出。

~~~python
from pdf_craft import FillFailedEvent, TranslationEventKind

def on_translation_event(event) -> None:
    if event.kind == TranslationEventKind.PROGRESS:
        print(event.completed_characters, event.total_characters)

def on_fill_failed(event: FillFailedEvent) -> None:
    if event.over_maximum_retries:
        print(f"未恢复的结构修复错误：{event.error_message}")

PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    on_translation_event=on_translation_event,
    on_fill_failed=on_fill_failed,
)
~~~

## 输入、输出与限制

- 输入必须是可读取的 EPUB 文件，输出路径应与输入路径不同。
- pdf-craft 会迁移 EPUB 压缩包中的 `mimetype`，并按书籍 spine 处理章节。
- 目录和部分书籍元数据会参与翻译。`language`、`identifier`、`date`、`meta`、`contributor`
  字段属于技术或标识信息，会被明确跳过，不会发送给 LLM；其他包含文本的元数据字段才会
  进入翻译流程。如果 TOC 文件存在但没有条目，目录阶段会没有任务并由其他内容继续；如果
  EPUB 根本没有可识别的 TOC 文件，当前实现会直接报错，而不是跳过该阶段。
- `APPEND_BLOCK` 适用于章节正文的双语排版；目录和元数据仍使用内联追加，以保持 EPUB
  结构。
- EPUB 翻译只处理文本和相关 XML 结构，不会重新 OCR，也不会把 PDF 写回 PDF。
- 目标文件生成过程中发生不可恢复错误时，应检查 `on_fill_failed` 报告和日志目录，并使用
  新的输出路径重新运行；不要把未完成的输出当作完整译本。
