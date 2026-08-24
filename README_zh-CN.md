<div align=center>
  <h1>PDF Craft</h1>
  <p>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/merge-build.yml" target="_blank"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/merge-build.yml" alt="ci" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/badge/pip_install-pdf--craft-blue" alt="pip install pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/v/pdf-craft.svg" alt="pypi pdf-craft" /></a>
    <a href="https://pypi.org/project/pdf-craft/" target="_blank"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="python versions" /></a>
    <a href="https://deepwiki.com/oomol-lab/pdf-craft" target="_blank"><img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt="license" /></a>
  </p>
  <p><a href="https://trendshift.io/repositories/15538" target="_blank"><img src="https://trendshift.io/api/badge/repositories/15538" alt="oomol-lab%2Fpdf-craft | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a></p>
  <p><a href="./README.md">English</a> | 中文</p>
</div>

## pdf-craft 是什么

pdf-craft 面向扫描版书籍，把 PDF 转换为 Markdown、EPUB 或翻译后的 PDF。一次提取
会生成可复用的 `DocumentPackage`，后续可以继续渲染、翻译或写回 PDF，不必每次重新
OCR。

项目提供三种本地 OCR 和三种 vendor OCR。vendor OCR 通过远程服务识别页面，不要求
本机 CUDA；local OCR 在本机运行模型，需要 CUDA。翻译使用独立的文本 LLM 配置，
与 OCR backend 分开。

核心流程是：

~~~text
PDF → DocumentPackage
DocumentPackage → translated DocumentPackage
原始 PDF + translated DocumentPackage → PDF
~~~

## 安装

项目支持 Python 3.11、3.12 和 3.13。所有 PDF 路径都需要系统安装 Poppler；请参考
[安装指南](docs/INSTALLATION_zh-CN.md)。

### 默认安装：vendor、渲染和 package 操作

~~~bash
pip install pdf-craft
~~~

默认安装包含 vendor OCR、DocumentPackage、Markdown/EPUB 渲染和 PDF patch 所需的
基础依赖，不主动安装 local OCR 的模型运行时。vendor OCR 还需要 endpoint 和凭据；
库 API 通过配置对象接收它们，不读取 .env。

### local OCR 安装

~~~bash
pip install "pdf-craft[local]"
~~~

local extra 提供 Hugging Face、Transformers 等 local OCR 运行时。真实 local OCR
还需要 CUDA-capable PyTorch、模型缓存和足够显存；没有 CUDA 时请使用 vendor OCR。

## 快速开始

下面示例使用 vendor OCR。请把 endpoint、模型名和密钥替换成你的服务配置。

~~~python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(ocr=DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr",
)))
craft.convert_pdf_to_markdown(
    "input.pdf", "output.md",
    package_path="work/package", assets_path="work/assets",
)
~~~

转换为 EPUB：

~~~python
from pdf_craft import (
    BookMeta,
    DeepSeekOCRVendorConfig,
    PDFCraft,
    PDFOptions,
)

ocr_config = DeepSeekOCRVendorConfig(
    base_url="https://example.com/v1",
    api_key="your-api-key",
    model="deepseek-ocr",
)
craft = PDFCraft(pdf=PDFOptions(ocr=ocr_config))
craft.convert_pdf_to_epub(
    "input.pdf", "output.epub",
    package_path="work/package",
    book_meta=BookMeta(title="书名", authors=["作者"]),
)
~~~

PDFCraft 是 2.0 的公共 facade。旧的 transform_markdown 和 transform_epub 仍作为
兼容包装保留；新代码建议使用 PDFCraft。

## DocumentPackage：可复用的中间文档

DocumentPackage 是一个文件夹，通常包含：

~~~text
package/
├── chapters/       # 章节 XML、文字、结构和页面坐标
├── assets/         # 图片等资源
├── toc.xml         # 可选目录
├── cover.png       # 可选封面
└── document.json   # 页面几何和 package 元数据
~~~

库层的原子操作如下：

