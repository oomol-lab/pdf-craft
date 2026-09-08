# PDF 转换与翻译指南

本文面向已经完成基础安装、准备使用 pdf-craft 库进行 PDF 高级处理的用户。这里不展开
OCR backend、安装环境或 EPUB 文件本身的翻译；这些内容分别由对应专题文档负责。

## 先理解几种 PDF 工作流

pdf-craft 的 PDF 能力可以按使用目标分成三类：

| 目标 | 入口 | 结果 |
| --- | --- | --- |
| 直接转换 | `convert_pdf_to_markdown` / `convert_pdf_to_epub` | Markdown 或 EPUB |
| 转换时翻译 | 在上述入口传入一个 `translator` | 翻译后的 Markdown 或 EPUB |
| 翻译并写回 PDF | `translate_pdf` | 保留源页、覆盖擦除层与可提取译文文本层的新 PDF |

如果只是想完成一次转换，优先使用两个 `convert_pdf_to_*` 方法。它们会在内部完成提取、
可选内容变换和渲染。只有需要复用提取结果、分别控制每个阶段，或需要写回 PDF 时，才使用
下面的原子方法。

## 创建 PDFCraft

通过 `PDFCraft` 门面 API 读取 PDF 的流程从 `PDFCraft` 开始。本文后面介绍的
`PDFTranslationPipeline` / `PDFPatcher` 是可直接读取并写回 PDF 的低层入口，不经过该门面。
`PDFOptions` 保存一次会长期复用的 PDF 基础设施配置；它不会在构造 `PDFCraft` 时立即加载 OCR
模型。

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

`PDFOptions` 还提供 `models_cache_path` 与 `local_only`，但它们只用于未显式传入 `ocr` 的
情况：此时 pdf-craft 会构造 `DeepSeekOCRLocalConfig`，将 `models_cache_path` 作为本地模型
缓存目录，并原样传递 `local_only`。默认 `local_only=False`，即本地模型加载允许下载缺失
文件；显式设为 `True` 时只使用本地缓存，适合模型已完整缓存、希望避免运行时下载的环境，
缓存不完整时实际加载会失败。若已经显式传入任一种 `ocr` 配置（无论本地还是供应商），就不能
传入 `models_cache_path`（即值不是 `None`），也不能启用 `local_only=True`，否则构造提取引擎
时会抛出 `ValueError`；显式传入默认值 `local_only=False` 不会触发该错误。应将这两个设置直接
放进对应的本地 OCR 配置，或只使用 `PDFOptions` 的默认本地配置二者之一。

## PDF 转换为 Markdown

### 一次性转换

`convert_pdf_to_markdown` 会依次完成 PDF 提取、内容变换和 Markdown 渲染：

```python
craft.convert_pdf_to_markdown("input.pdf", "output.md")
```

两个一次性转换入口都会返回 `OCRTokensMetering`，可据此记录本次 OCR 的输入与输出 token：

```python
metering = craft.convert_pdf_to_markdown("input.pdf", "output.md")
print(metering.input_tokens, metering.output_tokens)
```

默认情况下，pdf-craft 使用操作系统临时 analysis 目录，并在转换完成或发生异常后清理。
需要保留诊断信息时传入 `analysing_path`；需要复用或跨机器交换中间结果时传入
`extraction_path` 导出 `.pcex`：

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    analysing_path="work/analysis",
    extraction_path="work/book.pcex",
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

可以在渲染前传入一个章节翻译器，完成一次翻译，并决定译文以替换方式还是追加方式提交：

```python
from pdf_craft import SubmitKind

craft.convert_pdf_to_markdown(
    "input.pdf",
    "translated.md",
    translator=translator,
    submit=SubmitKind.REPLACE,
)
```

这里的 `translator` 必须实现章节变换器接口（提供 `transform(chapter)` 方法），负责调用
文本 LLM 并返回修改后的章节。本文只说明 pdf-craft 如何接入变换器；LLM 客户端和具体
提示词由你的应用负责准备。

高层转换方法只执行一次翻译。需要分段或更细粒度控制时，可以使用
`extract_pdf()`、`translate_extraction()` 和 `render_*()` 自行组合。

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

该方法同样返回 `OCRTokensMetering`。

### EPUB 输出选项

- `book_meta`：EPUB 的标题、作者、出版社等元数据；省略时尝试读取源 PDF 元数据。
- `lan`：EPUB 内容语言标记，支持 `"zh"` 和 `"en"`。
- `table_render`：表格渲染方式，使用 `TableRender.HTML`、`TableRender.CLIPPING` 等
  `epub_generator` 提供的枚举值。
- `latex_render`：公式渲染方式，使用 `LaTeXRender.MATHML`、`LaTeXRender.SVG` 或
  `LaTeXRender.CLIPPING`。
- `inline_latex`：是否保留行内 LaTeX 表达式，默认值为 `True`。

