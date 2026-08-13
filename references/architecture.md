# 架构与模块边界

**约束范围：** 包结构、公共 API 和模块归属。**不约束：** OCR 算法细节、开发命令或发布步骤。**何时阅读：** 判断代码应放在哪里，或修改公共导入面时。

## 包公共面

`pdf_craft/__init__.py` 是公共导入面。`pdf_craft/functions.py` 提供便利函数，负责创建 `Transform` 并转发到实例方法。`pdf_craft/transform.py` 是完整 Markdown 和 EPUB 转换的编排边界。

除非任务明确要求破坏性 API 变更，否则把以下名称和默认值视为公共 API：

- `transform_markdown`
- `transform_epub`
- `predownload_models`
- `Transform`
- `LLM`
- `PDFHandler`、`PDFDocument`、`DefaultPDFHandler`、`DefaultPDFDocument`
- `BookMeta`、`TableRender`、`LaTeXRender`

## 模块归属

- `pdf_craft/pdf/` 负责 PDF 元数据、渲染、页引用、通过 `doc-page-extractor` 接入 DeepSeek OCR，以及 OCR 页 XML 数据。
- `pdf_craft/toc/` 负责目录页检测和标题层级分析，包括可选的 LLM 辅助分析。
- `pdf_craft/sequence/` 负责根据 OCR 页 XML 和 TOC 事实生成章节结构。
- `pdf_craft/markdown/` 负责 Markdown 段落解析和 Markdown 输出渲染。
- `pdf_craft/epub/` 负责把章节数据转换为 `epub-generator` 的记录并生成 EPUB。
- `pdf_craft/llm/` 负责增强目录分析所需的可选 LLM 调用。核心转换应保持不依赖该增强能力也可使用。
- `pdf_craft/common/` 负责可复用的文件系统、XML、资源和统计辅助逻辑。

## 外部包边界

`doc-page-extractor` 和 `epub-generator` 是被 pin 住的运行时依赖。它们内部的问题通常应在各自仓库修复，再通过版本升级或明确的本地联调引入。本仓库 `scripts/` 下的同步脚本会覆盖 `.venv` 中已安装的依赖源码；这些脚本只是手动本地联调辅助，不是普通项目 setup。

`torch` 和 `torchvision` 有意不作为本包运行时依赖，因为用户必须按自己的环境选择 CPU 或 CUDA wheel。不要轻易把它们加入运行时依赖。