~~~python
from pdf_craft import PDFCraft, PDFOptions, SubmitKind

# 复用上例中的 OCR 配置
craft = PDFCraft(pdf=PDFOptions(ocr=ocr_config))
package = craft.extract_pdf("input.pdf", "work/package")

# translator 实现 ChapterTransformer；这里不会重新 OCR 或读取 PDF。
translated = craft.translate_package(
    package, "work/translated-package", translator,
    submit=SubmitKind.REPLACE,
)

# 写回只使用原始 PDF 和已有 package，不调用 OCR/LLM。
PDFCraft().patch_pdf_with_package(
    "input.pdf", translated, "translated.pdf",
)
~~~

patch_pdf_with_package 使用 package 中的页面几何信息修改匹配的原始 PDF。它不是
通用 PDF 排版器，不能脱离原始 PDF 仅凭 package 生成新页面；package 与原始 PDF
不匹配时会提前失败。

## 翻译

库 API 的 PDFCraft.translate_pdf 要求调用者先准备好 DocumentPackage；它负责把翻译
package 写回原始 PDF。CLI 的 pdf translate 才会把 PDF 提取、package 翻译和 PDF patch
组合成一条命令。PDF 写回只支持替换式提交；Markdown 和 EPUB 还可以使用 append-block。

~~~python
package = craft.extract_pdf("input.pdf", "work/package")
craft.translate_pdf("input.pdf", package, "translated.pdf", translator)
~~~

已有 EPUB 可以直接翻译：

~~~python
from pdf_craft import PDFCraft, SubmitKind

PDFCraft().translate_epub(
    "input.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE,
)
~~~

OCR 只负责页面识别；章节翻译和可选的目录层级增强需要文本 chat-completion LLM。
不要把 OCR endpoint 当作翻译 LLM 使用。

## OCR backend

pdf-craft 支持 doc-page-extractor 提供的六种 backend：

- DeepSeekOCRLocalConfig：本地 DeepSeek OCR，需要 CUDA。
- DeepSeekOCR2LocalConfig：本地 DeepSeek OCR 2，需要 CUDA。
- UnlimitedOCRLocalConfig：本地 Unlimited OCR，需要 CUDA。
- DeepSeekOCRVendorConfig：OpenAI-compatible DeepSeek OCR endpoint。
- DeepSeekOCR2VendorConfig：OpenAI-compatible DeepSeek OCR 2 endpoint。
- UnlimitedOCRVendorConfig：Unlimited OCR vendor backend。

库 API 的 ocr 参数接收配置对象，不读取环境变量。CLI 使用以下 backend 名称：

~~~text
deepseek-ocr-local
deepseek-ocr2-local
unlimited-ocr-local
deepseek-ocr-vendor
deepseek-ocr2-vendor
unlimited-ocr-vendor
~~~

这些字符串由 pdf_craft_tool 从 .env 选择；--ocr-mode 只选择本次运行的 backend，
不会修改 .env。六种 backend 的配置可以同时存在。

Unlimited OCR local 仅支持 base 和 gundam；DeepSeek OCR 2 local 的已验证路径使用
base，显式使用 tiny 会快速失败并提示改用 base。

## CLI：从命令行复现流程

pdf_craft_tool 是仓库内的本地 CLI，不包含在发布的 pdf-craft Python 包中。它从
仓库根目录运行，并通过 .env.template/.env 配置 OCR backend 和文本 LLM：

~~~shell
poetry run python -m pdf_craft_tool --help
~~~

~~~shell
# PDF → 可复用 DocumentPackage
poetry run python -m pdf_craft_tool pdf extract input.pdf \
  --ocr-mode deepseek-ocr-vendor --pages 1 \
  --work-dir pdf-craft-output/extract

# package → 翻译 package
poetry run python -m pdf_craft_tool package translate \
  pdf-craft-output/extract/package zh \
  --output-package pdf-craft-output/translated-package

