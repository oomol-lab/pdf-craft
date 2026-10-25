# 转换流水线

**约束范围：** PDF 到输出文件的流程、中间产物和转换契约。**不约束：** 通用 setup 或打包。**何时阅读：** 修改提取、TOC、章节生成、Markdown 渲染或 EPUB 渲染时。

## 运行时流程

## 组合流程

目录检测分别保留真实 Page.index 与检测窗口内位置：前者用于来源/文件名，后者仅用于
目录位置比例及选中序列连续性。不能以 enumerate 替代页身份。排除的印刷目录页不生成
正文章节锚点。最终 heading 段内每个独立 TOC 锚点都形成章节边界，不丢弃后续 ID；
没有新增锚点的标题 fragment 和段内资产仍保留在原段。

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

1. 通过 `PDFHandler` 按业务需要读取并渲染 PDF 页面，不执行自动整份输入门禁。
   用户可在转换前主动调用独立 `assert_*` 断言，实际操作失败沿用现有错误传播与 ErrorPolicy。
2. 通过 `OCR.recognize()` 识别页面布局。
3. 在 `analysing_path/ocr/` 写入 OCR 页 XML 和恢复缓存，在
   `analysing_path/extraction/assets/` 写入资源。
4. OCR 的终态页事件经磁盘流恢复输入页序，传统跨页解析与 OCR 并行推进；闭合正文、脚注和
   未定归属来源存入磁盘数据库。可选 `on_body` / `on_footnote` 交付独立 PCEX XML 快照，
   回调背压由磁盘队列与 OCR 生产隔离。EOF 排空后再分析 TOC 数据。
5. 在 `analysing_path/extraction/` 生成章节、TOC、页面几何和 manifest；章节使用 PCEX v3
   FlowItem，保留文字 fragment、段内 anchored image/table 与独立段落公式的阅读顺序。
6. 仅从该 extraction 渲染 Markdown 或 EPUB。

章节生成在第 5 步内部先为 TOC 排除后的 OCR Page 建立完整来源记录：保留原文、原分类、
OCR 流内下标和独立阅读位置。正文沿用原 source_order；新提取的脚注使用正文布局数作为
偏移，写入同一页的 fragment 身份空间。OCR Page 的 order 不变，旧 PCEX 不重新编号。
阅读顺序保留正文已有的栏序，将脚注分类中的来源按同栏位置插入，不按全页 y 坐标排序。
通宽锚点限定阅读带；栏尾来源插在所属栏最后成员之后，不能退回整页或整带末尾。
传统 join、citation 拆分和同页 ref 匹配只建立初始结论，再投影为 PageAnalysis。
跨页合并必须把页尾/续页页首 image/table 保留在两侧文字之间，不可挪到整段之后。
PageAnalysis 的 continuation 表示文字前驱，跳过同一归属流中的图片/表格；资产自身 gap
保持 false，DisplayFormula 才是硬边界。投影与恢复必须保留段内锚点，精修与校验遵守同一规则。印刷标记的
完整来源和前缀范围保留到组装：正文投影保留前缀，定义投影才移除前缀。断词修复延后到 ownership
组装完成后的语义投影；原始文字记录不被改写，投影为空的 fragment 也保留原身份和 bbox。

默认传统算法由 `extractor/chapter/stream.py` 增量连接、来源投影和磁盘组装；无目录页排除时复用
在线生成的内容数据库，有目录页排除时从 OCR 缓存按排除规则重放。章节定稿逐章处理。
需要可编辑页级 ownership 的 PageAnalysisProcessor 仍使用 `restore_streams` 的整批事务语义；
在线回调发生在可选 refinement 之前，不声明是修正后归档的重放。
FootnoteOptions(refinement=FootnoteRefinement(...)) 在调用方 event loop 在线执行 JEV 筛选及
LLM 页级修正；CLI 的 --footnotes 只启用传统算法。LLM JSON 通过 request_guaranteed_json
编译 ownership、必需元数据、引用位置及定义关系，候选在完整暂存对象图上组装成功才接纳。
候选在暂存对象图通过校验后才提交；允许降级的页保留算法结果，必要时撤销整批精修，任何失败均不修改调用方原对象。不得额外嵌套请求重试。
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

PCEX 格式保持兼容。现有归档和译文层按原身份读取、校验和导出，不做推测重编号；
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

失败决策、可信译文缺失与任务级成功的原则见 [失败与有效成果](failure-and-completion.md)。页面级降级会保留图片或明确占位，全部主要工作降级仍须报错。

已存在的 `page_*.xml` 会被跳过。`done` 标记会让 OCR 识别整体跳过。修改恢复行为时要谨慎，因为它同时影响本地手动运行和 VGE worktree 重跑。

## EPUB 原子翻译回调

