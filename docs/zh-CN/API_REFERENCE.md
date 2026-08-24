# pdf-craft 2.0 API 参考

本文面向需要把 pdf-craft 集成到自己程序中的用户。入门流程请先阅读仓库根目录的
README；本文只说明稳定的公共导入和它们如何组合。示例默认使用：

```python
from pdf_craft import PDFCraft, PDFOptions
```

## 公共入口

`pdf_craft` 在包顶层导出常用类型。最主要的入口是 `PDFCraft`，它把 PDF 提取、渲染、
翻译和 PDF 写回组合成一组方法。下面这些对象属于公共 API：

- `PDFCraft`、`PDFOptions`、`ExtractionOptions`、`TranslationStep`
- `DocumentPackage`、`PDFExtractor`
- 六种 OCR 配置对象和 `OCRConfig`
- `LLM`
- `ChapterTransformer`、`PackageTransformer`、`ChapterPackageTransformer`、
  `ChapterXMLTransformer`、`SubmitKind`
- `BookMeta`、`TableRender`、`LaTeXRender`
- `OCRTokensMetering`、`OCREvent`、`OCREventKind`、`FillFailedEvent`
- `PDFHandler`、`DefaultPDFHandler`、`PDFDocument`、`DefaultPDFDocument`
- `PDFError`、`OCRError`、`IgnorePDFErrorsChecker`、`IgnoreOCRErrorsChecker`

本文不把以下内容当作公共扩展点：内部 engine、`pdf_craft_tool` CLI、`pdf_craft` 的私有
模块路径，以及 `doc-page-extractor` 的内部 extractor/factory。应用应从 `pdf_craft` 顶层
导入 API。

## PDFCraft

### 创建实例

```python
craft = PDFCraft(pdf=PDFOptions(...))
```

`PDFCraft()` 本身不会初始化 OCR。只做 EPUB → EPUB 翻译，或只渲染一个已经存在的 package
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
不要再通过 `PDFOptions` 的后两个字段覆盖它。远程 OCR 直接把对应 vendor 配置传给 `ocr`；
本地 OCR 可以把模型缓存和离线选项写进 local 配置，也可以使用 `models_cache_path` 和
`local_only` 的默认 local OCR 路径。

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

### 提取为 DocumentPackage

```python
package = craft.extract_pdf(
    "input.pdf",
    "work/package",
    ExtractionOptions(page_indexes={1, 2}),
)
```

`extract_pdf` 要求显式提供 `package_path`，因为返回的 `DocumentPackage` 需要在调用结束
后继续有效。带计量版本返回二元组：

```python
package, metering = craft.extract_pdf_with_metering(
    "input.pdf", "work/package", ExtractionOptions()
)
print(metering.input_tokens, metering.output_tokens)
```

### 直接转换为 Markdown 或 EPUB

一键转换方法会在未提供 `package_path` 时创建系统临时目录，并在成功或异常后清理；需要
调试、复用或保留中间结果时再显式传入路径：

```python
craft.convert_pdf_to_markdown("input.pdf", "book.md")
craft.convert_pdf_to_epub(
    "input.pdf", "book.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)
```

两个方法都返回 `OCRTokensMetering`。`convert_pdf_to_markdown` 另接受 `assets_path`，
用于把渲染出的图片资源写到指定目录；EPUB 的 `lan`、`table_render`、`latex_render` 和
`inline_latex` 控制 EPUB 输出格式。

### 从已有 package 渲染

```python
craft.render_markdown(package, "book.md", assets_path="book-assets")
craft.render_epub(
    package, "book.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)
```

渲染不会重新 OCR，也不会读取 PDF。package 必须通过 `DocumentPackage.validate()` 的
结构检查；Markdown 可选复制图片资源，EPUB 会读取 package 中的章节、目录、封面和元数据。

### PDF 转换时应用翻译步骤

`TranslationStep` 把章节级或 package 级变换插入渲染前：

```python
translation = TranslationStep(translator, mode=SubmitKind.REPLACE)
craft.convert_pdf_to_markdown("input.pdf", "translated.md", steps=[translation])
```

