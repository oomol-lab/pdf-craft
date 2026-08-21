# 架构与模块边界

**约束范围：** 包结构、公共 API 和模块归属。**不约束：** OCR 算法细节、开发命令或发布步骤。**何时阅读：** 判断代码应放在哪里，或修改公共导入面时。

## 包公共面

## 新模块边界

`pdf_craft` 的主要业务模块围绕可组合的文档处理阶段组织：

- `extractor/`：PDF 页面、OCR、目录和章节分析的适配入口；产出 Document Package。
- `document/`：渲染就绪工件的路径契约和来源位置（页码、bbox、阅读顺序）。
- `renderer/`：Document Package 到 Markdown 或 EPUB 的格式渲染入口。
- `transformer/`：格式无关的 XML 内容变换，包括 LLM 翻译、结构填充和校验。
- `pipeline/`：格式专属编排。EPUB Pipeline 将 EPUB XHTML/目录/元数据交给 Transformer；PDF Pipeline 以 replace-only 方式将 Chapter 来源区域写回 PDF。

提取后的渲染工件为 `chapters/`、`assets/`、`toc.xml` 和可选 `cover.png`。`ocr/`、`plots/` 与 `done` 仅是可丢弃的分析缓存。

`pdf_craft/__init__.py` 是公共导入面。`pdf_craft/functions.py` 提供便利函数，负责创建 `Transform` 并转发到实例方法。`pdf_craft/transform.py` 是完整 Markdown 和 EPUB 转换的编排边界。

除非任务明确要求破坏性 API 变更，否则把以下名称和默认值视为公共 API：

- `transform_markdown`
- `transform_epub`
- `predownload_models`
- `Transform`
- `LLM`
- `DeepSeekOCRLocalConfig`、`DeepSeekOCR2LocalConfig`、`UnlimitedOCRLocalConfig`
- `DeepSeekOCRVendorConfig`、`DeepSeekOCR2VendorConfig`、`UnlimitedOCRVendorConfig`
- `PDFHandler`、`PDFDocument`、`DefaultPDFHandler`、`DefaultPDFDocument`
- `BookMeta`、`TableRender`、`LaTeXRender`

## 模块归属

- `pdf_craft/pdf/` 负责 PDF 元数据、渲染、页引用、通过 `doc-page-extractor` 接入 OCR 后端，以及 OCR 页 XML 数据。
- `pdf_craft/toc/` 负责目录页检测和标题层级分析，包括可选的 LLM 辅助分析。
- `pdf_craft/sequence/` 负责根据 OCR 页 XML 和 TOC 事实生成章节结构。
- `pdf_craft/markdown/` 负责 Markdown 段落解析和 Markdown 输出渲染。
- `pdf_craft/epub/` 负责把章节数据转换为 `epub-generator` 的记录并生成 EPUB。
- `pdf_craft/llm/` 负责增强目录分析所需的可选 LLM 调用。核心转换应保持不依赖该增强能力也可使用。
- `pdf_craft/common/` 负责可复用的文件系统、XML、资源和统计辅助逻辑。

## 外部包边界

`doc-page-extractor` 和 `epub-generator` 是被 pin 住的运行时依赖。它们内部的问题通常应在各自仓库修复，再通过版本升级或明确的本地联调引入。本仓库 `scripts/` 下的同步脚本会覆盖 `.venv` 中已安装的依赖源码；这些脚本只是手动本地联调辅助，不是普通项目 setup。

pdf-craft 对外只暴露自己的 OCR 配置对象，不暴露 `doc-page-extractor` 的 `PageExtractor`、`OCRAdapter` 或 factory 注入口。需要新增 OCR 后端时，优先在 `doc-page-extractor` 增加官方构造入口，再在 pdf-craft 映射成封闭配置对象。

本包通过 `doc-page-extractor[local]` 获得上游本地 OCR 运行时栈，但不要把 `torch` 或 `torchvision` 作为 pdf-craft 的直接运行时依赖；用户仍可能需要按自己的环境覆盖安装 CPU 或 CUDA wheel。
