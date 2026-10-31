# pdf-craft API 参考

2.6.5 新增任务规模查询及操作级资源用量报告，见[规模与用量](USAGE.md)。


## 可选输入断言

`assert_pdf_readable`、`assert_pdf_renderable`、`assert_pdf_editable` 和
`assert_epub_readable` 从 `pdf_craft` 顶层导出，是成功返回 `None`、失败抛原异常的
阻塞函数。转换与 `pdf_pages_count` 不会自动调用。详见[能力边界和用法](ERROR_HANDLING.md#可选输入断言)。

`pdf_pages_count(source, pdf_handler=DefaultPDFHandler(poppler_path=...))`
只通过指定 handler 读取页数。嵌套默认读取器共用显式 Poppler 目录，自定义 handler
无需新增配置属性。

本文面向需要把 pdf-craft 集成到自己程序中的用户。入门流程请先阅读仓库根目录的
README；本文只说明稳定的公共导入和它们如何组合。示例默认使用：

```python
from pdf_craft import AsyncPDFCraft, PDFCraft, PDFOptions
```

两个门面都接受 `retry_policy: RetryPolicy | None = None` 和
`error_policy: ErrorPolicy | None | bool | ErrorCallback = None`。提取、翻译、渲染和回填方法
可以按次覆盖；不传错误策略时继承实例配置，显式 `None` 或 `False` 恢复严格失败。
它们是执行参数，不属于 `ExtractionOptions` 的字段。
详见[策略配置与 2.6.0 迁移指南](ERROR_HANDLING.md)。

2.6.3 起，`pdf_craft` 和 `pdf_craft.error` 均已移除 `PDFPreflightError`。
请删除旧的导入和捕获分支；可选输入断言直接传播原后端或解析器异常。

## 操作级请求 Header

会调用模型服务的操作支持 keyword-only 参数
`request_headers: Mapping[str, str] | None = None`。异步与同步 Craft 门面、
提取／渲染／翻译／回填组件、`XMLTranslator.translate_element(s)`、LLM runtime/context
的 `request`、vendor OCR 的 `request`／`request_many`、JEV 的
`evaluate`／`evaluate_many` 均可使用。

```python
await craft.extract_pdf(
    "input.pdf", "book.pcex", options,
    request_headers={"X-Correlation-ID": correlation_id},
)
await craft.translate_extraction(
    "book.pcex", "translated.pcex", translator,
    request_headers={"X-Correlation-ID": correlation_id},
)
```

操作开始时复制并校验映射。省略或传 `None` 继承外层操作；顶层没有外层时表示不附加
Header。显式映射**替换**继承值，不做合并；`{}` 清空本次操作及其后续嵌套调用的附加
Header。成功、异常或取消后均恢复外层作用域。

嵌套模型调用、翻译／填充恢复、重试和后台任务继承同一作用域。LLM、vendor OCR 和 JEV
在发送请求时取值，不修改共享配置、translator、runtime 或 HTTP client；并发操作可以
安全使用不同 Header。这是执行参数，不是 `LLM`、OCR 配置或 `ExtractionOptions` 的字段。

如果此前使用支持实例级 `headers` 的实验版本，迁移时从模型配置构造函数中移除该参数，
改为在每次操作上通过 `request_headers` 传入映射。共享模型配置无需修改，各操作分别
提供自己的 Header。

透传范围仅为配置的模型服务：LLM completion、DeepSeek OCR/OCR2、Unlimited OCR 的
获取 token／提交／轮询，以及 JEV 请求。OCR 图片 URL resolver／上传和解析结果资源下载
不自动携带这些 Header；自定义 resolver／client 自行发起的网络请求仍由调用者负责。

名称和值必须是符合 HTTP Header 语法的字符串（值为 ASCII，不含 CR/LF），名称不区分
大小写且不得重复。`Authorization`、`Content-Type`、`Host`、`Content-Length` 和
`Transfer-Encoding` 由 HTTP client 管理，禁止通过该参数覆盖。非法参数在业务操作前
抛异常。Header 的业务含义由调用者决定，库不生成业务标识，也不绑定任何特定网关。


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
该规则覆盖所有用于同步第三方库的线程池边界；用户提供的可执行扩展统一采用异步协议。
异步 PCEX 导出会先写同目录临时归档，再通过一次原子替换发布。若取消先到达，调用会等待
writer 收尾、删除临时归档并保持目标不存在；若发布边界先完成，则该取消视为晚于成功完成。
`pdftotext`、Ghostscript、LaTeX 等外部工具在受跟踪的 POSIX 进程组或 Windows Job Object
中运行；取消或内部超时会终止命令及其派生进程，正常完成或失败也会回收晚于父进程退出的
后代。取消 Qt worker 时，会先完成这些清理，再结束 worker 本身。

图片/表格独立翻译采用仅异步的 `AnchoredContentTransformer`，提供
`async def transform_assets(assets)`。
`PDFOptions.pdf_handler` 仅接受 `AsyncPDFHandler`：其 `open()` 返回
`AsyncPDFDocument`，并提供可等待的 `pages_count()`、`metadata()`、
`page_size()`、`render_page()` 和 `close()`。SDK 在进入同步 OCR 或 Qt 边界前，
先在调用方事件循环中等待并临时物化源页面，只有页面路径和元数据会进入这些执行域。

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

`PDFCraft` 是异步实现之上的同步门面，适合普通脚本；但不能从已经运行事件循环的线程中
调用，否则会明确抛出 `RuntimeError`，而不会嵌套启动事件循环。此时应改用
`AsyncPDFCraft`。

用户只需在构造时选择门面，之后两种模式使用相同的方法名。
`PDFCraftExtraction` 是不暴露内部操作的句柄：通过 `PDFCraft.open_extraction()` /
`export_extraction()` 或 `AsyncPDFCraft` 上的可等待同名方法打开、导出，再把句柄交回门面。
高级组件仅提供无后缀的异步方法，包括 `PDFExtractor.extract`、
`MarkdownRenderer.render`、`EpubRenderer.render` 和各 transformer API。已有 EPUB
翻译同样只通过门面调用。模型预下载使用异步的 `await predownload_models(...)`；同步调用使用 `PDFCraft().predownload_models(...)`。

## 公共入口

`pdf_craft` 包顶层导出常用类型。最主要的入口是 `PDFCraft`，它把 PDF 提取、渲染、
翻译和 PDF 写回组合成一组方法。下面这些对象可直接从 `pdf_craft` 导入：

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

`AnchoredContentTransformer` 是仅异步的公共扩展协议，也可直接从包顶层导入。本文不把以下内容当作
公共扩展点：内部 engine、`pdf_craft_tool` CLI、`pdf_craft` 的私有模块路径，以及
`doc-page-extractor` 的内部 extractor/factory。

提取和翻译使用操作/单元两层的 STARTED、PROGRESS、COMPLETED，开始时报告必填 unit_counts，
运行时按需分配整数 unit_id。提取使用 ExtractionOptions.on_extraction_event，翻译使用 on_translation_event。
进度统一使用原文 **words** 累计量，PCEX/PDF/EPUB 会预统计完整总量。计数器的流式规则、
20 字符长词上限、fallback 语义和不兼容字段改名见[翻译进度](TRANSLATION_PROGRESS.md)。
内容回调仍独立交付成果。

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
    pdf_handler=None,           # AsyncPDFHandler；省略时使用默认处理器
    models_cache_path=None,     # local OCR 模型缓存目录
    local_only=False,           # 禁止 local OCR 下载缺失模型
    ocr_executor=None,          # vendor OCR 必须显式提供的共享 AsyncExecutor
    ocr_image_url_resolver=None,# 可选的 OCRImageURLResolver
)
```

`ocr` 与 `models_cache_path`、`local_only` 是两套互斥的配置方式：如果显式传入 `ocr`，
不能再同时传入 `models_cache_path` 或将 `local_only` 设为 `True`，否则会抛出 `ValueError`。
远程 OCR 直接把对应 vendor 配置传给 `ocr`；
本地 OCR 可以把模型缓存和离线选项写进 local 配置，也可以使用 `models_cache_path` 和
`local_only` 的默认 local OCR 路径。

`ocr_image_url_resolver` 是异步函数，接收每张 Vendor OCR 渲染图片的 `Path`，返回 HTTP(S) URL 或
Base64 图片 Data URL。未设置时，pdf-craft 默认读取图片并生成
Base64 Data URL。本地 OCR 不使用该字段。

### 自定义 PDFHandler

用户传入 `PDFOptions(pdf_handler=handler)` 的 handler 必须实现异步协议。`open()` 返回的
`AsyncPDFDocument` 提供可等待的 `pages_count()`、`metadata()`、`page_size()`、
`render_page()` 和 `close()`。SDK 会在进入同步 OCR 或 Qt 第三方边界前，在调用方事件循环中
完成页面物化。用户提供的可执行扩展不支持同步协议。

一个异步 handler 需要实现 `open(pdf_path: Path) -> AsyncPDFDocument`。它返回的 document 必须提供：

- `pages_count()`，返回页面总数；
- `metadata()`，返回 `PDFDocumentMetadata`；
- `page_size(page_index)`，返回以英寸计的宽、高；
- `render_page(page_index, dpi)`，返回 `PIL.Image.Image`；
- `close()`，释放打开的文档资源。

这些方法的 `page_index` 均从 1 开始，且必须是异步方法。框架会在使用完成后关闭 document。

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
    footnotes=False,
    refinement=None,
    includes_furniture=True,
    extract_book_metadata=False,
    metadata_llm=None,
    generate_plot=False,
    toc_assumed=False,
    toc_llm=None,
    aborted=lambda: False,
    on_extraction_event=lambda event: None,
    on_body=None,
    on_footnote=None,
    on_metadata=None,
    context=None,
)
```