# package → Markdown（不需要 OCR）
poetry run python -m pdf_craft_tool package render \
  pdf-craft-output/translated-package --format markdown

# 原始 PDF + translated package → patched PDF（不需要 OCR/LLM）
poetry run python -m pdf_craft_tool package patch-pdf \
  input.pdf pdf-craft-output/translated-package \
  --output translated.pdf

# 一键 PDF 翻译
poetry run python -m pdf_craft_tool pdf translate input.pdf zh \
  --format pdf --submit replace --ocr-mode deepseek-ocr-vendor --pages 1

# EPUB → EPUB
poetry run python -m pdf_craft_tool epub translate input.epub zh \
  --submit replace
~~~

所有 --pages 都使用从 1 开始的 PDF 页码。--work-dir 用于复用 package、缓存和日志，
也方便中断后恢复。完整 CLI 参数、smoke 矩阵和 .env 字段请看
[pdf_craft_tool/README.md](pdf_craft_tool/README.md)。

## 模型缓存与常用参数

local OCR 默认可以从 Hugging Face 下载模型。生产环境可以先预下载，再使用
local_only=True：

~~~python
from pdf_craft import DeepSeekOCRLocalConfig, predownload_models

predownload_models(
    ocr=DeepSeekOCRLocalConfig(models_cache_path="models"),
    revision=None,
)
~~~

ocr_size 可使用 tiny、small、base、large 和 gundam，但不同 backend 的 preset 不完全
相同。Markdown 默认 toc_assumed=False，EPUB 默认 toc_assumed=True；复杂目录可以
传入 toc_llm。

pdf-craft 默认通过 pdf2image 使用系统 PATH 中的 Poppler。也可以向 PDFOptions 传入
自定义 PDFHandler。ignore_pdf_errors 和 ignore_ocr_errors 支持布尔值或自定义判断函数，
用于决定是否跳过单页错误。

## 兼容 API

transform_markdown、transform_epub 和 predownload_models 仍可从顶层导入。前两个是旧
API 的便利包装；新代码建议使用 PDFCraft facade。DocumentPackage 的正式操作是
extract_pdf、translate_package、render_markdown、render_epub 和 patch_pdf_with_package。

## 开发

本地贡献者环境、验证命令、手动转换检查和 VGE worktree 说明，请参考[开发指南](docs/DEVELOPMENT_zh-CN.md)。

## 在线版本

如果你希望在不进行本地安装的情况下体验 pdf-craft，可以试试 [Inkora - PDF Craft](https://inkora.oomol.com/pdf-craft/)，这是一个基于相同 PDF 转换流程构建的在线应用。你可以直接上传 PDF 文件，在浏览器中体验主要功能。

[![PDF Craft 在线版本](docs/images/website-cn.png)](https://inkora.oomol.com/pdf-craft/)

## 相关项目

- [EPUB Translator](https://github.com/oomol-lab/epub-translator)：如果你想把 PDF Craft 生成的 EPUB 继续翻译成双语版本，EPUB Translator 可以在保留原始排版、插图和目录的前提下完成转换。完整流程可参考这个[演示视频](https://www.bilibili.com/video/BV1tMQZY5EYY/)。
- [SpineDigest](https://github.com/oomol-lab/spinedigest)：如果你想进一步把转换后的书提炼成结构化摘要，SpineDigest 可以基于 EPUB 或 Markdown 生成摘要、章节拓扑和知识图谱。

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](./LICENSE) 文件。

自 v1.0.0 起，pdf-craft 全面迁移到 DeepSeek OCR（MIT 协议），移除了原有的 AGPL-3.0 依赖，使得整个项目能够以更宽松的 MIT 协议发布。注意 pdf-craft 通过 DeepSeek OCR 间接依赖了 easydict（LGPLv3 协议）。感谢社区的支持与贡献！

## 致谢

- [DeepSeekOCR](https://github.com/oomol-lab/DeepSeek-OCR)
- [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor)
- [pyahocorasick](https://github.com/WojciechMula/pyahocorasick)
