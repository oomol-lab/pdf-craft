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
`manifest.json`、`pages.xml`、`chapters/`、`assets/`，以及可选 `toc.xml`、`cover.png`、
`furnitures.xml` 和 v4 `translations/`。`translations/index.json` 按文件内唯一 ID 排列零到多个
译文层；每层包含完整章节译文、metadata overlay、`coverage.xml`，以及可选 furniture 译文。
目标语言只是层的元数据，同一种语言可以有多个 ID。根级 `translation.xml` 仍是旧式或内部物化
译文视图的覆盖记录，不代表 v4 中可供渲染选择的持久化译文层。

PCEX 格式版本以已经发布的 Git tag 为兼容基线，不随开发期间的中间 commit 逐次递增。只有准备
新版本且格式相对上一个已发布版本发生 schema 变化时，才评估是否增加 `format_version`。当前 v4
相对 `v2.3.1` 的 v3 增加了可叠加 translation layer；读取器继续接受 v1/v2/v3。

PCEX chapter 的阅读流使用 `FlowItem`：`TextFlowItem` 是作者意义的正文/标题段，包含保留
来源 bbox 的 `SourceTextFragment`，并可在 fragment 之间嵌入图片/表格 `SourceAsset`。
`DisplayFormula` 与 `StandaloneAsset` 是独立 flow node；段落公式不是 AnchoredContent，必须
阻断段落拼接。类名与 chapter XML 元素一一对应，详见 `docs/*/PCEX_FORMAT.md`。
Narrative 翻译仅把嵌在 `TextFlowItem` 中的图片/表格替换为无文本的临时 anchor，不能读取或覆写
asset 文本；`StandaloneAsset` 不伪造 paragraph anchor，并与 NarrativeFlow 隔离；
`AnchoredContentTransformer` 是另一条按 asset 小批次工作的翻译边界，负责 title/content/caption。
目录-backed 形态只供一键转换在 `analysing_path/extraction/` 内部衔接前后端；普通目录不是
公开输入。`ocr/`、`plots/`、`done` 和其他 analysis 文件仅是可丢弃的诊断/恢复缓存。

`pdf_craft/__init__.py` 是公共导入面。`AsyncPDFCraft` 是业务核心，`PDFCraft` 是位于
`pdf_craft/sync/` 的同步兼容门面；`pdf_craft/transform.py` 是 PDF 前端
提取 engine。Markdown、EPUB、翻译和 PDF 写回后端只能读取 PDFCraftExtraction，不得读取
analysis/OCR 缓存。

`PDFCraftExtraction` 是公开但不包含业务方法的 opaque handle。用户只能通过两个 Craft 门面的
`open_extraction` / `export_extraction` 打开和导出，再把句柄传回门面或高级组件。高级可组合组件
（Extractor、Renderer、Transformer、LLM runtime）仅提供无 `_async` 后缀的异步方法；同步适配
不得进入业务核心。配置、选项、事件和数据类不拆分同步/异步版本。模型下载和环境探测属于启动前
准备，只保留同步入口。

`translate_extraction()` 保留 source layer 并追加 replacement-only 译文层。Markdown/EPUB 渲染
通过 `RenderMode.SOURCE`、`REPLACE`、`BILINGUAL` 决定只读原文、选择一份译文或合并原译文；
后两种模式可显式传 translation ID，省略时稳定选择 index 第一项。PDF 写回也可显式选择译文层，
但仅消费已有正文和 furniture 写回能力，不写回 anchored 图片/表格文字。

除非任务明确要求破坏性 API 变更，否则把以下名称和默认值视为公共 API：

- `PDFCraft`、`AsyncPDFCraft`、`PDFOptions`、`ExtractionOptions`、`PDFCraftExtraction`
- `PDFExtractor`、`MarkdownRenderer`、`EpubRenderer`
- `ExtractionTransformer`、`NarrativeXMLTransformer`
- `AnchoredContentExtractionTransformer`、`AnchoredContentTransformer`、`AnchoredContentXMLTransformer`
- `predownload_models`
- `LLM`
- `JEV`、`FootnoteOptions`、`FootnoteRefinement`
- `DeepSeekOCRLocalConfig`、`DeepSeekOCR2LocalConfig`、`UnlimitedOCRLocalConfig`
- `DeepSeekOCRVendorConfig`、`DeepSeekOCR2VendorConfig`、`UnlimitedOCRVendorConfig`
- `PDFHandler`、`AsyncPDFHandler`、`PDFDocument`、`AsyncPDFDocument`、`DefaultPDFHandler`、`DefaultPDFDocument`
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