章节翻译器需要实现类似下面的协议（`Chapter` 是章节数据对象）：

```python
class MyTranslator:
    def transform(self, chapter):
        ...
```

也可以传入实现 `transform(package, output_path) -> DocumentPackage` 的 package transformer。
多个步骤按列表顺序执行，每一步产生新的 package。`SubmitKind.REPLACE`、
`SubmitKind.APPEND_TEXT` 和 `SubmitKind.APPEND_BLOCK` 的含义取决于变换器；PDF 写回不支持
`APPEND_BLOCK`。

### 翻译并写回 PDF

```python
package = craft.extract_pdf("input.pdf", "work/package")
craft.translate_pdf(
    "input.pdf", package, "translated.pdf", translator,
)
```

`translate_pdf` 会生成翻译后的临时 package，再调用 `patch_pdf_with_package`。写回只会
替换 package 中记录了来源坐标的原始 PDF 文本，不是通用 PDF 排版器；输入 PDF 必须和 package
来自同一份源文件，并且 package 要有页面几何元数据。PDF 输出只支持替换语义。

如果已经有翻译后的 package，也可以单独写回：

```python
craft.patch_pdf_with_package("input.pdf", translated_package, "translated.pdf")
```

## DocumentPackage

`DocumentPackage` 是渲染器和变换器之间的稳定中间对象。它通常从目录读取：

```python
package = DocumentPackage.from_path("work/package")
package.validate()
```

公开字段包括：

- `chapters_path`：章节 XML 目录，必需。
- `assets_path`：图片和其他资源目录，必需。
- `toc_path`：可选目录文件。
- `cover_path`：可选封面图片。
- `metadata_path`：可选 `document.json`，包含 schema 和页面几何信息。

`validate(require_toc=True)` 可以额外要求目录存在。`has_toc()`、`has_cover()` 和
`page_pixel_sizes()` 可用于检查 package 能否用于相应输出。`page_pixel_sizes()` 返回
以 1-based 页码为键的 OCR 画布尺寸；PDF 写回依赖这些元数据。

`DocumentPackage` 是目录工件，不是独立的 PDF 或 EPUB 文件。使用 `extract_pdf` 生成的
package 可以重复渲染、翻译或写回；使用一键 `convert_pdf_to_*` 时的隐式临时 package 会
在方法返回后删除。

## 翻译与变换接口

### ChapterTransformer

章节变换器实现一个 `transform(chapter) -> chapter` 方法。它可以修改章节文本、段落或布局，
并被 `TranslationStep`、`translate_package` 和 `translate_pdf` 使用。章节对象及其布局属于
公共类型时，应保留原有页面来源信息，否则 PDF 写回可能无法定位原文。

### PackageTransformer

package 变换器实现：

```python
def transform(package: DocumentPackage, output_path: Path) -> DocumentPackage:
    ...
```

它负责把一个完整 package 写入新的输出目录，并返回新的 `DocumentPackage`。适合需要同时
修改章节、目录、资源或元数据的高级流程。

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
`concurrency`、`translation_llm`、`fill_llm`、`on_progress` 和 `on_fill_failed`；完整行为
和回调字段请参阅 EPUB 翻译专题文档。

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

`predownload_models(ocr=..., revision=None)` 可以提前下载 local 模型。`local_only=True`
会禁止缺失模型联网下载。不同模型支持的 `ocr_size` preset 不完全相同，应以 OCR 配置指南
和具体 backend 的约束为准。

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

## 组合建议

- 一次性 PDF → Markdown/EPUB：使用 `PDFCraft(pdf=PDFOptions(...)).convert_pdf_to_*`，不传
  `package_path` 即可自动管理临时目录。
- 需要重复渲染、翻译或写回：先用 `extract_pdf` 保存 package，再调用 `render_*`、
  `translate_package` 或 `patch_pdf_with_package`。
- PDF → 翻译 PDF：使用同一源 PDF 生成 package，再调用 `translate_pdf`；不要把 EPUB 的
  `APPEND_BLOCK` 语义用于 PDF。
- EPUB → EPUB：使用 `PDFCraft().translate_epub`，只配置文本 LLM，不需要 OCR。
