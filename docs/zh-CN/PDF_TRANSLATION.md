# PDF 转换与翻译指南

本文面向已经完成基础安装、准备使用 pdf-craft 库进行 PDF 高级处理的用户。这里不展开
OCR backend、安装环境或 EPUB 文件本身的翻译；这些内容分别由对应专题文档负责。

## 先理解几种 PDF 工作流

pdf-craft 的 PDF 能力可以按使用目标分成三类：

| 目标 | 入口 | 结果 |
| --- | --- | --- |
| 直接转换 | `convert_pdf_to_markdown` / `convert_pdf_to_epub` | Markdown 或 EPUB |
| 转换时翻译 | 在上述入口传入 `steps` | 翻译后的 Markdown 或 EPUB |
| 翻译并写回 PDF | `translate_pdf` | 保留原页面的翻译后 PDF |

如果只是想完成一次转换，优先使用两个 `convert_pdf_to_*` 方法。它们会在内部完成提取、
可选内容变换和渲染。只有需要复用提取结果、分别控制每个阶段，或需要写回 PDF 时，才使用
下面的原子方法。

## 创建 PDFCraft

所有需要读取 PDF 的流程都从 `PDFCraft` 开始。`PDFOptions` 保存一次会长期复用的 PDF
基础设施配置；它不会在构造 `PDFCraft` 时立即加载 OCR 模型。

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(
    ocr=DeepSeekOCRVendorConfig(
        base_url="https://example.com/v1",
        api_key="your-api-key",
        model="deepseek-ocr",
    ),
))
```

OCR 配置的具体选择和字段不在本文展开，请参考 OCR backend 配置指南。

## PDF 转换为 Markdown

### 一次性转换

`convert_pdf_to_markdown` 会依次完成 PDF 提取、内容变换和 Markdown 渲染：

```python
craft.convert_pdf_to_markdown("input.pdf", "output.md")
```

`package_path` 默认为 `None`。省略它时，pdf-craft 使用操作系统临时目录，并在转换完成
或发生异常后清理。需要保留中间结果进行调试或再次渲染时，传入一个可写目录：

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    package_path="work/pdf-package",
)
```

Markdown 中的图片和其他资源可以通过 `assets_path` 指定输出目录；省略时使用渲染器的
默认行为：

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    assets_path="output/assets",
)
```

### 转换时翻译

通过 `steps` 可以在渲染前对章节内容执行变换。`TranslationStep` 包装一个章节变换器，
并决定译文以替换方式还是追加方式提交：

```python
from pdf_craft import SubmitKind, TranslationStep

translation = TranslationStep(translator, mode=SubmitKind.REPLACE)
craft.convert_pdf_to_markdown(
    "input.pdf",
    "translated.md",
    steps=[translation],
)
```

这里的 `translator` 必须实现章节变换器接口（提供 `transform(chapter)` 方法），负责调用
文本 LLM 并返回修改后的章节。本文只说明 pdf-craft 如何接入变换器；LLM 客户端和具体
提示词由你的应用负责准备。

`steps` 也可以传入实现 `PackageTransformer` 的变换器。多个步骤会按列表顺序执行；每个
步骤都会生成独立的变换结果，最后一个结果交给 Markdown 或 EPUB 渲染器。

## PDF 转换为 EPUB

`convert_pdf_to_epub` 与 Markdown 入口共享提取和变换流程，额外提供 EPUB 元数据和排版
选项：

```python
from epub_generator import BookMeta

craft.convert_pdf_to_epub(
    "input.pdf",
    "output.epub",
    book_meta=BookMeta(title="Book title", authors=["Author"]),
)
```

### EPUB 输出选项

- `book_meta`：EPUB 的标题、作者、出版社等元数据；省略时尝试读取源 PDF 元数据。
- `lan`：EPUB 内容语言标记，支持 `"zh"` 和 `"en"`。
- `table_render`：表格渲染方式，使用 `TableRender.HTML`、`TableRender.CLIPPING` 等
  `epub_generator` 提供的枚举值。
- `latex_render`：公式渲染方式，使用 `LaTeXRender.MATHML`、`LaTeXRender.SVG` 或
  `LaTeXRender.CLIPPING`。
- `inline_latex`：是否保留行内 LaTeX 表达式，默认值为 `True`。

转换时翻译 EPUB 的方式与 Markdown 相同：将 `TranslationStep` 放入 `steps`。已有 EPUB
文件的翻译属于另一条流程，请参考 EPUB 翻译指南。

## 翻译并写回 PDF

### `translate_pdf`

`translate_pdf` 接收原始 PDF、已经提取的结果、输出路径和翻译器。它会把译文写回原始
PDF 中与章节来源匹配的文字区域：

```python
package = craft.extract_pdf("input.pdf", "work/pdf-package")
craft.translate_pdf(
    "input.pdf",
    package,
    "translated.pdf",
    translator,
)
```

PDF 写回使用章节中的页面来源和边界框信息，因此不需要重新设计页面布局。`translator`
可以是章节变换器，也可以是接收文本并返回译文的 callable：

```python
def translator(text: str) -> str:
    return call_text_llm(text)

