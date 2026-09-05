# pdf-craft 2.0 API 参考

本文面向需要把 pdf-craft 集成到自己程序中的用户。入门流程请先阅读仓库根目录的
README；本文只说明稳定的公共导入和它们如何组合。示例默认使用：

```python
from pdf_craft import PDFCraft, PDFOptions
```

## 公共入口

`pdf_craft` 包顶层导出常用类型。最主要的入口是 `PDFCraft`，它把 PDF 提取、渲染、
翻译和 PDF 写回组合成一组方法。下面这些对象可直接从 `pdf_craft` 导入：

- `PDFCraft`、`PDFOptions`、`ExtractionOptions`
- `PDFCraftExtraction`、`PDFExtractor`
- 六种 OCR 配置对象和 `OCRConfig`
- `predownload_models`
- `LLM`
- `ExtractionTransformer`、`ChapterExtractionTransformer`、`ChapterXMLTransformer`、
  `XMLTranslator`、`SubmitKind`
- `BookMeta`、`TableRender`、`LaTeXRender`
- `OCRTokensMetering`、`OCREvent`、`OCREventKind`、`TranslationEvent`、
  `TranslationEventKind`、`TranslationItemKind`、`FillFailedEvent`
- `PDFHandler`、`DefaultPDFHandler`、`PDFDocument`、`DefaultPDFDocument`、
  `PDFDocumentMetadata`
- `PDFPatcher`、`PDFReplacement`、`PDFSkippedReplacement`、`PatchTextOptions`、
  `PDFTranslationPipeline`
- `PDFError`、`OCRError`、`IgnorePDFErrorsChecker`、
  `IgnoreOCRErrorsChecker`
- `translate_epub`

`ChapterTransformer` 是公共协议，但导入路径为
`from pdf_craft.transformer import ChapterTransformer`，而不是包顶层。本文不把以下内容当作
公共扩展点：内部 engine、`pdf_craft_tool` CLI、`pdf_craft` 的私有模块路径，以及
`doc-page-extractor` 的内部 extractor/factory。

所有翻译入口都可以接收 `on_translation_event`。事件类型与 `OCREvent` 风格一致，
包含 `START`、`ITEM_START`、`ITEM_COMPLETE`、`PROGRESS` 和 `COMPLETE`；item 类型为
TOC、metadata 或 chapter。字符统计是可翻译源文本的 Unicode 字符数，不是 token 数，
也不是预先计算的百分比。chapter 可以来自 EPUB，也可以来自 PDF OCR 生成的
`PDFCraftExtraction`。

`ITEM_START`、`PROGRESS` 和 `ITEM_COMPLETE` 事件会提供当前 item 的已完成字符数和总字符数；
范围事件会提供整个翻译范围的累计字符数。调用方可以据此自行计算 item 或整体百分比，库不
提供固定比例。

## PDFCraft

### 创建实例

```python
craft = PDFCraft(pdf=PDFOptions(...))
```

`PDFCraft()` 本身不会初始化 OCR。只做 EPUB → EPUB 翻译，或只渲染已有 extraction
时，可以不传 `PDFOptions`。凡是需要从 PDF 提取内容的操作，都必须提供 PDF 配置，或者使用
已经准备好的测试 engine（后者是测试用途，不属于普通应用集成方式）。

### PDFOptions

`PDFOptions` 保存一次 `PDFCraft` 实例长期使用的 PDF 基础设施：

```python
PDFOptions(
    ocr=None,                   # OCRConfig；省略时默认为 DeepSeek OCR local 配置
    pdf_handler=None,           # PDFHandler；省略时使用默认处理器
    models_cache_path=None,     # local OCR 模型缓存目录
    local_only=False,           # 禁止 local OCR 下载缺失模型
)
```

`ocr` 与 `models_cache_path`、`local_only` 是两套互斥的配置方式：如果显式传入 `ocr`，
不能再同时传入 `models_cache_path` 或将 `local_only` 设为 `True`，否则会抛出 `ValueError`。
远程 OCR 直接把对应 vendor 配置传给 `ocr`；
本地 OCR 可以把模型缓存和离线选项写进 local 配置，也可以使用 `models_cache_path` 和
`local_only` 的默认 local OCR 路径。

### 自定义 PDFHandler

