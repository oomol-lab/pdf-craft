# pdf-craft API 参考

本文面向需要把 pdf-craft 集成到自己程序中的用户。入门流程请先阅读仓库根目录的
README；本文只说明稳定的公共导入和它们如何组合。示例默认使用：

```python
from pdf_craft import AsyncPDFCraft, PDFCraft, PDFOptions
```

## 异步与同步门面

`AsyncPDFCraft` 是服务端、Notebook 和其他 asyncio 程序的首选入口。它为
`PDFCraft` 的提取、渲染、PCEX 翻译、PDF 回填、EPUB 翻译和两个一站式转换流程提供
对应的异步方法。OCR、PDF/ZIP/图片处理、Qt 排版等同步第三方库会进入有界执行域，
不会阻塞调用方事件循环；翻译并发和 LLM 网络请求则使用原生 asyncio。

```python
craft = AsyncPDFCraft(pdf=PDFOptions(ocr=your_ocr_config))
extraction = await craft.extract_pdf("input.pdf", "book.pcex")
await craft.render_markdown(extraction, "book.md")
```

OCR 和翻译事件回调既可以是普通函数，也可以是 `async def`；它们在调用方事件循环
线程执行，异步回调会被等待。取消异步任务时，原生网络请求和子进程会被取消，线程池中
支持协作取消的阶段会通过原有 abort 回调收到信号；直到工作线程真正退出后，调用方才会
收到取消完成，因此临时工作区会保留到 worker 的最后写入和清理结束。
该规则覆盖所有线程池边界，包括同步扩展 transformer 和同步 PDF handler，而不只限于支持
协作取消的 OCR worker。
异步 PCEX 导出会先写同目录临时归档，再通过一次原子替换发布。若取消先到达，调用会等待
writer 收尾、删除临时归档并保持目标不存在；若发布边界先完成，则该取消视为晚于成功完成。
`pdftotext`、Ghostscript、LaTeX 等外部工具在受跟踪的 POSIX 进程组或 Windows Job Object
中运行；取消或内部超时会终止命令及其派生进程，正常完成或失败也会回收晚于父进程退出的
后代。取消 Qt worker 时，会先完成这些清理，再结束 worker 本身。

扩展实现采用仅异步的 `ChapterTransformer` 协议，实现
`async def transform(chapter)`；异步门面会直接在调用方事件循环中等待它。
图片/表格独立翻译采用仅异步的 `AnchoredContentTransformer`，提供
`async def transform_assets(assets)`。
`PDFOptions.pdf_handler` 也接受 `AsyncPDFHandler`：其 `open()` 返回
`AsyncPDFDocument`，并提供可等待的 `pages_count()`、`metadata()`、
`page_size()`、`render_page()` 和 `close()`。SDK 会在同步 OCR 边界进行适配，
不会把这些协程送进工作线程执行。PDF 回填时，需要的源页面会先在调用方事件循环中
等待并临时落盘，只有页面路径和元数据会跨入隔离的 Qt 进程。

异步 XML/EPUB 翻译的 `on_fill_failed` 同样可以是普通函数或 `async def`；每次修复失败
通知都在调用方事件循环中执行，异步 callback 会在进入下一次修复步骤前被完整等待。

`PDFCraft` 作为异步实现之上的兼容适配层，继续提供适合普通脚本的同步 API；但不能从已经运行事件循环的线程中
调用，否则会明确抛出 `RuntimeError`，而不会嵌套启动事件循环。此时应改用
`AsyncPDFCraft`。

用户只需在构造时选择门面，之后两种模式使用相同的方法名。
`PDFCraftExtraction` 是不暴露内部操作的句柄：通过 `PDFCraft.open_extraction()` /
`export_extraction()` 或 `AsyncPDFCraft` 上的可等待同名方法打开、导出，再把句柄交回门面。
高级组件仅提供无后缀的异步方法，包括 `PDFExtractor.extract`、
`MarkdownRenderer.render`、`EpubRenderer.render` 和各 transformer API。已有 EPUB
翻译同样只通过门面调用。模型预下载属于环境准备，只保留同步的 `predownload_models(...)`。

## 公共入口

`pdf_craft` 包顶层导出常用类型。最主要的入口是 `PDFCraft`，它把 PDF 提取、渲染、
翻译和 PDF 写回组合成一组方法。下面这些对象可直接从 `pdf_craft` 导入：

