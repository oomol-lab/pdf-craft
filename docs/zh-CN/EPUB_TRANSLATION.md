# EPUB 翻译指南

pdf-craft 可以直接翻译已有 EPUB。输入已经是 EPUB 时，不需要 OCR；翻译流程会读取书籍
中的章节、目录和可翻译的元数据，调用文本 LLM，并生成一个新的 EPUB 文件。它适合两类结果：只保留
目标语言的单语版本，或保留原文并追加译文的双语版本。

## 最小示例

`PDFCraft.translate_epub` 是库层的 EPUB 翻译入口。你需要准备一个 OpenAI-compatible 的
文本 LLM 配置，并指定输入文件、输出文件、目标语言和译文提交模式：

~~~python
from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, LLM, PDFCraft, SubmitKind,
)

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="gpt-4.1-mini",
    token_encoding="o200k_base",
)
executor = ConcurrentExecutor(FixedCapacity(4))

PDFCraft().translate_epub(
    "source.epub",
    "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    executor=executor,
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

章节正文会严格按照所选模式提交。目录、XHTML 文档标题和可翻译的书籍元数据也会翻译；EPUB 2 的 NCX `docTitle` 跟随翻译后的书名，`dc:language` 直接写为 `target_language`。在 `APPEND_BLOCK` 模式下，
为了保持目录和元数据的结构可用，它们的译文会以内联方式接在原文后，而不是创建新的目录
或元数据块。

下面两个调用分别生成单语和双语 EPUB：

~~~python
# 单语：输出中只保留中文
PDFCraft().translate_epub(
    "source.epub", "translated.zh.epub",
    target_language="zh", submit=SubmitKind.REPLACE, llm=llm,
    executor=executor,
)

# 双语：原文保留，译文按独立文本块追加
PDFCraft().translate_epub(
    "source.epub", "bilingual.zh.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK, llm=llm,
    executor=executor,
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
    executor=executor,
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
    executor=executor,
    user_prompt="使用正式书面语，保留人名、专业术语和脚注编号。",
)
~~~

### 重试与分组大小

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

### 并发翻译

`executor` 控制真实 LLM 请求共享的供应商容量。翻译和 XML 修复若使用同一供应商配额，
应复用同一个执行器。`window` 默认值为 1，限制在途翻译 group 的数量。某个 group 完成后即释放
执行名额，即使更早的结果仍未返回；已完成结果在磁盘等待有序交付。执行器独立限制真实供应商
请求容量。XML 预取只使用 `window`，供应商容量必须通过执行器显式提供。
两个模型使用不同配额时，可分别传 `translation_executor` 与 `fill_executor`：

~~~python
PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh",
    submit=SubmitKind.APPEND_BLOCK,
    llm=llm,
    executor=executor,
    window=4,
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

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

- `error_message`：错误消息。
- `retried_count`：当前已尝试的次数。
- `over_maximum_retries`：是否已经达到尝试上限并耗尽修复机会。每次结构修复失败都会先收到
  一个 `False` 事件；最后一次耗尽上限的事件为 `True`，此时错误可能影响最终 EPUB，应记录
  并检查输出。

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

def on_translation_event(event) -> None:
    if event.kind == TranslationEventKind.PROGRESS:
        print(event.completed_characters, event.total_characters)

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

## 输入、输出与限制

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。


## 在完整 EPUB 生成前获取译文

`on_body` 接收 `EpubFragmentContent(epubcfi, original, translated)`。它对应已采纳的原子翻译
片段，可能比一个自然段更小；原译文均为纯文本，不包含 LLM 窗口重叠的上下文。
可选范围 CFI 定位原始输入 EPUB，不指向译文文件。`epubcfi` 类型为 `str | None`：XHTML
保留精确定位，`text/html` 或明确缺少可靠来源信息时使用 `None`，原译文仍正常交付。
混合格式书籍的其他片段保留可靠 CFI，调用方使用定位前应判断是否为 `None`。
仅降级明确的定位能力缺失；解析、翻译、意外映射错误及用户回调异常仍传播。
支持从嵌套 OPF 通过 `../` 等相对路径定位章节。

`on_metadata` 一次性交付 OPF 书籍元信息的完整 `MetadataContent`。双语模式中，回调依然将
译文视图与原文分开，最终 EPUB 保留所选追加方式。正文回调不包含导航、文档 head/title 或
书籍元信息。

两条回调均可选，支持同步或异步函数；异步回调会被等待，异常会终止操作。输出 EPUB 只在
成功后以完整文件提交。示例、CFI 定位、顺序及来源有效期见[增量回调指南](STREAMING.md)。


统一策略、默认严格失败和全局全部降级的规则见 [错误处理](ERROR_HANDLING.md)。