`page_indexes` 使用从 1 开始的 PDF 页码。`toc_assumed` 决定是否把目录页作为输入线索，
默认值为 `False`；如果需要目录页检测，应在 EPUB 或 Markdown 提取时显式传入 `True`。
`toc_llm` 是可选的目录层级分析
LLM，不是 OCR 配置，也不承担 `XMLTranslator` 的正文翻译职责。

`footnotes: bool = False` 控制是否提取脚注；`refinement: Refinement | None = None`
独立控制页面精校。`Refinement` 必须同时提供 JEV、LLM 及显式执行器，不支持只配置其中一个。

```python
from pdf_craft import (
    ExtractionOptions,
    Refinement,
    JEV,
    LLM,
    ConcurrentExecutor,
    FixedCapacity,
)

executor = ConcurrentExecutor(FixedCapacity(4))

options = ExtractionOptions(
    footnotes=True,
    refinement=Refinement(
        jev=JEV(key="...", model="jev-latest"),
        llm=LLM("...", "https://example.com/v1", "model", "o200k_base"),
        executor=executor,
    ),
)
```

初级页及相邻初级输入就绪后逐页 JEV，按当前页评分独立决定是否 LLM 精修；不等全书 OCR 或全批 JEV。
LLM prompt 只包含当前页评分，不披露邻页评分；邻页始终使用不可变初级内容。候选通过 schema、
来源身份及页内关系校验后按序增量组段，跨页约束在组段时检查；闭合内容立即交付。
最终 TOC 整理不阻塞正文/脚注回调，详见[设计取舍](../design/pdf-extraction-streaming.md)。
JEV 与 LLM 使用不同供应商配额时，可分别传 `jev_executor` 与 `llm_executor`；传共同的
`executor` 表示两者共享容量。AI 矫正必须显式传入执行器，不再提供各服务独立的数字并发默认值。


