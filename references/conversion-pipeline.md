# 转换流水线

**约束范围：** PDF 到输出文件的流程、中间产物和转换契约。**不约束：** 通用 setup 或打包。**何时阅读：** 修改提取、TOC、章节生成、Markdown 渲染或 EPUB 渲染时。

## 运行时流程

## 组合流程

Extractor 生成 PDFCraftExtraction 后，Renderer 可直接生成 Markdown 或 EPUB。可选 Transformer
可以在渲染前修改结构化文本；`pipeline/epub` 也可把既有 EPUB 的 XHTML、目录和元数据交给
同一个 XML Transformer。PDF Translation Pipeline 只处理已记录来源 bbox 内的文本。

`PDFCraft.convert_pdf_to_markdown()` 和 `PDFCraft.convert_pdf_to_epub()` 会先提取到内部 workspace，
再渲染目标输出。提取流程是：

1. 通过 `PDFHandler` 渲染 PDF 页面。
2. 通过 `OCR.recognize()` 识别页面布局。
3. 在 `analysing_path/ocr/` 写入 OCR 页 XML 和恢复缓存，在
   `analysing_path/extraction/assets/` 写入资源。
4. 分析 TOC 数据。
5. 在 `analysing_path/extraction/` 生成章节、TOC、页面几何和 manifest。
6. 仅从该 extraction 渲染 Markdown 或 EPUB。

当未传入 `analysing_path` 时，`EnsureFolder` 会创建临时目录。当传入该路径时，它会成为可持久复用的缓存和调试输出目录。

## 中间产物契约

analysis 与稳定 extraction 明确分离：

- `ocr/page_*.xml`：OCR 页数据。
- `ocr/done`：表示所有选中页面已完成识别的标记。
- `plots/`：启用 plot 生成时的可选可视化调试输出。
- `extraction/manifest.json`：格式版本、producer、创建时间和文档元数据。
- `extraction/pages.xml`：1-based OCR 像素坐标空间、实际渲染 DPI 和逐页像素宽高。
- `extraction/assets/`：按内容 hash 存放裁剪出的图片、公式和表格。
- `extraction/chapters/chapter_*.xml`：生成的章节记录及原 PDF page/bbox 映射。
- `extraction/toc.xml`、`extraction/cover.png`：可选目录和封面。

公共分段流程把 `extraction/` 打包为 `.pcex`；恢复后端只接受 `.pcex` 或已加载的
`PDFCraftExtraction`。一键转换直接使用 workspace，只有显式 `extraction_path` 时才额外导出
`.pcex`，避免压缩往返。翻译后的 `.pcex` 必须保留 manifest、pages、TOC、封面和资源。

修改 XML schema、文件命名或跳过语义会影响多个模块，应视为跨流水线变更，并配套有针对性的测试。

## 重型运行时边界

`PageExtractorNode` 会延迟导入 `doc-page-extractor`，并且只在需要 OCR 时根据 pdf-craft 的 OCR 配置创建上游 extractor。除非任务明确要求 eager loading，否则应保持这种延迟加载行为。

OCR 配置是封闭公共面：`DeepSeekOCRLocalConfig`、`DeepSeekOCR2LocalConfig`、`UnlimitedOCRLocalConfig`、`DeepSeekOCRVendorConfig`、`DeepSeekOCR2VendorConfig`、`UnlimitedOCRVendorConfig`。不要把 `doc-page-extractor` 的 `PageExtractor`、`OCRAdapter` 或 factory 作为 pdf-craft 公共注入口。

本地 OCR 可能需要 Poppler、支持 CUDA 的 PyTorch、大型模型下载和较高显存；供应商 OCR 不需要本地 CUDA，但需要网络和密钥。普通单元测试应保持不依赖这些资源也能运行。

## 错误与恢复语义

`ignore_pdf_errors` 和 `ignore_ocr_errors` 可以是布尔值或 callable。当页面级错误被忽略时，流水线会写入 fallback 页数据并继续处理。

已存在的 `page_*.xml` 会被跳过。`done` 标记会让 OCR 识别整体跳过。修改恢复行为时要谨慎，因为它同时影响本地手动运行和 VGE worktree 重跑。
