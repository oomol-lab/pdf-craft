# 架构与模块边界

**约束范围：** 包结构、公共 API 和模块归属。**不约束：** OCR 算法细节、开发命令或发布步骤。**何时阅读：** 判断代码应放在哪里，或修改公共导入面时。

## 包公共面

## 新模块边界

`pdf_craft` 的主要业务模块围绕可组合的文档处理阶段组织：

- `extractor/`：PDF 页面、OCR、目录和章节分析的适配入口；产出 PDFCraftExtraction。
- `document/`：PDFCraftExtraction 的 workspace/`.pcex` 存储、校验和来源位置契约。
- `renderer/`：PDFCraftExtraction 到 Markdown 或 EPUB 的格式渲染入口。
- `transformer/`：格式无关的 XML 内容变换，包括 LLM 翻译、结构填充和校验。
- `pipeline/`：格式专属编排。EPUB Pipeline 将 EPUB XHTML/目录/元数据交给 Transformer；PDF Pipeline 以 replace-only 方式将 Chapter 来源区域写回 PDF。

公开中间格式为 `PDFCraftExtraction`，持久化与交换载体必须是 `.pcex` ZIP。其内容为
`manifest.json`、`pages.xml`、`chapters/`、`assets/`，以及可选 `toc.xml`、`cover.png`。
目录-backed 形态只供一键转换在 `analysing_path/extraction/` 内部衔接前后端；普通目录不是
公开输入。`ocr/`、`plots/`、`done` 和其他 analysis 文件仅是可丢弃的诊断/恢复缓存。

`pdf_craft/__init__.py` 是公共导入面。`PDFCraft` 是门面；`pdf_craft/transform.py` 是 PDF 前端
提取 engine。Markdown、EPUB、翻译和 PDF 写回后端只能读取 PDFCraftExtraction，不得读取
analysis/OCR 缓存。

除非任务明确要求破坏性 API 变更，否则把以下名称和默认值视为公共 API：

- `PDFCraft`、`PDFOptions`、`ExtractionOptions`、`PDFCraftExtraction`
- `PDFExtractor`、`MarkdownRenderer`、`EpubRenderer`
- `ExtractionTransformer`、`ChapterExtractionTransformer`、`ChapterXMLTransformer`
- `predownload_models`
- `LLM`
- `DeepSeekOCRLocalConfig`、`DeepSeekOCR2LocalConfig`、`UnlimitedOCRLocalConfig`
- `DeepSeekOCRVendorConfig`、`DeepSeekOCR2VendorConfig`、`UnlimitedOCRVendorConfig`
- `PDFHandler`、`PDFDocument`、`DefaultPDFHandler`、`DefaultPDFDocument`
- `BookMeta`、`TableRender`、`LaTeXRender`

## 模块归属

- `pdf_craft/pdf/` 负责 PDF 元数据、渲染、页引用、通过 `doc-page-extractor` 接入 OCR 后端，以及 OCR 页 XML 数据。
- `pdf_craft/extractor/toc/` 负责目录页检测和标题层级分析，包括可选的 LLM 辅助分析。
- `pdf_craft/extractor/chapter/` 负责根据 OCR 页 XML 和 TOC 事实生成章节结构。
- `pdf_craft/markdown/` 负责 Markdown 段落解析和 Markdown 输出渲染。
- `pdf_craft/renderer/epub/` 负责把章节数据转换为 `epub-generator` 的记录并生成 EPUB。
- `pdf_craft/llm/` 负责增强目录分析所需的可选 LLM 调用。核心转换应保持不依赖该增强能力也可使用。
- `pdf_craft/common/` 负责可复用的文件系统、XML、资源和统计辅助逻辑。

## 外部包边界

`doc-page-extractor` 和 `epub-generator` 是被 pin 住的运行时依赖。它们内部的问题通常应在各自仓库修复，再通过版本升级或明确的本地联调引入。本仓库 `scripts/` 下的同步脚本会覆盖 `.venv` 中已安装的依赖源码；这些脚本只是手动本地联调辅助，不是普通项目 setup。

pdf-craft 对外只暴露自己的 OCR 配置对象，不暴露 `doc-page-extractor` 的 `PageExtractor`、`OCRAdapter` 或 factory 注入口。需要新增 OCR 后端时，优先在 `doc-page-extractor` 增加官方构造入口，再在 pdf-craft 映射成封闭配置对象。

本包通过 `doc-page-extractor[local]` 获得上游本地 OCR 运行时栈，但不要把 `torch` 或 `torchvision` 作为 pdf-craft 的直接运行时依赖；用户仍可能需要按自己的环境覆盖安装 CPU 或 CUDA wheel。
