# 转换流水线

**约束范围：** PDF 到输出文件的流程、中间产物和转换契约。**不约束：** 通用 setup 或打包。**何时阅读：** 修改提取、TOC、章节生成、Markdown 渲染或 EPUB 渲染时。

## 运行时流程

## 组合流程

Extractor 生成 PDFCraftExtraction 后，Renderer 可直接生成 Markdown 或 EPUB。可选 Transformer
可以在渲染前修改结构化文本；`pipeline/epub` 也可把既有 EPUB 的 XHTML、目录和元数据交给
同一个 XML Transformer。PDF Translation Pipeline 只处理已记录来源 bbox 内的文本。

PCEX 翻译不再覆盖 source：`translate_extraction()` 在 v4 `translations/` 下追加一个完整的
replacement-only 层。Markdown/EPUB 随后选择 source、某一译文替换或 source 加某一译文；选择
依赖 translation ID，未指定时使用 index 第一项。这个渲染选择与直接 EPUB 输入的 append/replace
提交模式是不同阶段的概念。一键 PDF→Markdown/EPUB 也复用 replacement-only 译文层，显式
选择本次新生成的 ID；REPLACE / APPEND_TEXT / APPEND_BLOCK 分别映射为 REPLACE /
BILINGUAL_TEXT / BILINGUAL 渲染。只有临时展示视图组合原译文，不把重复来源写入待校验
PCEX。BILINGUAL_TEXT 在同一来源 fragment 内追加内容；BILINGUAL 按正文段追加译文。
资产先按稳定 flow/child 索引组合，再插入双语段落；表格在单元格内组合，不能追加第二张表。PDF Translation Pipeline 也可通过显式 translation ID 选择持久化译文；
省略 ID 时继续消费根内容。PDF 写回只处理 narrative 与 furniture，不处理缺少字段级来源坐标的
anchored 图片/表格文字。

`AsyncPDFCraft.convert_pdf_to_markdown()` 和 `AsyncPDFCraft.convert_pdf_to_epub()` 会先提取到内部 workspace，
再渲染目标输出。提取流程是：

1. 通过 `PDFHandler` 渲染 PDF 页面。
2. 通过 `OCR.recognize()` 识别页面布局。
3. 在 `analysing_path/ocr/` 写入 OCR 页 XML 和恢复缓存，在
   `analysing_path/extraction/assets/` 写入资源。
4. 分析 TOC 数据。
5. 在 `analysing_path/extraction/` 生成章节、TOC、页面几何和 manifest；章节使用 PCEX v3
   FlowItem，保留文字 fragment、段内 anchored image/table 与独立段落公式的阅读顺序。
6. 仅从该 extraction 渲染 Markdown 或 EPUB。

章节生成在第 5 步内部先为 TOC 排除后的 OCR Page 建立完整来源记录：保留原文、原分类、
OCR 流内下标和独立阅读位置。正文沿用原 source_order；新提取的脚注使用正文布局数作为
偏移，写入同一页的 fragment 身份空间。OCR Page 的 order 不变，旧 PCEX 不重新编号。
阅读顺序保留正文已有的栏序，将脚注分类中的来源按同栏位置插入，不按全页 y 坐标排序。
通宽锚点限定阅读带；栏尾来源插在所属栏最后成员之后，不能退回整页或整带末尾。
传统 join、citation 拆分和同页 ref 匹配只建立初始结论，再投影为 PageAnalysis。印刷标记的
完整来源和前缀范围保留到组装：正文投影保留前缀，定义投影才移除前缀。断词修复延后到 ownership
组装完成后的语义投影；原始文字记录不被改写，投影为空的 fragment 也保留原身份和 bbox。

