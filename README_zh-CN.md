<div align=center>
  <h1>PDF Craft</h1>
  <p>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/build.yml" target="_blank"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/build.yml" alt"ci" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/badge/pip_install-pdf--craft-blue" alt="pip install pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/v/pdf-craft.svg" alt"pypi pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="python versions" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt"license" /></a>
  </p>
  <p><a href="https://hub.oomol.com/package/pdf-craft?open=true" target="_blank"><img src="https://static.oomol.com/assets/button.svg" alt="Open in OOMOL Studio" /></a></p>
  <p><a href="./README.md">English</a> | 中文</p>
</div>

## 简介

pdf-craft 可以将 PDF 文件转换为各种其他格式，本项目专注于处理扫描版书籍的 PDF 文件。

本项目基于 [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR) 进行文档识别。支持表格、公式等复杂内容的识别。通过 GPU 加速，pdf-craft 能够在本地完成从 PDF 到 Markdown 或 EPUB 的完整转换流程。转换过程中，pdf-craft 会自动识别文档结构，准确提取正文内容，同时过滤页眉、页脚等干扰信息。对于包含脚注、公式、表格的学术或技术文档，pdf-craft 也能妥善处理，保留这些重要元素（包括脚注中的图片等资源）。转换为 EPUB 时会自动生成目录。最终生成的 Markdown 或 EPUB 文件保持了原书的内容完整性和可读性。

## 轻装上阵

从 v1.0.0 正式版开始，pdf-craft 全面拥抱 [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR)，不再依赖 LLM 进行文本矫正。这一改变带来了显著的性能提升：整个转换流程在本地完成，无需网络请求，告别了旧版本中漫长的等待和偶发的网络失败。