EPUB 适配器异步逐项读取任务，在共享 XMLTranslator 的局部映射采纳位置接收结果，按章写入
临时归档；不收集全书正文任务或完整译文结果。同步 iterable 的既有核心接口继续供 PCEX 翻译使用。
可选 `on_body` 接收 `EpubFragmentContent(epubcfi, original, translated)`，只包含去掉窗口上下文后的
原子片段；来源 CFI 索引由 EPUB 适配器按需构建，源范围贯穿公式替换。`pipeline/epub/cfi.py`
只负责文档内定位，OPF/spine 前缀由外层提供。用户说明见 `docs/*/STREAMING.md`。


## PCEX 内容流与页面查询

`document/content.py` 逐 XML 内容项解码现有 FlowItem/Reference，body 中引用只携带身份，完整
定义走独立条目。`document/stream_view.py` 提供固定资源/目录/页面几何与原文/所选层/在线译文流；
标准 XMLTranslator 不先生成全书译文 workspace。`transformer/content.py` 复用同一引擎，保留
资源前后文，PCEX writer 是该流的一个消费者。自定义整章 translator 保留显式兼容路径。

Markdown 逐项写暂存内容，EPUB 逐项转换并 spooling EPUB records，PDF 以来源顺序的磁盘
feed 向隔离 Qt 进程提交替换项。PDF 真实布局通过磁盘回传，跨页段落在所有区域排版定稿后
一次通知；窗口只按现有页级约束收尾，不拿内容回调冒充排版通知。

公共 `document/paragraph.py` 定义 SourceRegion、ParagraphContent、PDFSourceRegion 与
PDFParagraphContent。翻译/重排渲染使用独立 ParagraphContent（Markdown text、可选 original、
来源 regions），PDF 使用每区域带字号/行/公式/擦除的 PDFParagraphContent。提取仍保留原有
XML 回调；EPUB 输入翻译仍保留 EpubFragmentContent。未提供通道时不封装该通道的公共产物。
SourceRegion.page 复用 PageGeometry，source_order_in_page 对应 PCEX source_order；bbox
仍为 OCR 像素整数，map_bbox 根据实际页面尺寸返回 float。render_dpi 是请求值，不能单独
作为缩放依据。PDF 公共布局的坐标与字号也换算为来源 OCR 像素，不引入 font_id。

`document/access.py` 的 read_pages/read_asset 与 ExtractionContext 继续作为公共几何/资源
访问入口。内容对象包含页面几何，因此回调消费者不必再做页面联表。资源只通过受支持的
字节读取入口获取，不依赖临时目录。

LLM 有序交付使用磁盘重排缓存；异步任务上下文、来源 DOM 与完成结果都可落盘，通过
稳定 root/node identity 在当前文档交付时恢复映射，队首慢请求不再保留全书原文 DOM。
同步 iterable/自定义整章契约保留原对象身份。双阶段 vendor OCR 续阶段优先，未确定归属来源
按阅读顺序发出，延续 #794/#795 回归。


元信息通知复用 `document/metadata.py` 的 MetadataContent/DocumentMetadata。
PDF 原生-only 通知在 OCR 前，LLM 提取通知在字段合并后；异常不能被提取降级逻辑吞掉。
PCEX 的共享 NarrativeXMLTransformer 元信息阶段负责翻译通知，EPUB 适配器负责 OPF 视图通知；
书籍通知不包含 TOC 或文档 head/title。只有用户提供 on_metadata 时才构造独立公共快照。
EpubTranslation 仅作为 EpubFragmentContent 的兼容别名。


## PCEX 公式填充与恢复顺序

第一阶段自然段译文是 fill Loop 的全文基准，允许把源 LaTeX 自然表达为 `97%`、
“16倍”等文字。Loop 内的临时 `expression` 与普通 inline 标签遵守同样的结构/属性/ID
校验，里面的文字参与严格全文比较，但不与原公式内容作额外比较。完整候选通过结构及
全文校验、退出 Loop 后，ChapterFormulaInterrupter 才按身份恢复源公式；这是确定的程序
后处理，不再次比较源 LaTeX 与扁平化译文，也不发起新的 fill 请求。最终 PCEX 仍使用
原有 inline_expr 表达，不发布临时 expression/anchor 或恢复标记。图片/表格 anchor 保持
零宽结构语义，不能因公式文字可填充而放宽 anchor 或独立公式边界。

付费公式回归入口是 `python -m pdf_craft_tool.smoke.fill_formula_loop --output NEW-DIRECTORY`。
它对三个固定真实源段落重新翻译和填充，以 temperature=0.7、top_p=1.0 连续三轮全过
为门禁，不复用模型响应，不将 fallback 当成功；详细运行要求见 docs/verification/issue-821.md。

## EPUB 固定文案

`label_language` 仅选择目录、封面、前言和未命名标题等固定文案，默认英文。
精确 `zh` / `en` 使用已有文案，其他字符串统一回退英文；在调用 epub-generator 时才
映射到依赖内部的 `lan`。文案目录不是正文支持语言白名单，不能用原文或译文语言
推断并拒绝渲染，也不能因文案回退改写翻译目标、PCEX 元数据或回调内容。