- `AsyncPDFCraft`、`PDFCraft`、`PDFOptions`、`ExtractionOptions`
- `PDFCraftExtraction`、`PDFExtractor`
- 六种 OCR 配置对象和 `OCRConfig`
- `predownload_models`
- `LLM`
- `ExtractionTransformer`、`ChapterExtractionTransformer`、`ChapterXMLTransformer`、
  `AnchoredContentExtractionTransformer`、`AnchoredContentTransformer`、
  `AnchoredContentXMLTransformer`、`XMLTranslator`、`SubmitKind`
- `BookMeta`、`TableRender`、`LaTeXRender`
- `OCRTokensMetering`、`OCREvent`、`OCREventKind`、`TranslationEvent`、
  `TranslationEventKind`、`TranslationItemKind`、`FillFailedEvent`
- `PDFHandler`、`AsyncPDFHandler`、`DefaultPDFHandler`、`PDFDocument`、
  `AsyncPDFDocument`、`DefaultPDFDocument`、
  `PDFDocumentMetadata`
- `PDFPatcher`、`PDFReplacement`、`PDFReplacementRegion`、`PDFInlineFormula`、
  `PatchTextOptions`、`PatchTextStyle`、`FontResolution`、`QTextParagraphFiller`、`EraseOptions`、
  `PDFTranslationPipeline`
- `PDFError`、`OCRError`、`NoUsableFillPagesError`、`IgnorePDFErrorsChecker`、
  `IgnoreOCRErrorsChecker`、`IgnoreFillErrorsChecker`

`ChapterTransformer` 与 `AnchoredContentTransformer` 是仅异步的公共扩展协议。前者的导入路径为
`from pdf_craft.transformer import ChapterTransformer`，`AnchoredContentTransformer` 也可直接从包顶层导入。本文不把以下内容当作
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

`PDFHandler` 是替换 PDF 读取和页面渲染实现的同步协议。默认的
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

异步应用也可实现 `AsyncPDFHandler`。它的 `open()` 以及返回的
`AsyncPDFDocument` 的 `pages_count()`、`metadata()`、`page_size()`、
`render_page()`、`close()` 都是异步方法；提取流水线会在调用方事件循环中等待这些方法，
同时让同步 OCR 引擎继续留在专用工作线程。

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
    includes_furniture=True,
    extract_book_metadata=False,
    metadata_llm=None,
    generate_plot=False,
    toc_assumed=False,
    toc_llm=None,
    page_repair=None,
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

`page_repair` 是可选的页级脚注矫正配置，启用时必须同时设置 `includes_footnotes=True`。
凭据与模型配置和 `LLM` 一样由调用方显式传入：

```python
from pdf_craft import ExtractionOptions, JEV, LLM, PageRepairOptions

options = ExtractionOptions(
    includes_footnotes=True,
    page_repair=PageRepairOptions(
        jev=JEV(key="...", model="jev-latest"),
        llm=LLM("...", "https://example.com/v1", "model", "o200k_base"),
    ),
)
```

传统算法仍先完整生成可逆的 PageAnalysis；JEV 只负责筛选低置信页，LLM 返回的完整目标页必须通过
schema、layout 不可变性、citation/ref 一一对应和 gap 等确定性约束，之后才继续组装 FlowItem。

`extract_book_metadata` 默认关闭。开启后必须通过独立的 `metadata_llm` 参数显式提供 LLM，
不会隐式复用 `toc_llm`。它会先向 LLM 提供前三个原始 OCR 页；模型可继续请求前部页面，但总数最多为
12 页。每个元信息字段必须给出 OCR 证据并通过 JSON repair Loop 的结构与业务校验。OCR 值优先，
PDF 文件自身的 metadata 只补充 OCR 未得到的字段，绝不覆盖 OCR 结果；得到的 PCEX metadata 会同时供
EPUB 渲染与 PDF 回填使用，后者会将其写入输出 PDF 的 document metadata。元信息请求最终失败也不会
中断整本 PDF 的转换。

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
    ignore_errors=True,  # 某一页写回失败时保留该页视觉底图，并继续后续页
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

`translate_pdf` 与 `patch_pdf_with_extraction` 默认在写回错误时立即失败。传入
`ignore_errors=True` 后，能够归属到某一页的擦除、文字层、公式或 PDF 合成异常（包括未知代码
异常）会记录完整 traceback，并让该页退回为不可交互的视觉底图；其余页仍继续写回。若所有需要
写回的页面都退回，会抛出 `NoUsableFillPagesError`，不会产生伪成功文件。不能打开、枚举或编译
为视觉底图的源 PDF 没有可回退页，仍会直接失败。