转换时翻译 EPUB 的方式与 Markdown 相同：传入一个 `translator`。已有 EPUB
文件的翻译属于另一条流程，请参考 EPUB 翻译指南。

## 翻译并写回 PDF

### `translate_pdf`

`translate_pdf` 接收原始 PDF、已经提取的结果、输出路径和翻译器。它会把译文写回原始
PDF 中与章节来源匹配的文字区域：

```python
extraction = craft.extract_pdf("input.pdf", "work/book.pcex")
craft.translate_pdf(
    "input.pdf",
    extraction,
    "translated.pdf",
    translator,
)
```

PDF 写回使用章节中的页面来源和边界框信息，因此不需要重新设计页面布局。`translator`
可以是章节变换器，也可以是接收文本并返回译文的 callable：

```python
def translator(text: str) -> str:
    return call_text_llm(text)

craft.translate_pdf("input.pdf", extraction, "translated.pdf", translator)
```

PDF 输出不接受 `APPEND_BLOCK` 模式，因为 PDF pipeline 不能在原页面中安全追加新的块级内容。

### PDF 输出的限制

- PDF 写回明确不支持 `APPEND_BLOCK`，因为 PDF pipeline 不能在原页面中安全追加新的
  块级内容；`REPLACE` 与 `APPEND_TEXT` 不会被该入口预先拒绝。
- 写回前会检查结果是否带有页面几何元数据、章节和几何中涉及的页码是否落在源 PDF 页数
  范围内，以及每个章节页面是否具有对应的几何记录。它不验证结果目录是否确实由该源 PDF
  提取而来，因此调用方应自行确保二者匹配。
- 每个源页会先被渲染成图像，再与覆盖后的译文一起写入输出 PDF。因此输出页以源页图像为
  背景，不保留原 PDF 的矢量文字、链接、注释等页面对象；需要这些对象或可编辑矢量内容时，
  应评估该流程是否适用。
- 写回只处理 `ref` 为 `text` 或 `sub_title` 的 `ParagraphLayout`。图片、表格以及其他
  布局不会成为可替换项。
- 每段译文都必须在对应 OCR bbox 内排版。默认排版策略会在允许的字号范围内寻找可容纳的
  字号；最小字号仍无法容纳时抛出 `ValueError`。所有 bbox 会先完成预检，因此失败时不会
  留下部分输出文件。
- `patch_pdf_with_extraction` 是写回已有 PDF 的操作，不是通用 PDF 排版器，不能只凭提取结果
  生成一个没有原始页面的全新 PDF。

### `patch_pdf_with_extraction`

如果译文已经由其他流程生成，可以跳过 `translate_pdf`，直接把一个结果写回原始 PDF：

```python
craft.patch_pdf_with_extraction(
    "input.pdf",
    "work/translated.pcex",
    "translated.pdf",
)
```

传入路径时必须是通过校验的 `.pcex`；也可以直接传入 `PDFCraftExtraction` 对象。普通目录
不是公开输入。这个入口不会调用 OCR 或 LLM。PDF 写回使用 `pypdf`、`reportlab` 和
PySide6/Qt；在依赖被移除或非标准安装的环境中，底层导入失败会抛出 `RuntimeError`。

### 调整写回排版

`PDFCraft.translate_pdf` 与 `PDFCraft.patch_pdf_with_extraction` 为简化调用而设计，不提供字体、
字号、对齐、padding 或 overflow 策略参数。需要调整这些规则时，使用公开的低层
`PDFPatcher`、`PatchTextOptions` 与 `EraseOptions`，再交给 `PDFTranslationPipeline`：

```python
from pathlib import Path

from pdf_craft import EraseOptions, PDFPatcher, PDFTranslationPipeline, PatchTextOptions, PatchTextStyle

patcher = PDFPatcher(options=PatchTextOptions(
    font_name="Noto Sans CJK SC",  # 优先字体；缺失时由 Qt fallback
    max_font_size=14,
    min_font_size=5,
    alignment="left",
    horizontal_padding=1,
    vertical_padding=1,
    styles={"sub_title": PatchTextStyle(max_font_size=18, min_font_size=8)},
    overflow="error",
), erase_options=EraseOptions(padding=2))
pipeline = PDFTranslationPipeline(patcher=patcher)
pipeline.translate(Path("input.pdf"), Path("translated.pdf"), extraction, translator)
```

`overflow="error"` 是默认策略，无法容纳的译文会失败；`overflow="skip"` 会跳过该 bbox，
并将原因记录在 `patcher.skipped_replacements`。低层 API 适用于愿意自行处理排版策略、
跳过结果和输出文件生命周期的高级调用方。

### 写回的页面与文本层

写回不会将整页通过 `PDFHandler.render_page()` 栅格化。它保留原始 PDF 页，再依次合并独立的
矩形擦除 overlay 和由 Qt `QTextLayout` / PDF paint device 生成的译文文本 overlay。因此扫描页
仍会自然保留扫描背景，原生 PDF 页也不会因写回而压扁；译文则是可缩放、可提取的 PDF 文字。