将上例改成 `footnotes=False` 即可只精校正文，不提取脚注。两种精校模式复用同一条
有界流式管道。关闭脚注时不增加脚注 OCR 轮次，也不创建 citation/reference 或脚注内容回调。
启用精校时，第一轮 OCR 与已有缓存中的 footnote 来源仍作为候选保留；初级结果默认不输出
这些候选，但 JEV 可指出误分类，LLM 可将误标的正文恢复为 paragraph。反向误入正文、且
有明确语义证据属于脚注的完整块，可标为 ignored。不能仅凭页底、小字号或数字开头删正文；
混合正文和脚注的块保留为正文，不整块丢弃。原始缓存、文本及来源身份不改写。
没有 refinement 时仍按 OCR 原分类排除脚注；JEV 放行或允许精校失败降级时也不猜测恢复候选。
旧缓存若已丢弃了误标文字，精校无法凭空救回，需用新分析目录重新 OCR。
旧的 `FootnoteOptions` / `FootnoteRefinement` 嵌套配置需迁移为布尔 `footnotes`
和独立的 `refinement=Refinement(...)`，不提供兼容别名。

| footnotes | refinement | 处理方式 |
| --- | --- | --- |
| False | None | 普通 OCR + 算法分析 |
| True | None | 脚注 OCR + 算法分析 |
| False | Refinement(...) | 普通 OCR + JEV + 按需 LLM 页面精校 |
| True | Refinement(...) | 脚注 OCR + JEV + 按需 LLM 页面精校 |

页面精校的 `risk_threshold` 默认值为 **0.45**：JEV 风险评分达到该值时调用 LLM。
这是控制 LLM 调用成本的部分精修，接受一部分结构错误未被修复；显式传入的阈值仍按调用者配置执行。
评分不是经过校准的错误概率，此阈值不限制单本精修配额，也不保证网站整体的精修比例。
实验结果与限制见[部分精修研究记录](../verification/jev-partial-refinement.md)。
精校覆盖脚注的完整范围，包括无编号续文。未确认起点的初级别名不作为不可更改的跨页事实。

`extract_book_metadata` 默认关闭。开启后必须通过独立的 `metadata_llm` 参数显式提供 LLM，
不会隐式复用 `toc_llm`。它会先向 LLM 提供前三个原始 OCR 页；模型可继续请求前部页面，但总数最多为
12 页。每个元信息字段必须给出 OCR 证据并通过 JSON repair Loop 的结构与业务校验。OCR 值优先，
PDF 文件自身的 metadata 只补充 OCR 未得到的字段，绝不覆盖 OCR 结果；得到的 PCEX metadata 会同时供
EPUB 渲染与 PDF 回填使用，后者会将其写入输出 PDF 的 document metadata。元信息请求最终失败默认抛异常；只有 ErrorPolicy 允许分析降级时才使用 PDF metadata fallback 并继续。

`aborted` 返回 `True` 时请求中止当前任务。`on_extraction_event` 接收统一工作单元进度，见[协议与 stage 编号](TRANSLATION_PROGRESS.md)。`ErrorPolicy.ignore_pdf_render_errors` 和 `ErrorPolicy.ignore_ocr_errors` 接受布尔值，也接受
根据 FailureContext 返回布尔值的 callable；它们只影响页面级错误是否继续处理。

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
`analysing_path/extraction/`，不会为衔接前后端执行无意义的压缩与解压。指定 `extraction_path`
时，原文 PCEX 在提取成功后独立提交；后续翻译或渲染失败不会撤回它：

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
用于把渲染出的图片资源写到指定目录；EPUB 的 `label_language`、`table_render`、`latex_render` 和
`inline_latex` 控制 EPUB 输出格式。
两个方法都可以通过 `translator` 和 `on_translation_event` 在转换时完成一次翻译。