`ignore_errors` 还可以传入 `Callable[[Exception], bool]`，在每个可归属页面的异常发生时决定是否
允许该页回退。局部函数和 lambda 均受支持：Qt 回填在隔离进程运行时，predicate 仍留在调用方进程，
无需可 pickle；异步调用会在调用方事件循环线程执行 predicate。默认 `False` 保持 fail-fast；只有业务可以接受“未翻译视觉底图页与已翻译页面并存”时，
才应启用此恢复策略。

## PDFCraftExtraction 与 `.pcex`

`PDFCraftExtraction` 是带原始 PDF 页码和 bbox 映射的结构化中间对象。公开持久化和交换格式
统一为 `.pcex`（ZIP），通过以下方式加载：

```python
extraction = craft.open_extraction("work/book.pcex")
```

门面会在返回句柄前完成校验；`PDFCraftExtraction` 自身不公开打开、校验、导出或元数据读取方法。

归档固定包含 `manifest.json`、`pages.xml`、`chapters/`、`assets/`，并可选包含 `toc.xml` 与
`cover.png`。manifest 保存格式版本、producer、创建时间及书名、作者、出版社、语言等文档
元数据；pages 保存 1-based 页码、OCR 像素坐标空间、实际 DPI 和各页像素宽高。OCR 响应、
plot 和 done 标记属于 analysis 诊断信息，不进入 `.pcex`。

加载时会检查版本、ZIP 路径安全、必需组件、XML、页面引用、bbox 和资源引用；非法、损坏或
不支持版本的包会被拒绝。所有后端只读取 extraction 内字段，不会回退读取 analysis/OCR 缓存。

## 翻译与变换接口

### ChapterTransformer

章节变换器实现一个可等待的 `transform(chapter) -> chapter` 方法。它可以修改章节文本、段落或布局，
并被 `translate_extraction` 和 `translate_pdf` 使用。实现该低层协议时，需从
它的实际定义处导入 `Chapter`：

```python
from pdf_craft.extractor.chapter.chapter import Chapter
from pdf_craft.transformer import ChapterTransformer

class KeepChapterStructure:
    async def transform(self, chapter: Chapter) -> Chapter:
        # 修改 chapter 后返回同一个 Chapter；必须保留来源坐标和页面信息。
        return chapter

transformer: ChapterTransformer = KeepChapterStructure()
```

章节布局类型不是顶层 facade 的日常 API。自行编辑它们时必须保留原有页面来源信息，否则 PDF
写回无法定位原文；纯文本翻译应优先使用下一节的 `XMLTranslator`，避免依赖章节内部结构。

### ExtractionTransformer

extraction 变换器实现：

```python
async def transform(extraction: PDFCraftExtraction, output_path: Path) -> PDFCraftExtraction:
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
    with_furniture=True,
)
```

`with_furniture` 属于翻译，而非提取。默认值为 `False`；设为 `True` 时需要使用结构化的
`ChapterXMLTransformer`，
`translate_extraction()` 会在同一次 PCEX 翻译中先处理 NarrativeFlow，再处理包中已有的页眉、
页脚和页码等 furniture。关联 `toc_id` 的 section 会复用已译正文标题，模板 position 仅翻译一次，
未关联 section 以页为范围翻译，并在 `translation.xml` 记录 PDF 回填是否可覆盖。它不会重新 OCR；
没有 `furnitures.xml` 的包会安全退化为仅翻译 NarrativeFlow。

`translate_extraction` 不会把图片/表格的 title、content、caption 混入正文 LLM 上下文；只有段内
asset 以无文本、不可变 anchor 维持前后文本的位置，`StandaloneAsset` 不伪造 anchor。独立 asset 翻译的每个非保留结果都以稳定 identity 绑定来源 slot。若要翻译这些已提取的 asset 文本，请使用
`translate_anchored_contents(extraction, output_path, transformer)`。其 transformer 处理带局部上下文
的小批 asset，返回 `None` 即保留单个 asset；该阶段也不会自动组合到其他工作流。

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

EPUB-only 程序直接构造不带 PDF 配置的 `PDFCraft()` 或 `AsyncPDFCraft()` 即可；该能力不再
作为顶层独立函数导出。