`PDFHandler` 是替换 PDF 读取和页面渲染实现的协议。默认的
`DefaultPDFHandler(poppler_path=...)` 使用 `pypdf` 读取元数据、使用 Poppler 渲染页面；系统
PATH 中没有 Poppler 时，可以把其安装目录传给 `poppler_path`。只有接入其他 PDF 渲染器时才需要
自定义 handler，并将它传给 `PDFOptions(pdf_handler=handler)`。

一个 handler 需要实现 `open(pdf_path: Path) -> PDFDocument`。它返回的 document 必须提供：

- `pages_count` 属性，返回页面总数；
- `metadata()`，返回 `PDFDocumentMetadata`；
- `page_size(page_index)`，返回以英寸计的宽、高；
- `render_page(page_index, dpi)`，返回 `PIL.Image.Image`；
- `close()`，释放打开的文档资源。

这些方法的 `page_index` 均从 1 开始。调用方负责在使用完成后关闭自定义 document；框架自己的
提取和写回流程会关闭由 handler 打开的 document。

### ExtractionOptions

`ExtractionOptions` 控制一次 PDF 提取。常用字段如下：

```python
ExtractionOptions(
    page_indexes=None,              # 要处理的 1-based 页码集合
    ocr_size="gundam",              # tiny/small/base/large/gundam
    dpi=None,
    max_page_image_file_size=None,
    max_ocr_tokens=None,
    max_ocr_output_tokens=None,
    includes_cover=False,
    includes_footnotes=False,
    generate_plot=False,
    toc_assumed=False,
    toc_llm=None,
    ignore_pdf_errors=False,
    ignore_ocr_errors=False,
    aborted=lambda: False,
    on_ocr_event=lambda event: None,
)
```

`page_indexes` 使用从 1 开始的 PDF 页码。`toc_assumed` 决定是否把目录页作为输入线索，
默认值为 `False`；如果需要目录页检测，应在 EPUB 或 Markdown 提取时显式传入 `True`。
`toc_llm` 是可选的目录层级分析
LLM，不是 OCR 配置，也不是章节翻译器。

`aborted` 返回 `True` 时请求中止当前任务。`on_ocr_event` 会收到每次 OCR 事件，可用于
进度、token 或日志记录。`ignore_pdf_errors` 和 `ignore_ocr_errors` 接受布尔值，也接受
根据错误对象返回布尔值的 callable；它们只影响页面级错误是否继续处理。

## PDF 工作流

### 提取为 PDFCraftExtraction

```python
extraction = craft.extract_pdf(
    "input.pdf",
    "work/book.pcex",
    ExtractionOptions(page_indexes={1, 2}),
)
```

`extract_pdf` 要求显式提供 `.pcex` 输出路径，因为返回的 `PDFCraftExtraction` 是可长期保存和
跨机器交换的中间产物。普通目录不是公开输入。带计量版本返回二元组：

```python
extraction, metering = craft.extract_pdf_with_metering(
    "input.pdf", "work/book.pcex", ExtractionOptions()
)
print(metering.input_tokens, metering.output_tokens)
```

`PDFExtractor` 是面向已有提取 backend 的低层包装器，构造时需要传入提取 backend；
backend。常规应用不应自行构造它，而应通过 `PDFCraft.extract_pdf*()` 获得已正确配置 OCR、PDF
handler 和中断处理的提取流程。

### 直接转换为 Markdown 或 EPUB

一键转换方法默认创建系统临时分析目录，并在成功或异常后清理。`analysing_path` 可保留 OCR、
图表等诊断信息；`extraction_path` 可额外导出稳定的 `.pcex`。完整转换在内部直接使用
`analysing_path/extraction/`，不会为衔接前后端执行无意义的压缩与解压：

```python
craft.convert_pdf_to_markdown(
    "input.pdf", "book.md", extraction_path="work/book.pcex"
)
craft.convert_pdf_to_epub(
    "input.pdf", "book.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)
```

两个方法都返回 `OCRTokensMetering`。`convert_pdf_to_markdown` 另接受 `assets_path`，
用于把渲染出的图片资源写到指定目录；EPUB 的 `lan`、`table_render`、`latex_render` 和
`inline_latex` 控制 EPUB 输出格式。
两个方法都可以通过 `translator` 和 `on_translation_event` 在转换时完成一次翻译。

### 从已有 extraction 渲染

```python
craft.render_markdown(extraction, "book.md", assets_path="book-assets")
craft.render_epub(
    extraction, "book.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)
```