### 从已有 extraction 渲染

```python
from pdf_craft import RenderMode

craft.render_markdown(extraction, "book.md", assets_path="book-assets")
craft.render_epub(
    extraction, "book.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)

# translation_id 省略时，稳定选择第一份翻译结果。
craft.render_markdown(
    extraction, "translated.md", mode=RenderMode.REPLACE,
)
craft.render_epub(
    extraction, "bilingual.epub", mode=RenderMode.BILINGUAL,
    translation_id="zh-main",
)
```

渲染不会重新 OCR，也不会读取 PDF。Markdown 与 EPUB 都要求 extraction 校验通过；
`toc.xml` 可选，EPUB 可根据章节标题生成导航。Markdown 可选复制图片资源；EPUB 从
`manifest.json` 读取默认元数据，显式提供的非空 `book_meta` 字段优先。
`label_language="en"` 只选择目录、封面、前言和未命名标题等固定文案；精确 `zh` / `en`
选择对应文案，其他字符串统一回退英文。不根据原文或 translator 的目标语言推断，
不限制或修改正文、翻译目标、PCEX 语言元数据或回调内容。
2.6.2 起，`render_epub()`、`convert_pdf_to_epub()` 和 `EpubRenderer.render()` 的旧 `lan`
关键字改为 `label_language`，旧关键字不再接受；默认文案也改为英文。

已有调用只需在以下三个入口替换关键字（`EpubRenderer` 调用放在异步函数中）：

```python
# 2.6.2 之前
craft.render_epub(extraction, output, lan="zh")
craft.convert_pdf_to_epub(source, output, lan="zh")
await EpubRenderer().render(extraction, output, lan="zh")

# 从 2.6.2 开始
craft.render_epub(extraction, output, label_language="zh")
craft.convert_pdf_to_epub(source, output, label_language="zh")
await EpubRenderer().render(extraction, output, label_language="zh")
```

`PDFCraft` 和 `AsyncPDFCraft` 都使用新关键字，异步门面的调用需加 `await`。
原来没有传 `lan` 的调用不需要迁移关键字，但固定文案现在默认英文；需要保留中文文案时，
显式传入 `label_language="zh"`。翻译配置中的 `target_language="ko"` 等参数不变，
不能用 `label_language` 替换 `target_language`。

固定依赖的 EPUB 生成器仍将部分 OPF/XHTML 语言标记写死为 `zh`；这是独立的既有问题，
本次文案选择没有修复它，也不能把 `label_language` 当作内容语言标记设置。

`RenderMode.SOURCE` 只渲染原文，不选择译文。`RenderMode.REPLACE` 用所选译文替换
已翻译单元，未翻译单元回退原文。
`RenderMode.BILINGUAL` 按正文段先原文、后译文输出；`RenderMode.BILINGUAL_TEXT` 在同一文本流的各来源 fragment 内追加对应译文。以上译文渲染模式可传文件内唯一的
`translation_id`；省略时稳定选择第一份翻译结果。没有任何译文、显式 ID
不存在或 ID 格式非法时会明确失败，不会静默改选其他译文。也可先调用
`resolve_translation(extraction, translation_id=None)` 得到实际选择的 `TranslationInfo`。
译文 metadata 覆盖原文 metadata，缺少的字段保留原值；双语书名、描述、章节标题、目录标题
以及已翻译的图片/表格字段同时包含两种文本。

### PDF 转换时翻译

PDF 转换入口可以传入一个 `XMLTranslator` 和提交模式，在渲染前完成一次翻译：

```python
craft.convert_pdf_to_markdown(
    "input.pdf", "translated.md", translator=translator,
    submit=SubmitKind.REPLACE,
)
```

标准 PDF/PCEX 翻译入口直接接收 `XMLTranslator`，章节内部布局不是公共扩展面。

### 翻译并写回 PDF

```python
extraction = craft.extract_pdf("input.pdf", "work/book.pcex")
craft.translate_pdf(
    "input.pdf", extraction, "translated.pdf", translator,
    error_policy=True,  # 允许可恢复错误降级；写回失败时保留原 PDF 页
)
```

`translate_pdf` 将 PCEX 内容交给公共翻译流，再由 PDF 消费端增量排版和写回，无需先生成完整
临时译本。自定义整章 translator 保留兼容路径。写回只会替换 extraction
中记录了来源坐标的原始 PDF 文本，不是通用 PDF 排版器；输入 PDF 必须来自同一源文件，并且
`pages.xml` 要包含完整页面几何。PDF 翻译固定使用纯译文替换，不接受提交模式。

如果已经有翻译后的 `.pcex`，也可以单独写回：

```python
craft.patch_pdf_with_extraction(
    "input.pdf", "work/translated.pcex", "translated.pdf",
    translation_id="zh-main",
)
```