## 低层 PDF 写回 API

通常应使用 `PDFCraft.patch_pdf_with_extraction()` 或 `PDFCraft.translate_pdf()`。顶层也公开了较低层的
写回组件，供已经能自行生成替换坐标与文字的集成方使用：

- `PDFReplacement` 描述一段待替换文本：`page_index`、像素坐标 `bbox`、`body`、OCR 画布尺寸
  `page_pixel_size`，以及可选的 `dpi`、`reading_order`。PDF 翻译会以一个 `TextFlowItem` 为单位
  调用翻译器一次，并将该段落全部、有序的来源框保存在 `regions`（`PDFReplacementRegion`）中，而不是把
  同一译文复制到每个框。
- `PDFPatcher(options=PatchTextOptions(...), erase_options=EraseOptions(...), pdf_handler=...)` 通过 `.patch(source_path,
  target_path, replacements)` 写出 PDF。它接受任意通过字段校验的 `PDFReplacement`，不要求这些
  替换项来自 `PDFCraftExtraction` 或 OCR；`page_pixel_size` 仅用于把像素 `bbox` 换算为 PDF 坐标，
  patcher 不会验证它是否等于源页的实际渲染尺寸。调用方必须自行保证页码、坐标与尺寸对应源 PDF。
  `PatchTextOptions` 控制默认字体、字号、内边距、对齐和
  `render_inline_formulas`。省略或传入空 `font_name`
  时，会解析一个已安装的本机字体，并在本次写回的所有未指定样式中复用；
  `PDFPatcher.font_resolutions` 可以查看自动选择或显式字体走 Qt fallback 的诊断，且不会与 bbox
  排版错误混淆。`EraseOptions.padding` 在
  OCR 像素坐标中扩大来源框，并用原始页面对应区域按频次加权的 RGB 中位色完整覆盖扩展矩形。`pdf_handler`
  仅为这个颜色估计渲染原始页，输出不会使用该 raster 作为页面底图。可用
  `PatchTextStyle` 以 `"body"`、`"heading"` 或 `"heading:2"` 为键覆盖不同文字等级。
  第一阶段会为同一个段落统一搜索字号，随后按顺序流入所有 `regions`；放不下的整行会进入下一个框，
  绝不局部跨框。第二阶段会在不改变冻结行数和文字分配的前提下，按文字等级的加权平均字号对每个 bbox
  局部归一化，因此最终字号可略有不同。Qt 负责断行、字形位置与字体 fallback；用户填写的缺失字体
  不会阻断运行。若合法来源几何在最小字号仍无法容纳完整正文，patcher 会从第一个来源 bbox 以该字号强制写入完整文字，
  允许越过普通 bbox 与障碍物边界，避免整份 PDF 中断。标题有相对已排版正文的最小字号；若该下限无法装入 bbox，先从 bbox 左侧
  中点向右按自然宽度绘制，仍不满足时再走同一强制写入兜底。

  `render_inline_formulas=True` 是默认值。章节 XML 会保留行内公式，使它们参与段落翻译上下文而不被
  替换；当 Matplotlib 与本机 TeX 可用时，patcher 将其写为 PDF 矢量内容，否则（或单个公式失败时）
  自动降级为可读 plain text。将该选项设为 `False` 可显式选择 plain-text 行为。Qt 负责普通文本的
  原生 shaping、断行、字体 fallback 和字形定位；只有公式及其紧随可见空白是不可拆分原子。矢量公式
  片段携带 plain-text 的 PDF `/ActualText` 语义替代，但 pdf-craft 不生成完整 tagged PDF，不能保证
  不同阅读器中该公式相对正文的复制顺序。
- `PDFTranslationPipeline` 只负责将已经翻译的 extraction `.patch()` 回 PDF。这是 facade 的
  底层组成部分；翻译应先产生新的 PCEX，再由 `PDFCraft.translate_pdf()` 或
  `PDFCraft.patch_pdf_with_extraction()` 编排写回。它只从 extraction 中带来源坐标的 `body` 和
  `heading` 布局收集替换项。

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

`PDFError`、`OCRError`、`NoUsableFillPagesError` 可用于日志和错误判断。`ignore_pdf_errors`、
`ignore_ocr_errors` 以及 PDF 写回的 `ignore_errors` 可传 `True`、`False` 或 callable。
`FillFailedEvent` 用于 EPUB XML 结构修复失败回调，包含
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
