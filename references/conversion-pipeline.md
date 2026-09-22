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
5. 在 `analysing_path/extraction/` 生成章节、TOC、页面几何和 manifest；章节使用 PCEX v3
   FlowItem，保留文字 fragment、段内 anchored image/table 与独立段落公式的阅读顺序。
6. 仅从该 extraction 渲染 Markdown 或 EPUB。

章节生成在第 5 步内部以 TOC 排除后的 OCR `Page` 为起点：传统 join、citation 拆分与同页
ref 匹配先产出既有的正文/citation 结果，再投影为内存中的 `PageAnalysis`，随后恢复成相同的
两条逻辑流，最后才进入 FlowItem 组装。`PageAnalysis` 是 analysis-only 的可逆页级边界，不是
OCR `Page`、PCEX schema 或持久化缓存；未安装后续审查器时，这次投影与恢复必须是行为无效操作。
它会保留传统 citation 拆分未采用的页脚前缀，但无修改恢复时仍按旧行为忽略；跨页 citation
使用稳定内部身份连接各页片段，只有 citation 在本页开始时才具有页内索引，续页片段的索引为
空。每页无索引 citation 必须排在有索引 citation 之前，有索引部分必须从 1 连续递增。
所有 layout 共用一套文档顺序，使 ownership 修改后仍能确定地恢复到目标逻辑流。

当未传入 `analysing_path` 时，`EnsureFolder` 会创建临时目录。当传入该路径时，它会成为可持久复用的缓存和调试输出目录。

## 中间产物契约

analysis 与稳定 extraction 明确分离：

- `ocr/page_*.xml`：OCR 页数据。
- `ocr/done`：表示所有选中页面已完成识别的标记。
- `plots/`：启用 plot 生成时的可选可视化调试输出。
- `extraction/manifest.json`：格式版本、producer、创建时间和文档元数据。
- `extraction/pages.xml`：1-based OCR 像素坐标空间、实际渲染 DPI 和逐页像素宽高。
- `extraction/assets/`：按内容 hash 存放裁剪出的图片、公式和表格。
- `extraction/chapters/chapter_*.xml`：生成的 v3 FlowItem 章节记录及原 PDF page/bbox 映射；
  图表可嵌在 TextFlowItem fragment 之间，DisplayFormula 必须独立且不能被段落拼接跨越。
- `extraction/toc.xml`、`extraction/cover.png`：可选目录和封面。
- `extraction/furnitures.xml`：可选的页面家具 pattern 与页级 section。
- `extraction/translation.xml`：可选翻译覆盖记录；包含 Narrative、furniture position/section，
  以及独立图片/表格文本 asset 的 `translated` / `preserved` 状态。Narrative 翻译中的段内
  段内 asset 只以无文本临时 anchor 保持位置；独立 asset 不伪造 anchor。两者的
  title/content/caption 均由独立步骤翻译。

公共分段流程把 `extraction/` 打包为 `.pcex`；恢复后端只接受 `.pcex` 或已加载的
`PDFCraftExtraction`。一键转换直接使用 workspace，只有显式 `extraction_path` 时才额外导出
`.pcex`，避免压缩往返。翻译后的 `.pcex` 必须保留 manifest、pages、TOC、封面、furniture、
覆盖记录和资源。

修改 XML schema、文件命名或跳过语义会影响多个模块，应视为跨流水线变更，并配套有针对性的测试。

Markdown/EPUB 只从 FlowItem 的阅读顺序渲染，不能也不应复刻固定 PDF 页面几何：Markdown 将
段内图片/表格安全拆成块级内容；EPUB 默认同样处理，仅在小型图片的相邻 fragment 提供同页、
另一侧、纵向重叠等强几何证据时尝试可降级的左右 float。表格和 DisplayFormula 不得 float；
阅读器不支持 CSS 或窄屏时仍必须保留正常块级顺序。

## 重型运行时边界

`PageExtractorNode` 会延迟导入 `doc-page-extractor`，并且只在需要 OCR 时根据 pdf-craft 的 OCR 配置创建上游 extractor。除非任务明确要求 eager loading，否则应保持这种延迟加载行为。

OCR 配置是封闭公共面：`DeepSeekOCRLocalConfig`、`DeepSeekOCR2LocalConfig`、`UnlimitedOCRLocalConfig`、`DeepSeekOCRVendorConfig`、`DeepSeekOCR2VendorConfig`、`UnlimitedOCRVendorConfig`。不要把 `doc-page-extractor` 的 `PageExtractor`、`OCRAdapter` 或 factory 作为 pdf-craft 公共注入口。

本地 OCR 可能需要 Poppler、支持 CUDA 的 PyTorch、大型模型下载和较高显存；供应商 OCR 不需要本地 CUDA，但需要网络和密钥。普通单元测试应保持不依赖这些资源也能运行。

## 错误与恢复语义

`ignore_pdf_errors` 和 `ignore_ocr_errors` 可以是布尔值或 callable。当页面级错误被忽略时，流水线会写入 fallback 页数据并继续处理。

已存在的 `page_*.xml` 会被跳过。`done` 标记会让 OCR 识别整体跳过。修改恢复行为时要谨慎，因为它同时影响本地手动运行和 VGE worktree 重跑。