`translate_pdf` 与 `patch_pdf_with_extraction` 默认在写回错误时立即失败。传入
`error_policy=True` 后，能够归属到某一页的擦除、文字层、公式或 PDF 合成异常（包括未知代码
异常）会记录完整 traceback，并让该页保留原始 PDF 内容；其余页仍继续写回。若所有需要
写回的页面都退回，会抛出 `NoUsableFillPagesError`，不会产生伪成功文件。不能打开、枚举或编译
为视觉底图的源 PDF 没有可回退页，仍会直接失败。

`error_policy` 还可以传入同步或异步判断函数，接收最终失败的 `FailureContext` 后返回 bool。
顶层 callback 会决定所有可降级类别；若只决定 PDF 写回失败，使用
`ErrorPolicy(ignore_pdf_fill_errors=callback)`。局部函数和 lambda 均受支持：Qt 回填在隔离进程
运行时，predicate 仍留在调用方进程，无需可 pickle；异步判断会在调用方事件循环线程被等待。
只有业务可以接受“原始 PDF 页与已翻译页面并存”的结果时才应启用恢复；默认严格失败。

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

### ExtractionTransformer

extraction 变换器实现：

```python
async def transform(extraction: PDFCraftExtraction, output_path: Path) -> PDFCraftExtraction:
    ...
```

它负责把一个完整 extraction 写入新的 `.pcex`，并返回新的 `PDFCraftExtraction`。

### 使用 XMLTranslator 翻译 PDF 章节

`XMLTranslator` 是包顶层导出的结构化文本翻译器。它需要分别提供翻译文本和修复 XML
结构的 LLM；同一个 `LLM` 可以同时承担两项工作。标准 PDF 转换或 extraction 翻译入口直接
接收该对象：

```python
from pdf_craft import (
    ConcurrentExecutor,
    FixedCapacity,
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
executor = ConcurrentExecutor(FixedCapacity(4))
from pdf_craft import RetryPolicy

xml_translator = XMLTranslator(
    translation_llm=llm,
    fill_llm=llm,
    target_language="zh",
    user_prompt=None,
    retry_policy=RetryPolicy(fill=3),
    max_fill_displaying_errors=10,
    max_group_score=2600,
    executor=executor,
)
craft.convert_pdf_to_markdown(
    "input.pdf", "translated.md", translator=xml_translator,
    submit=SubmitKind.REPLACE,
)
```

`translation_llm` 负责生成译文，`fill_llm` 负责在必要时修复 XML 结构。两个 LLM 可以使用
不同的模型、提示参数、缓存或重试策略。必须提供共用的 `executor`，或分别提供
`translation_executor` 和 `fill_executor`；
`translate_element(s)` 使用 `window` 控制 XML 业务预取，不再接受 `concurrency` 别名。若目标是双语 Markdown 或 EPUB，可把提交模式设为
`APPEND_TEXT` 或 `APPEND_BLOCK`。PCEX 翻译和 PDF 写回没有提交模式。

已有可复用 extraction 时，调用 `translate_extraction` 并显式指定新的 `.pcex`：

```python
translated_extraction = craft.translate_extraction(
    extraction,
    "work/translated.pcex",
    xml_translator,
    with_anchored=True,
    with_furniture=True,
    translation_id="zh-main",
    target_language="zh",
)
translations = craft.list_translations(translated_extraction)
```

`translate_extraction()` 保留原文并追加一个翻译版本。调用方可指定文件内唯一的短 opaque
`translation_id`，也可自动生成；同一语言可以有多个版本。重复 ID 会在翻译开始前拒绝。
`list_translations()` 返回每个版本的 ID、目标语言和创建时间。目标语言可以由调用方显式传入，
也可以由 translator 声明；两者都未提供时，调用会在翻译开始前失败。保存的翻译版本只包含纯译文；
`render_markdown` 和 `render_epub` 通过 `RenderMode` 选择原文、译文替换或双语输出。

PDF 一键转换复用相同的 replacement-only 译文层流程：`SubmitKind.REPLACE` 选择替换渲染，
`APPEND_TEXT` 选择 `BILINGUAL_TEXT`，`APPEND_BLOCK` 选择 `BILINGUAL`。即使已有其他译文，
也显式选择本次新生成的层。原文和译文在各自层内保留相同的 fragment 来源身份与几何；
组合后的双语展示视图只用于渲染，不作为 PCEX 章节保存。分块模式将一个正文段的原文片段
组合后再输出译文段；标题与目录仍合为一个标题。图片、表格只出现一次，标签和单元格文字
在原位组合。脚注定义跟随文本／分块样式，多次引用仍链接同一份定义。
`extraction_path` 仍导出原始提取结果。一键 EPUB 转换与独立渲染使用同一套
`label_language` 固定文案选择和英文回退，不再从原文／译文语言推断文案。
直接翻译已有 EPUB 的提交模式不受影响。

标准入口直接接收 `XMLTranslator`。narrative 与 metadata 固定执行；`with_anchored` 默认是
`True`，只控制独立图片/表格文字阶段；`with_furniture` 默认是 `False`，只控制页面 furniture
阶段。三个阶段由 pdf-craft 内部使用固定 adapter 编排，开关不会改变翻译算法。