渲染不会重新 OCR，也不会读取 PDF。Markdown 要求 extraction 校验通过；EPUB 额外要求
`toc.xml`。Markdown 可选复制图片资源；EPUB 从 `manifest.json` 读取默认元数据和语言，调用时
显式提供的 `book_meta` / `lan` 优先。

### PDF 转换时翻译

PDF 转换入口可以传入一个章节翻译器和提交模式，在渲染前完成一次翻译：

```python
craft.convert_pdf_to_markdown(
    "input.pdf", "translated.md", translator=translator,
    submit=SubmitKind.REPLACE,
)
```

自定义章节变换器可以实现 `ChapterTransformer` 协议：

```python
from pdf_craft.transformer import ChapterTransformer

def accepts_transformer(transformer: ChapterTransformer) -> None:
    ...
```

这是低层协议：章节的具体 XML/布局对象不从包顶层导出。需要由文本 LLM 完成章节翻译时，
请使用下一节的 `XMLTranslator` 和 `ChapterXMLTransformer` 组合，而不是自行猜测章节内部
结构。`SubmitKind.REPLACE`、`SubmitKind.APPEND_TEXT` 和 `SubmitKind.APPEND_BLOCK` 的
含义取决于变换器；PDF 写回仅拒绝 `APPEND_BLOCK`。

### 翻译并写回 PDF

```python
extraction = craft.extract_pdf("input.pdf", "work/book.pcex")
craft.translate_pdf(
    "input.pdf", extraction, "translated.pdf", translator,
)
```

`translate_pdf` 会生成翻译后的临时 extraction，再执行 PDF 写回。写回只会替换 extraction
中记录了来源坐标的原始 PDF 文本，不是通用 PDF 排版器；输入 PDF 必须来自同一源文件，并且
`pages.xml` 要包含完整页面几何。PDF 写回不支持 `APPEND_BLOCK`；
`APPEND_TEXT` 可以把双语内容放进原文本框，但更容易超过原有版面，通常优先选 `REPLACE`。

如果已经有翻译后的 `.pcex`，也可以单独写回：

```python
craft.patch_pdf_with_extraction("input.pdf", "work/translated.pcex", "translated.pdf")
```

## PDFCraftExtraction 与 `.pcex`

`PDFCraftExtraction` 是带原始 PDF 页码和 bbox 映射的结构化中间对象。公开持久化和交换格式
统一为 `.pcex`（ZIP），通过以下方式加载：

```python
extraction = PDFCraftExtraction.open("work/book.pcex")
extraction.validate()
```

归档固定包含 `manifest.json`、`pages.xml`、`chapters/`、`assets/`，并可选包含 `toc.xml` 与
`cover.png`。manifest 保存格式版本、producer、创建时间及书名、作者、出版社、语言等文档
元数据；pages 保存 1-based 页码、OCR 像素坐标空间、实际 DPI 和各页像素宽高。OCR 响应、
plot 和 done 标记属于 analysis 诊断信息，不进入 `.pcex`。

加载时会检查版本、ZIP 路径安全、必需组件、XML、页面引用、bbox 和资源引用；非法、损坏或
不支持版本的包会被拒绝。所有后端只读取 extraction 内字段，不会回退读取 analysis/OCR 缓存。

## 翻译与变换接口

### ChapterTransformer

章节变换器实现一个 `transform(chapter) -> chapter` 方法。它可以修改章节文本、段落或布局，
并被 `translate_extraction` 和 `translate_pdf` 使用。实现该低层协议时，需从
它的实际定义处导入 `Chapter`：

```python
from pdf_craft.extractor.chapter.chapter import Chapter
from pdf_craft.transformer import ChapterTransformer

class KeepChapterStructure:
    def transform(self, chapter: Chapter) -> Chapter:
        # 修改 chapter 后返回同一个 Chapter；必须保留来源坐标和页面信息。
        return chapter

transformer: ChapterTransformer = KeepChapterStructure()
```

章节布局类型不是顶层 facade 的日常 API。自行编辑它们时必须保留原有页面来源信息，否则 PDF
写回无法定位原文；纯文本翻译应优先使用下一节的 `XMLTranslator`，避免依赖章节内部结构。

### ExtractionTransformer

extraction 变换器实现：

```python
def transform(extraction: PDFCraftExtraction, output_path: Path) -> PDFCraftExtraction:
    ...
```