擦除会先以 padding 扩大每个来源 bbox，然后只渲染原始页来计算该矩形内按频次加权的 RGB 中位背景色，
再以这个颜色完整覆盖扩展矩形。渲染页不会作为输出页，写回仍保留原始 PDF 页。当前擦除不恢复纸张纹理、
表格线、公式或插图，底层的原文字内容流也可能仍可被提取。写回需要本机具备 PySide6/Qt、Poppler（或
调用方提供的 `PDFHandler`）和可用字体；字体配置是优先选择，Qt 找不到指定字体或字形时会使用系统 fallback。

## 原子 API

需要分别控制提取、变换和渲染时，可以组合以下公开方法：

### `extract_pdf` 与 `extract_pdf_with_metering`

这两个方法将 PDF 提取为 `PDFCraftExtraction`。`.pcex` 输出路径是必填项；该文件可以直接
存储、上传或交给另一台机器上的后端。普通目录不能作为后端公开输入。

`extract_pdf_with_metering` 额外返回 `OCRTokensMetering`，用于读取本次 OCR 的输入和输出
token 计量：

```python
extraction, metering = craft.extract_pdf_with_metering(
    "input.pdf",
    "work/book.pcex",
)
```

### `render_markdown` 与 `render_epub`

这两个方法只负责渲染已有 `PDFCraftExtraction`，不会重新 OCR。它们适合在提取或变换完成后
重复生成不同输出格式。

### `translate_extraction`

`translate_extraction` 将一个 `.pcex` 中的章节交给章节变换器，并生成另一个 `.pcex`。输出会
保留 manifest、pages、TOC、封面和 assets；任意变换链不属于公共 API。

## `ExtractionOptions`

将一次提取所需的选项集中传给 `extract_pdf*` 或 `convert_pdf_to_*` 的 `extraction` 参数：

| 选项 | 默认值 | 用途 |
| --- | --- | --- |
| `page_indexes` | `None` | 只处理指定的 1-based 页面索引 |
| `ocr_size` | `"gundam"` | 选择 OCR preset；不同 preset 的质量、速度和资源消耗取决于所选 backend |
| `dpi` | `None` | 控制 PDF 页面渲染分辨率 |
| `max_page_image_file_size` | `None` | 限制页面图片大小，必要时调整分辨率 |
| `max_ocr_tokens` / `max_ocr_output_tokens` | `None` | 限制 OCR 请求的 token 数 |
| `includes_cover` | `False` | 生成封面图片 |
| `includes_footnotes` | `False` | 提取脚注内容 |
| `generate_plot` | `False` | 生成图表相关资源 |
| `toc_assumed` | `False` | 是否假定 PDF 中存在目录页 |
| `toc_llm` | `None` | 使用文本 LLM 辅助分析复杂目录层级 |
| `ignore_pdf_errors` / `ignore_ocr_errors` | `False` | 按布尔值或 callable 决定是否跳过页面级错误 |
| `aborted` | 始终返回 `False` 的回调 | 外部中断检查回调 |
| `on_ocr_event` | 无操作回调 | 接收 OCR 页面事件的回调 |

`ExtractionOptions` 同时适用于 Markdown 和 EPUB。`toc_assumed` 的公共默认值始终是
`False`，包括 `convert_pdf_to_epub`；若你的 PDF 确实包含需要按目录页处理的目录，应显式
传入 `ExtractionOptions(toc_assumed=True)`。

没有显式设置 `dpi` 时，提取时实际以 `300` DPI 渲染页面，且生成的 `pages.xml` 会记录
该值；`ExtractionOptions.dpi` 的 `None` 表示采用默认值，不表示没有 DPI。提高
提取 DPI 可能改善小字或细节的 OCR 输入，但同时增加图像尺寸、处理时间和资源消耗。页面像素
尺寸会随提取 DPI 写入 `pages.xml`，供后续 PDF 写回将 OCR bbox 对齐到源页。缺少几何时
后端会直接拒绝，不会读取 OCR 缓存或重新渲染 PDF 来补齐。

## 计量、进度与中断

- `extract_pdf_with_metering` 与两个 `convert_pdf_to_*` 入口都会返回 `OCRTokensMetering`，
  可用于记录 OCR token 使用量。
- `ExtractionOptions.on_ocr_event` 在 OCR 页面事件发生时回调，适合显示页面级进度。
- `ExtractionOptions.aborted` 会在提取和渲染阶段被检查；返回 `True` 时当前操作中断。
- PDF 写回和内容变换中的异常应由调用方捕获；`ignore_*_errors` 只针对提取阶段的页面级
  错误。

## 相关限制

本文只描述库的 PDF 工作流。OCR backend 的具体配置、模型缓存和 CUDA 条件请参考 OCR
backend 配置指南；已有 EPUB 的翻译和其 LLM 参数请参考 EPUB 翻译指南。