`translate_extraction` 不会把图片/表格的 title、content、caption 混入正文 LLM 上下文；只有段内
asset 以无文本、不可变 anchor 维持前后文本的位置，`StandaloneAsset` 不伪造 anchor。随后自动组合的独立 asset 阶段以稳定 identity 绑定每个结果。底层
`translate_anchored_contents(extraction, output_path, transformer)` 仍可用于自定义流水线；其 transformer 处理带局部上下文
的小批 asset，返回 `None` 即保留单个 asset。

### 翻译 EPUB

```python
from pdf_craft import (
    ConcurrentExecutor, FixedCapacity, LLM, PDFCraft, SubmitKind,
)

llm = LLM(
    key="your-api-key",
    url="https://api.openai.com/v1",
    model="your-model",
    token_encoding="o200k_base",
)
executor = ConcurrentExecutor(FixedCapacity(4))
PDFCraft().translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.APPEND_BLOCK,
    llm=llm, executor=executor,
)
```

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

EPUB-only 程序直接构造不带 PDF 配置的 `PDFCraft()` 或 `AsyncPDFCraft()` 即可；该能力不再
作为顶层独立函数导出。

## 低层 PDF 写回 API

通常应使用 `PDFCraft.patch_pdf_with_extraction()` 或 `PDFCraft.translate_pdf()`。顶层也公开了较低层的
写回组件，供已经能自行生成替换坐标与文字的集成方使用：

- `PDFReplacement` 描述一段待替换文本：`page_index`、像素坐标 `bbox`、`body`、OCR 画布尺寸
  `page_pixel_size`，以及可选的 `dpi`、`reading_order`。PDF 翻译会以一个 `TextFlowItem` 为单位
  调用翻译器一次，并将该段落全部、有序的来源框保存在 `regions`（`PDFReplacementRegion`）中，而不是把
  同一译文复制到每个框。
- `PDFPatcher(options=PatchTextOptions(...), erase_options=EraseOptions(...), pdf_handler=...)` 通过 `await patcher.patch(source_path,
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
- `PDFTranslationPipeline` 只负责将已经翻译的 extraction `await pipeline.patch(...)` 回 PDF。这是 facade 的
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

`await predownload_models(models_cache_path=None, revision=None, ocr=None)` 可以提前
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
    failure_log_path=None,       # keyword-only; exhausted repair-loop evidence
    total_timeout=360.0,
    cleanup_timeout=5.0,
)
```

`timeout` 保留 HTTP connect/read/write/pool 超时语义，不能限制整个流式响应。
`total_timeout` 限制一次 LLM 操作的容量等待、流式请求、传输重试及退避等待；
TOC 的 JSON/schema/业务修复尝试共用同一预算。默认 360 秒，即使 `timeout=None`
也有保护；只有显式 `total_timeout=None` 才关闭总预算。这两个新增参数仅接受关键字，
也适用于翻译请求。PR #506 曾把 `timeout` 用作总预算；依赖该未发布行为的调用方
应改为显式设置 `total_timeout`。

成功、超时或取消后，stream 关闭与 client 退出各使用 `cleanup_timeout` 的一半
（默认合计 5 秒）。这是操作 deadline 之后额外的清理预算，不是整个 PDF 转换的
硬总时限。清理超时记录 `cleanup-timeout`；取消会继续传播，清理不会脱离为后台任务。
上述约束要求事件循环正常运行且传输实现响应取消；阻塞事件循环或吞掉取消的任意扩展
需要宿主使用进程 watchdog 强制终止，库不能强杀协程。服务 deadline 和 failed 状态
转换仍由宿主负责。

没有 `log_dir_path` 时，请求、缓存、重试、超时、失败、取消和成功事件传播到
`pdf_craft.llm` 父 logger，由调用方为它或 root logger 配置 handler；配置目录后
事件写入请求日志文件。TOC LLM 失败默认抛异常，允许分析降级时才回退统计方法；调用方取消始终向上传播。
EPUB 输出类型本身不会开启 LLM；只有显式传入 `toc_llm` 才会请求。


`LLM` 只保存配置，实际请求由 pdf-craft 内部运行时发起。OCR endpoint 和文本翻译 LLM
是两套独立配置，不要把 OCR 配置当作翻译 LLM 使用。

## 共享异步容量

`ConcurrentExecutor(FixedCapacity(n))` 提供一条绑定到事件循环的远程调用容量通道。需要共享并发容量的 LLM、
JEV 或 vendor OCR 可共享执行器；只共享限流配额时使用同一个 `RateLimitScope`。`run()` 与 `map()` 共享容量；
`map()` 惰性读取 `Callable[[int], Awaitable[tuple[int, T]]]`，按完成顺序返回
`OperationResult`，调用方通过 `operation_id` 关联原始输入。自定义执行器的 `map()` 返回
带 `aclose()` 的 `AsyncResultIterator`，使调用方可在释放网络 client 或临时资源前取消并收敛
未完成任务。

