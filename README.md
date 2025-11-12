# pdf-craft

将 PDF 文件转换为各种其他格式的工具。本项目专注于处理扫描书籍的 PDF 文件，支持 OCR 识别、表格提取、公式识别等功能。

## 功能特性

- **PDF 转 Markdown**：将 PDF 文件转换为 Markdown 格式，保留文档结构
- **PDF 转 EPUB**：将 PDF 文件转换为电子书格式，支持自定义书籍元信息
- **OCR 文字识别**：支持扫描版 PDF 的文字识别
- **表格识别**：智能识别并提取 PDF 中的表格内容
- **公式识别**：支持 LaTeX 格式的数学公式识别
- **脚注支持**：可选择是否包含文档中的脚注内容
- **进度追踪**：实时显示 OCR 处理进度

## 系统要求

- Python 3.10 - 3.13
- （可选）NVIDIA GPU 支持 CUDA 11.8、12.1 或 12.4

## 安装说明

### CPU 版本（仅文本处理，无 OCR 功能）

```bash
pip install pdf-craft
```

### GPU 版本（支持 OCR、表格识别、公式识别）

根据你的 CUDA 版本选择对应的安装命令：

**CUDA 11.8：**
```bash
pip install --index-url https://download.pytorch.org/whl/cu118 pdf-craft[gpu]
```

**CUDA 12.1（推荐）：**
```bash
pip install --index-url https://download.pytorch.org/whl/cu121 pdf-craft[gpu]
```

**CUDA 12.4：**
```bash
pip install --index-url https://download.pytorch.org/whl/cu124 pdf-craft[gpu]
```

### 验证安装

运行以下命令验证 GPU 是否正确配置：

```python
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
```

如果显示 `CUDA available: True`，说明 GPU 配置成功。

## 使用方法

### PDF 转 Markdown

```python
from pathlib import Path
from pdf_craft import transform_markdown, OCREventKind

transform_markdown(
    pdf_path=Path("input.pdf"),
    markdown_path=Path("output.md"),
    markdown_assets_path=Path("images"),  # Markdown 中图片的相对路径
    analysing_path=Path("analysing"),     # 分析文件输出目录（可选）
    models_cache_path=Path("models-cache"),  # 模型缓存目录（可选）
    includes_footnotes=True,              # 是否包含脚注
    generate_plot=True,                   # 是否生成绘图分析文件
    on_ocr_event=lambda e: print(f"OCR {OCREventKind(e.kind).name} - 页面 {e.page_index}/{e.total_pages} - {e.cost_time_ms}ms"),
)
```

### PDF 转 EPUB

```python
from pathlib import Path
from pdf_craft import transform_epub, OCREventKind, TableRender, LaTeXRender, BookMeta

transform_epub(
    pdf_path=Path("input.pdf"),
    epub_path=Path("output.epub"),
    analysing_path=Path("analysing"),     # 分析文件输出目录（可选）
    models_cache_path=Path("models-cache"),  # 模型缓存目录（可选）
    includes_footnotes=True,              # 是否包含脚注
    generate_plot=True,                   # 是否生成绘图分析文件
    table_render=TableRender.HTML,        # 表格渲染方式：HTML 或 MARKDOWN
    latex_render=LaTeXRender.MATHML,      # 公式渲染方式：MATHML 或 LATEX
    on_ocr_event=lambda e: print(f"OCR {OCREventKind(e.kind).name} - 页面 {e.page_index}/{e.total_pages} - {e.cost_time_ms}ms"),
    book_meta=BookMeta(
        title="书籍标题",
        authors=["作者1", "作者2"],
    ),
)
```

### 参数说明

#### transform_markdown 参数

- `pdf_path`: PDF 文件路径
- `markdown_path`: 输出的 Markdown 文件路径
- `markdown_assets_path`: Markdown 中引用的图片资源相对路径
- `analysing_path`: （可选）分析过程文件的输出目录，用于调试
- `models_cache_path`: （可选）AI 模型缓存目录，避免重复下载
- `includes_footnotes`: 是否包含脚注内容（默认 `True`）
- `generate_plot`: 是否生成分析图表（默认 `False`）
- `on_ocr_event`: OCR 进度回调函数

#### transform_epub 参数

除了 `transform_markdown` 的参数外，还包括：

- `epub_path`: 输出的 EPUB 文件路径
- `table_render`: 表格渲染方式
  - `TableRender.HTML`: 使用 HTML 表格
  - `TableRender.MARKDOWN`: 使用 Markdown 表格
- `latex_render`: 公式渲染方式
  - `LaTeXRender.MATHML`: 使用 MathML 格式
  - `LaTeXRender.LATEX`: 使用 LaTeX 格式
- `book_meta`: 书籍元信息（`BookMeta` 对象）
  - `title`: 书籍标题
  - `authors`: 作者列表

#### OCREventKind 事件类型

- `OCREventKind.START`: OCR 开始
- `OCREventKind.PROGRESS`: OCR 进行中
- `OCREventKind.COMPLETE`: OCR 完成

## 示例脚本

项目提供了两个示例脚本，位于 [scripts/](scripts/) 目录：

- [gen_md.py](scripts/gen_md.py) - PDF 转 Markdown 的完整示例
- [gen_epub.py](scripts/gen_epub.py) - PDF 转 EPUB 的完整示例

## 开发

### 安装开发依赖

```bash
poetry install
```

### 运行测试

```bash
poetry run pytest
```

### 代码检查

```bash
poetry run pylint pdf_craft
```

## 许可证

本项目基于 [MIT 许可证](LICENSE) 开源。

## 相关链接

- [项目主页](https://hub.oomol.com/package/pdf-craft)
- [GitHub 仓库](https://github.com/oomol-lab/pdf-craft)
- [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor) - 核心 OCR 依赖

## 贡献

欢迎提交 Issue 和 Pull Request！