不过，新版本也移除了 LLM 文本矫正功能。如果你的使用场景仍然需要这一特性，可以继续使用 [v0.2.8](https://github.com/oomol-lab/pdf-craft/tree/v0.2.8) 旧版本。

## 快速开始

### 在线体验

我们提供了 [在线演示平台](https://pdf.oomol.com/)，让你无需安装即可体验 PDF Craft 的转换效果。你可以直接上传 PDF 文件并转换。

[![PDF Craft 在线演示](docs/images/website-cn.png)](https://pdf.oomol.com/)

### 安装

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install pdf-craft
```

本项目使用 DeepSeek OCR，依赖 CUDA 环境。 **上述命令仅能确保 Python 能读到类型不报错，无法真正运行 OCR 识别。** 具体 CUDA 环境的安装方式请参考[安装指南](docs/INSTALLATION_zh-CN.md)。

### 快速开始

#### 转换为 Markdown

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    markdown_assets_path="images",
)
```

![](docs/images/pdf2md-cn.png)

#### 转换为 EPUB

```python
from pdf_craft import transform_epub, BookMeta

transform_epub(
    pdf_path="input.pdf",
    epub_path="output.epub",
    book_meta=BookMeta(
        title="书名",
        authors=["作者"],
    ),
)
```

![](docs/images/pdf2epub-cn.png)

## 详细使用

### 转换为 Markdown

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    markdown_assets_path="images",
    analysing_path="temp",  # 可选：指定临时文件夹
    ocr_size="gundam",  # 可选：tiny, small, base, large, gundam
    models_cache_path="models",  # 可选：模型缓存路径
    includes_footnotes=True,  # 可选：包含脚注
    ignore_fitz_errors=False,  # 可选：遇到 PDF 渲染错误时继续处理
    generate_plot=False,  # 可选：生成可视化图表
)
```

### 转换为 EPUB

```python
from pdf_craft import transform_epub, BookMeta, TableRender, LaTeXRender

transform_epub(
    pdf_path="input.pdf",
    epub_path="output.epub",
    analysing_path="temp",  # 可选：指定临时文件夹
    ocr_size="gundam",  # 可选：tiny, small, base, large, gundam
    models_cache_path="models",  # 可选：模型缓存路径
    includes_cover=True,  # 可选：包含封面
    includes_footnotes=True,  # 可选：包含脚注
    ignore_fitz_errors=False,  # 可选：遇到 PDF 渲染错误时继续处理
    generate_plot=False,  # 可选：生成可视化图表
    book_meta=BookMeta(
        title="书名",
        authors=["作者1", "作者2"],
        publisher="出版社",
        language="zh",
    ),
    lan="zh",  # 可选：语言 (zh/en)
    table_render=TableRender.HTML,  # 可选：表格渲染方式
    latex_render=LaTeXRender.MATHML,  # 可选：公式渲染方式
    inline_latex=True,  # 可选：保留内联 LaTeX 表达式
)
```

### 模型管理

pdf-craft 依赖 DeepSeek OCR 模型，首次运行时会自动从 Hugging Face 下载。你可以通过 `models_cache_path` 和 `local_only` 参数控制模型的存储和加载行为。

#### 预下载模型

在生产环境中，建议提前下载模型，避免首次运行时下载：

```python
from pdf_craft import predownload_models

predownload_models(
    models_cache_path="models",  # 指定模型缓存目录
    revision=None,  # 可选：指定模型版本
)
```

#### 指定模型缓存路径

默认情况下，模型会下载到系统的 Hugging Face 缓存目录。你可以通过 `models_cache_path` 参数自定义缓存位置：

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    models_cache_path="./my_models",  # 自定义模型缓存目录
)
```

#### 离线模式

如果你已经预先下载了模型，可以使用 `local_only=True` 禁止网络下载，确保仅使用本地模型：

```python
from pdf_craft import transform_markdown

transform_markdown(
    pdf_path="input.pdf",
    markdown_path="output.md",
    models_cache_path="./my_models",
    local_only=True,  # 仅使用本地模型，不从网络下载
)
```

## API 参考

### OCR 模型

`ocr_size` 参数接受 `DeepSeekOCRSize` 类型：

- `tiny` - 最小模型，速度最快
- `small` - 小型模型
- `base` - 基础模型
- `large` - 大型模型
- `gundam` - 最大模型，质量最高（默认）

### 表格渲染方式

- `TableRender.HTML` - HTML 格式（默认）
- `TableRender.CLIPPING` - Clipping 格式（直接从原书扫描件上裁剪表格图像）

### 公式渲染方式

- `LaTeXRender.MATHML` - MathML 格式（默认）
- `LaTeXRender.SVG` - SVG 格式
- `LaTeXRender.CLIPPING` - Clipping 格式（直接从原书扫描件上裁剪公式图像）

### 内联 LaTeX

`inline_latex` 参数（仅 EPUB，默认：`True`）控制是否在输出中保留内联 LaTeX 表达式。启用后，内联数学公式将以 LaTeX 代码形式保留，可由支持的 EPUB 阅读器渲染。

### 错误处理

你可以使用 `ignore_fitz_errors=True` 参数，在遇到单个页面渲染失败时继续处理，为失败的页面插入占位符消息，而不是停止整个转换过程。

## 相关开源库
[epub-translator](https://github.com/oomol-lab/epub-translator) 利用 AI 大模型自动翻译 EPUB 电子书，并 100% 保留原书的格式、插图、目录和排版，同时生成 双语对照版本，方便语言学习或国际分享。与本库搭配，可将扫描 PDF 书籍转换并翻译。搭配使用可参考 [视频：PDF 扫描件书籍转 EPUB 格式，翻译成双语书](https://www.bilibili.com/video/BV1tMQZY5EYY)。

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](./LICENSE) 文件。

自 v1.0.0 起，pdf-craft 全面迁移到 DeepSeek OCR（MIT 协议），移除了原有的 AGPL-3.0 依赖，使得整个项目能够以更宽松的 MIT 协议发布。感谢社区的支持与贡献！

## 致谢

- [DeepSeekOCR](https://github.com/deepseek-ai/DeepSeek-OCR)
- [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor)