`CapacityProvider` 与 `CapacityLease` 是自适应策略的公开协议。自定义 provider 可在
`acquire()` 中异步等待，在 `release()` 收到的 `ExecutionReport` 中观察结果，并在容量恢复时
唤醒自己的等待者；它还必须实现 `close(error)`，让 `NonContinuableError` 唤醒所有阻塞请求。
普通 `OperationError` 在 `map()` 中作为单项结果返回；
`NonContinuableError` 会关闭共享执行器并取消未完成工作。非限流 retry 循环位于执行器外，限流恢复由执行器承接；
每次真实请求都重新申请容量。

### 共享限流范围

`RateLimitScope(cooldown_seconds=1.0, max_wait_seconds=600.0)` 协调同一账号、API key 或模型
配额的暂时限流。所有参数可省略；不传 `rate_limit_scope` 时，每个 `ConcurrentExecutor`
自动拥有独立 Scope。共享 Scope 不会共享并发容量、致命终止状态或取消范围。

```python
from pdf_craft import ConcurrentExecutor, FixedCapacity, RateLimitScope

limits = RateLimitScope()
ocr_executor = ConcurrentExecutor(FixedCapacity(4), rate_limit_scope=limits)
translation_executor = ConcurrentExecutor(FixedCapacity(8), rate_limit_scope=limits)
```

正常时直接放行，容量仍由各 executor 决定。适配层把暂时限流归一化为 `RateLimitedError`；
Scope 不解释 HTTP 状态，也不把普通 429 一律视为余额耗尽。限流后整个 Scope 冷却，结束后
只允许一个试探请求。成功后从最多一个在途请求开始，至少间隔一秒错峰准入；每级连续成功
达到该级在途上限后，按 1、2、4、8…增加上限，达到正常容量后取消限速。已在途的旧请求
不会被限流取消，其成功也不会解除新一轮冷却。试探取消或普通失败释放资格，不算恢复成功。

- `cooldown_seconds` 是无服务端指示时的基础冷却，连续限流指数增长，本地最多 60 秒。
  数值及 HTTP-date `Retry-After` 由适配层转换。冷却至少为基础值（最多 60 秒），服务端要求
  更久时不能截短；随机错峰只向后延迟。重复限流只能延长已有冷却。
- `max_wait_seconds` 是**每个逻辑请求**累计等待限流准入的预算，包含试探/恢复排队，
  不包括普通容量排队和请求执行；共享实例不是共享耗尽预算。LLM 网络重试及 Unlimited
  同一页的各 HTTP 阶段保留这份预算。已有请求总超时仍优先生效。
- 等待冷却不占请求容量，实际调用前重新检查冷却和恢复间隔。容量延迟授予后也不能集中
  启动请求；仍需等待的请求先释放容量。重试重新排队，不持续插队。
  自定义 `CapacityProvider` 无需新增方法；已知 `concurrency` 用作恢复目标，否则使用观察到的
  正常并发高水位（至少 1），共享范围按各 executor 目标之和恢复。
- `run()` 内部恢复暂时限流；`map()` 同样恢复，超过预算返回带 `operation_id` 的单项失败。
  `RateLimitTimeoutError` 是公开 `OperationError` 子类，原因链保留最近的限流及供应商原始错误。
  它不关闭整个请求池。余额/配额耗尽等 `NonContinuableError` 仍按首个致命原因终止。

迁移：`LLM.retry_times`、OCR vendor 的 `retry_times` 和 JEV 的 `RetryPolicy.analysis` 不再消耗
在暂时限流上；即使设为 0，仍可由 Scope 恢复限流。旧 `retry_interval_seconds` 保留，用于
非限流网络错误、空响应相关既有行为及供应商特定恢复，不再控制限流冷却。JEV 非限流网络
重试仍使用 0.5 秒间隔。要调整限流等待，应配置 Scope，而非增大这些重试次数。
自定义 `AsyncExecutor` 实现仍自行负责限流恢复；需要内置行为时使用 `ConcurrentExecutor`
搭配自定义 `CapacityProvider`。没有增加第二套同步实现。


同一次 SDK 操作中，首次观察并记录的 `NonContinuableError` 是唯一终止原因；各阶段使用
不同执行器时也遵守此规则。SDK 会取消兄弟工作并等待清理，然后抛出原始异常，完整保留其
原因链。后续失败、派生取消和收尾错误不能替换它。“首次”指 SDK 观察并记录的顺序，
不保证是供应商现实时间中最早发生的错误。没有已记录的致命原因时，调用方主动取消或
任务自行取消仍传播 `CancelledError`。独立操作不会共享终止状态，除非共用了执行器。

## 计量、事件与错误

`OCRTokensMetering` 提供 `input_tokens` 和 `output_tokens`，由带 `with_metering` 的提取
方法以及一键转换方法返回。`ProgressEvent` 用于提取和翻译的统一工作单元进度，见[协议与迁移](TRANSLATION_PROGRESS.md)。

失败决策与重试配置见 [错误处理](ERROR_HANDLING.md)。