craft.translate_pdf("input.pdf", package, "translated.pdf", translator)
```

### PDF 输出的限制

- PDF 写回只支持替换式提交；`APPEND_BLOCK` 会被拒绝，因为 PDF pipeline 不能在原页面
  中安全追加新的块级内容。
- `source` 和 `package` 必须来自同一个 PDF。pdf-craft 会校验页面数量和页面几何元数据，
  不匹配时在写回前失败。
- `patch_pdf_with_package` 是写回已有 PDF 的操作，不是通用 PDF 排版器，不能只凭提取结果
  生成一个没有原始页面的全新 PDF。
- 写回只会修改已有来源区域；超出源 PDF 页面范围或缺少页面几何信息的结果不能写回。

### `patch_pdf_with_package`

如果译文已经由其他流程生成，可以跳过 `translate_pdf`，直接把一个结果写回原始 PDF：

```python
craft.patch_pdf_with_package(
    "input.pdf",
    "work/translated-package",
    "translated.pdf",
)
```

传入路径时，该目录必须符合 pdf-craft 的渲染结果契约；也可以直接传入
`DocumentPackage` 对象。这个入口不会调用 OCR 或 LLM。

## 原子 API

需要分别控制提取、变换和渲染时，可以组合以下公开方法：

### `extract_pdf` 与 `extract_pdf_with_metering`

这两个方法将 PDF 提取为 `DocumentPackage`。`package_path` 是必填项，因为返回对象仍然
依赖该目录中的文件；与一次性转换不同，提取方法不会自动创建并清理临时目录。

`extract_pdf_with_metering` 额外返回 `OCRTokensMetering`，用于读取本次 OCR 的输入和输出
token 计量：

```python
package, metering = craft.extract_pdf_with_metering(
    "input.pdf",
    "work/pdf-package",
)
```

### `render_markdown` 与 `render_epub`

这两个方法只负责渲染已有 `DocumentPackage`，不会重新 OCR。它们适合在提取或变换完成后
重复生成不同输出格式。

### `translate_package`

`translate_package` 将一个结果目录中的章节交给章节变换器，并生成另一个结果目录。它是
库中唯一面向结果目录翻译的公开入口；任意变换链不属于公共 API。

## `ExtractionOptions`

将一次提取所需的选项集中传给 `extract_pdf*` 或 `convert_pdf_to_*` 的 `extraction` 参数：

| 选项 | 用途 |
| --- | --- |
| `page_indexes` | 只处理指定的 1-based 页面索引 |
| `ocr_size` | 选择 OCR preset |
| `dpi` | 控制 PDF 页面渲染分辨率 |
| `max_page_image_file_size` | 限制页面图片大小，必要时调整分辨率 |
| `max_ocr_tokens` / `max_ocr_output_tokens` | 限制 OCR 请求的 token 数 |
| `includes_cover` | 生成封面图片 |
| `includes_footnotes` | 提取脚注内容 |
| `generate_plot` | 生成图表相关资源 |
| `toc_assumed` | 是否假定 PDF 中存在目录页 |
| `toc_llm` | 使用文本 LLM 辅助分析复杂目录层级 |
| `ignore_pdf_errors` / `ignore_ocr_errors` | 按布尔值或 callable 决定是否跳过页面级错误 |
| `aborted` | 外部中断检查回调 |
| `on_ocr_event` | 接收 OCR 页面事件的回调 |

`ExtractionOptions` 同时适用于 Markdown 和 EPUB；但 `toc_assumed` 的默认值应根据输出
格式选择：Markdown 默认为 `False`，EPUB 默认为 `True`。

## 计量、进度与中断

- `extract_pdf_with_metering` 返回 `OCRTokensMetering`，可用于记录 OCR token 使用量。
- `ExtractionOptions.on_ocr_event` 在 OCR 页面事件发生时回调，适合显示页面级进度。
- `ExtractionOptions.aborted` 会在提取和渲染阶段被检查；返回 `True` 时当前操作中断。
- PDF 写回和内容变换中的异常应由调用方捕获；`ignore_*_errors` 只针对提取阶段的页面级
  错误。

## 相关限制

本文只描述库的 PDF 工作流。OCR backend 的具体配置、模型缓存和 CUDA 条件请参考 OCR
backend 配置指南；已有 EPUB 的翻译和其 LLM 参数请参考 EPUB 翻译指南。