它负责把一个完整 extraction 写入新的 `.pcex`，并返回新的 `PDFCraftExtraction`。

### 使用 XMLTranslator 翻译 PDF 章节

`XMLTranslator` 是包顶层导出的结构化文本翻译器。它需要分别提供翻译文本和修复 XML
结构的 LLM；同一个 `LLM` 可以同时承担两项工作。将它包装为 `ChapterXMLTransformer` 后，
即可作为 `translator` 传给 PDF 转换或 extraction 翻译入口：

```python
from pdf_craft import (
    ChapterXMLTransformer,
    LLM,
    SubmitKind,
    XMLTranslator,
)

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="your-model",
    token_encoding="o200k_base",
)
xml_translator = XMLTranslator(
    translation_llm=llm,
    fill_llm=llm,
    target_language="zh",
    user_prompt=None,
    ignore_translated_error=False,
    max_retries=5,
    max_fill_displaying_errors=10,
    max_group_score=2600,
)
translator = ChapterXMLTransformer(xml_translator)
craft.convert_pdf_to_markdown(
    "input.pdf", "translated.md", translator=translator,
    submit=SubmitKind.REPLACE,
)
```

`translation_llm` 负责生成译文，`fill_llm` 负责在必要时修复 XML 结构。两个 LLM 可以使用
不同的模型、提示参数、缓存或重试策略。若目标是双语 Markdown 或 EPUB，可把提交模式设为
`APPEND_TEXT` 或 `APPEND_BLOCK`；PDF 不支持 `APPEND_BLOCK`，而 `APPEND_TEXT` 虽可使用，
但需要为双语文本的版面溢出承担处理成本，因此通常推荐 `REPLACE`。

已有可复用 extraction 时，调用 `translate_extraction` 并显式指定新的 `.pcex`：

```python
translated_extraction = craft.translate_extraction(
    extraction,
    "work/translated.pcex",
    ChapterXMLTransformer(xml_translator),
    submit=SubmitKind.REPLACE,
)
```

### 翻译 EPUB

```python
from pdf_craft import LLM, PDFCraft, SubmitKind

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="your-model",
    token_encoding="o200k_base",
)
PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK, llm=llm,
)
```

`REPLACE` 只输出译文，`APPEND_TEXT` 在原文后追加内联译文，`APPEND_BLOCK` 追加独立译文
块，适合双语阅读。`translate_epub` 还支持 `user_prompt`、`max_retries`、`max_group_tokens`、
`concurrency`、`translation_llm`、`fill_llm`、`on_translation_event` 和 `on_fill_failed`；完整行为
和回调字段请参阅 EPUB 翻译专题文档。

对于不需要保留 `PDFCraft` 实例的 EPUB-only 程序，也可直接从顶层导入同一能力：

```python
from pdf_craft import SubmitKind, translate_epub

translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE, llm=llm,
)
```

`PDFCraft().translate_epub()` 会将其翻译关键字参数转发给顶层 `translate_epub()`；两种调用都
不需要 PDF OCR 配置。顶层函数显式接受前文列出的 EPUB 翻译参数。

## 低层 PDF 写回 API

通常应使用 `PDFCraft.patch_pdf_with_extraction()` 或 `PDFCraft.translate_pdf()`。顶层也公开了较低层的
写回组件，供已经能自行生成替换坐标与文字的集成方使用：

- `PDFReplacement` 描述一段待替换文本：`page_index`、像素坐标 `bbox`、`text`、OCR 画布尺寸
  `page_pixel_size`，以及可选的 `dpi`、`reading_order`。
- `PDFPatcher(options=PatchTextOptions(...), pdf_handler=...)` 通过 `.patch(source_path,
  target_path, replacements)` 写出 PDF。它接受任意通过字段校验的 `PDFReplacement`，不要求这些
  替换项来自 `PDFCraftExtraction` 或 OCR；`page_pixel_size` 仅用于把像素 `bbox` 换算为 PDF 坐标，
  patcher 不会验证它是否等于源页的实际渲染尺寸。调用方必须自行保证页码、坐标与尺寸对应源 PDF。
  `PatchTextOptions` 控制字体、字号、内边距、对齐和 `overflow` 策略；`overflow="error"`（默认）
  在文字无法放入原框时失败，`"skip"` 则把对应项记录在 `patcher.skipped_replacements` 中。