传统算法和可选 PageAnalysisProcessor 最终共用 restore_streams 业务组装与校验。
FootnoteOptions(refinement=FootnoteRefinement(...)) 在调用方 event loop 在线执行 JEV 筛选及
LLM 页级修正；CLI 的 --footnotes 只启用传统算法。LLM JSON 通过 request_guaranteed_json
编译 ownership、必需元数据、引用位置及定义关系，候选在完整暂存对象图上组装成功才接纳。
所有选中页成功后才返回整批结果；任何失败均不修改调用方原对象。没有额外异常吞咽或本地重试。
ref 仍以 layout_id + citation_index + before/mark/after 定位，不要求 LLM 算 offset。

连接的唯一编辑真源为后继的 continues_from_previous；continues_to_next 由此派生，旧字段若
显式返回，仅作为同页一致性断言。没有按修正先后顺序选择布尔值的 reconciliation。
已知跨页定义 ID、起点索引与既有跨页片段不可由单页请求删除或重命名。保护同时覆盖 segment
记录与既有 layout 成员，不能保留 ID 却将成员改为 ignored、正文或其他定义；内部自然段边界仍可编辑。页内非共享定义可以
通过重分组拆分或合并，新增 ID 为 new-页码-index；未确定归属的续段可明确连接到上一页已知
定义。无索引段排在本页有索引段前，有索引定义从 1 连续递增。没有起点的已知 continuation
是非法对象图；unresolved 来源则显式保留为可读正文，不能猜测归属后丢弃。

一条定义可以有零个或多个引用出现；数字、圈号及星号都属于印刷标记域，标记不是定义身份。
未匹配定义由 Chapter.definitions 保留，经既有 PCEX references 容器编码，无需制造正文 ref。
Markdown/EPUB 保留这些定义。正文及脚注 TextFlowItem 共用 narrative_texts 遍历，翻译 coverage
和 PDF 计划消费同一来源映射；有 coverage 的脚注正文实际回填，无 coverage 的区域仍作障碍。
多次引用或跨章节复用同一来源不重复回填。定义开头的印刷标记在 PDF 输出时恢复。
格式专有适配留在 PCEX 层，通用 XML translator 无需了解脚注。

PCEX 格式与公共配置 API 不变。现有归档和译文层按原身份读取、校验和导出，不做推测重编号；
缺失 coverage 继续表示 preserved。旧归档若同一章节已有歧义的重复 fragment 身份，仍明确拒绝，
不声称能够无损迁移。更详细的修复前后证据见 docs/verification/footnote-788.md。

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
- `extraction/translations/index.json`：v4 可选译文索引；条目以 ID 区分，记录目标语言和创建时间。
- `extraction/translations/<id>/chapters/`、`metadata.json`、`coverage.xml`：一份独立译文层；章节
  结构与 source 对齐，coverage 记录 Narrative、furniture position/section 及独立图片/表格文本的
  `translated` / `preserved` 状态。可选 `furnitures.xml` 保存该层的 furniture 译文。
- `extraction/translation.xml`：旧式或内部临时物化译文视图的根级 coverage；不要把它当作 v4
  translation layer 索引。

Narrative 翻译中的段内 asset 只以无文本临时 anchor 保持位置；独立 asset 不伪造 anchor。图片/
表格 title/content/caption 属于独立翻译阶段；标准高层翻译入口固定先运行 NarrativeFlow，随后由
`with_anchored` 决定是否运行该阶段，并以 anchored coverage 约束 v4 translation layer 中允许变化的 slot。

公共分段流程把 `extraction/` 打包为 `.pcex`；恢复后端只接受 `.pcex` 或已加载的
`PDFCraftExtraction`。一键转换直接使用 workspace，只有显式 `extraction_path` 时才额外导出
`.pcex`，避免压缩往返。翻译后的 `.pcex` 必须保留 source 的 manifest、pages、章节、TOC、封面、
furniture 和资源，并把每份译文及其 coverage 隔离在对应 translation layer 中。
`PDFCraftExtraction` 只作为 opaque handle 流转；归档的打开、校验和导出由 `AsyncPDFCraft`
负责，`PDFCraft` 仅同步转发这些操作。

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