`ExtractionOptions.aborted` 返回 `True`，以及 `max_ocr_tokens` /
`max_ocr_output_tokens` 达到上限时，当前 PDF facade 会透出 OCR backend 的中断异常；它尚未把
这些异常适配为带 `OCRTokensMetering` 的 pdf-craft 统一异常。需要在中断前保留进度时，应通过 `on_extraction_event` 持续记录事件，并按所选 backend 的异常类型处理。
进度事件不再包含逐页 token；token 总量由 metered 接口返回。

## 组合建议

- 一次性 PDF → Markdown/EPUB：使用 `PDFCraft(pdf=PDFOptions(...)).convert_pdf_to_*`，默认
  自动管理临时 analysis；需要中间产物时传 `extraction_path`。
- 需要重复渲染、翻译或写回：先用 `extract_pdf` 保存 `.pcex`，再调用 `render_*`、
  `translate_extraction` 或 `patch_pdf_with_extraction`。
- PDF → 翻译 PDF：使用同一源 PDF 生成 extraction，再调用 `translate_pdf`；该入口固定使用
  纯译文替换。
- EPUB → EPUB：使用 `PDFCraft().translate_epub`，只配置文本 LLM，不需要 OCR。


## 增量内容与元信息回调

同步与异步门面显式声明相同的回调和翻译参数，并提供类型标注。回调可使用同步或异步函数，
异步回调会被等待。

| 入口 | 可选回调 | 产物 |
| --- | --- | --- |
| `ExtractionOptions` | `on_body`、`on_footnote` | 独立 PCEX XML `Element` 快照 |
| `render_markdown`、`render_epub`、`translate_extraction` | `on_body`、`on_footnote` | `ParagraphContent[SourceRegion]` |
| `patch_pdf_with_extraction`、`translate_pdf` | `on_body`、`on_footnote` | `PDFParagraphContent` |
| `translate_epub` | `on_body` | `EpubFragmentContent` |
| 提取与翻译 | `on_metadata` | `MetadataContent` |

`ParagraphContent(text, original, regions)` 对应完整自然段，text 与可选 original 是分离的
Markdown。`SourceRegion(page, source_order_in_page, bbox)` 将 `PageGeometry`、原始
fragment 身份和整数 OCR bbox 放在一起，`map_bbox(target_size)` 按实际宽高返回浮点坐标。
`PDFSourceRegion` 额外包含最终字号、行、公式及擦除信息，全部布局值以来源像素为单位；
不提供字体分发，不承诺浏览器与 Qt 的逐字形一致性。
`EpubFragmentContent(epubcfi, original, translated)` 对应原 EPUB 的原子翻译片段，
原译文均为纯文本；`EpubTranslation` 是同类型别名。从 2.5.1 起，字段和构造顺序为
`epubcfi: str | None`、`original: str`、`translated: str`。使用定位前判断 `epubcfi is not None`；
`text/html` 或明确缺少可靠定位信息时使用 `None`，原译文仍正常交付。混合格式 EPUB 的其他
片段保留可靠 CFI。解析、翻译、意外映射错误和回调异常仍传播；同步与异步门面契约一致。

`MetadataContent(metadata, original=None)` 包含完整 `DocumentMetadata`，作者使用
`DocumentAuthor`。一键转换中，`ExtractionOptions.on_metadata` 观察提取，顶层
`on_metadata` 观察可选翻译。内容与元信息回调独立于进度事件；输入尚未结束时，
全书翻译总量可以是 `None`。

`render_markdown` / `render_epub` 可传 `translator`、`window`、`with_anchored` 在线翻译。
`patch_pdf_with_extraction` 可传 `translator`、`window`、`with_furniture` 和
`on_translation_event`；`translation_id` 选择已有译文，不能与 `translator` 同时使用。

| 资源读取方法 | 返回值 |
| --- | --- |
| `read_pages(extraction)` | `dict[int, PageGeometry]`，按从 1 开始的页码索引 |
| `read_asset(extraction, asset_hash)` | 独立资源 `bytes` |

`ExtractionOptions.context` 接收 `ExtractionContext`，供提取期间查询：
`context.read_pages()` 同步返回几何快照，`await context.read_asset(hash)` 在提取活跃期间
读取资源。同一 context 不得在并发提取之间共享。

内容、几何与元信息值类型可从 `pdf_craft` 导入；XML 快照使用标准库
`xml.etree.ElementTree.Element`。内部翻译映射、提交、磁盘存储钩子和 PDF 预览调度字段
不属于公共扩展点。详细字段、顺序、有效期及文件提交语义见[增量回调指南](STREAMING.md)。


统一策略、默认严格失败和全局全部降级的规则见 [错误处理](ERROR_HANDLING.md)。

内容回调和进度独立及时交付；PAGE 完成与文件成功不同，PDF 回填只等关联页依赖。详细边界见 [流式说明](STREAMING.md)。


LLM、JEV 和 OCR vendor 构造参数 `on_request` 可观察每次实际请求尝试，见[请求观察](REQUEST_OBSERVATIONS.md)。

请求观察事件包含进程内 `call_id`，同一次调用的重试共用 ID；详见[请求观察](REQUEST_OBSERVATIONS.md)。