- `PDFTranslationPipeline` 可将一个 `PDFCraftExtraction` 与 `ChapterTransformer` 或
  `Callable[[str], str]` 直接写回 PDF；其 `.patch()` 则把 extraction 已有的文字写回。这是
  facade 的底层组成部分，普通应用无需直接构造。它只从 extraction 中带来源坐标的 `text` 和
  `sub_title` 布局收集替换项。

它们都不会重排 PDF 页面；页码从 1 开始。

## OCR 配置对象

六种配置对象分为 local 和 vendor 两组：

```python
DeepSeekOCRLocalConfig(
    models_cache_path=None,
    local_only=False,
    enable_devices_numbers=None,
)
DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="...",
    model="deepseek-ocr",
    temperature=None,
    top_p=None,
    max_tokens=8000,
    timeout_seconds=180,
)
```

`DeepSeekOCR2LocalConfig` 与 `DeepSeekOCRLocalConfig` 字段相同；
`UnlimitedOCRLocalConfig` 字段相同。`DeepSeekOCR2VendorConfig` 字段与
`DeepSeekOCRVendorConfig` 相同。`UnlimitedOCRVendorConfig` 使用 `ak`、`sk`、可选的
`base_url`、`poll_interval_seconds` 和 `timeout_seconds`。local 配置使用本机 CUDA 和模型
缓存，vendor 配置使用远程服务；六种配置不能混用。

`predownload_models(models_cache_path=None, pdf_handler=None, revision=None, ocr=None)` 可以提前
下载 local 模型。传入 `ocr` 时不能再同时传入 `models_cache_path`；`pdf_handler` 仅在需要替换
默认 PDF handler 时使用。`local_only=True` 会禁止缺失模型联网下载。不同模型支持的 `ocr_size`
preset 不完全相同，应以 OCR 配置指南和具体 backend 的约束为准。

## LLM

`LLM` 是 OCR 之外的文本 LLM 配置对象，用于章节翻译、EPUB 翻译和可选的目录层级分析：

```python
LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="your-model",
    token_encoding="o200k_base",
    timeout=None,
    top_p=None,
    temperature=None,
    retry_times=5,
    retry_interval_seconds=6.0,
    cache_path=None,
    log_dir_path=None,
)
```

`LLM` 只保存配置，实际请求由 pdf-craft 内部运行时发起。OCR endpoint 和文本翻译 LLM
是两套独立配置，不要把 OCR 配置当作翻译 LLM 使用。

## 计量、事件与错误

`OCRTokensMetering` 提供 `input_tokens` 和 `output_tokens`，由带 `with_metering` 的提取
方法以及一键转换方法返回。`OCREvent` 和 `OCREventKind` 用于 `on_ocr_event` 回调，适合
记录页面级 OCR 状态。

`PDFError`、`OCRError` 可用于日志和错误判断。`ignore_pdf_errors`、`ignore_ocr_errors` 可
传 `True`、`False` 或 callable。`FillFailedEvent` 用于 EPUB XML 结构修复失败回调，包含
`error_message`、`retried_count` 和 `over_maximum_retries`。

`ExtractionOptions.aborted` 返回 `True`，以及 `max_ocr_tokens` /
`max_ocr_output_tokens` 达到上限时，当前 PDF facade 会透出 OCR backend 的中断异常；它尚未把
这些异常适配为带 `OCRTokensMetering` 的 pdf-craft 统一异常。需要在中断前保留进度或 token
统计时，应通过 `on_ocr_event` 持续记录事件，并按所选 backend 的异常类型处理。

## 组合建议

- 一次性 PDF → Markdown/EPUB：使用 `PDFCraft(pdf=PDFOptions(...)).convert_pdf_to_*`，默认
  自动管理临时 analysis；需要中间产物时传 `extraction_path`。
- 需要重复渲染、翻译或写回：先用 `extract_pdf` 保存 `.pcex`，再调用 `render_*`、
  `translate_extraction` 或 `patch_pdf_with_extraction`。
- PDF → 翻译 PDF：使用同一源 PDF 生成 extraction，再调用 `translate_pdf`；不要把 EPUB 的
  `APPEND_BLOCK` 语义用于 PDF。
- EPUB → EPUB：使用 `PDFCraft().translate_epub`，只配置文本 LLM，不需要 OCR。
