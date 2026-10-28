# 增量提取与翻译回调

操作仍然返回完整产物。PDF 提取回调表示处理后页面形成的已闭合内容，其他内容回调表示部分处理已完成，不能代替整个任务成功的结果。
同步和异步回调均可使用；异步回调会被等待。回调抛错或取消任务会终止操作，最终 PCEX／EPUB
只在校验、封装成功后提交。

## 回调产物一览

| 操作 | 内容回调 | 产物 |
| --- | --- | --- |
| PDF → PCEX 提取 | `ExtractionOptions.on_body` / `on_footnote` | 逐页处理结果的独立 PCEX XML `Element` 快照 |
| PCEX 翻译、Markdown 或 EPUB 渲染 | `on_body` / `on_footnote` | `ParagraphContent[SourceRegion]`，一整个自然段 |
| PCEX → PDF 回填或翻译 | `on_body` / `on_footnote` | `PDFParagraphContent`，附最终排版的完整自然段 |
| 已有 EPUB 翻译 | `on_body` | `EpubFragmentContent`，一个原子翻译片段 |
| 书籍元信息提取或翻译 | `on_metadata` | `MetadataContent`，一次完整书籍视图 |

内容、几何与元信息值类型可从 `pdf_craft` 导入；XML 快照使用标准库
`xml.etree.ElementTree.Element`。内容回调与 `on_extraction_event`、`on_translation_event`
等进度事件分开。标准 PCEX/EPUB 翻译在操作 STARTED 前扫描总 words 与 unit 数；words 进度不代表内容回调或文件发布已同步完成。
回调 API 的首次发布见 [2.5.0 更新日志](../changelog/v2.5.0.md)，EPUB 可选定位的修复见
[2.5.1 更新日志](../changelog/v2.5.1.md)。

## PDF 提取

```python
from pdf_craft import AsyncPDFCraft, ExtractionOptions, FootnoteOptions

async def body(element):
    print(element.tag, list(element.iter("fragment")))

async def footnote(element):
    print(element.get("id"))

extraction = await AsyncPDFCraft(pdf_options).extract_pdf(
    "book.pdf", "book.pcex",
    ExtractionOptions(
        footnotes=FootnoteOptions(), on_body=body, on_footnote=footnote,
    ),
)
```

两条回调分别可选。`on_body` 收到一个已经闭合的 PCEX 阅读流节点：`<text>`、
`<standalone-asset>` 或 `<display-formula>`；`on_footnote` 收到包含 `<mark>`、`<flow>`
的完整 `<ref>` 定义。XML 语义沿用 [PCEX 公开规范](PCEX_FORMAT.md)，不暴露内部 Python 模型。
XML 树是独立的快照，用户可以持有和修改，不会影响最终文件。

fragment 保留来源页码、source_order 和像素 bbox，最终 `pages.xml` 提供页面几何信息；
asset_hash 引用提取资源，并非内嵌图片数据。

提取采用逐页管道：初级 OCR 快照不可变，当前页及存在的相邻初级输入就绪就可执行该页 JEV，
需要时立即进入该页 LLM。LLM 只看到当前页 JEV 评分，相邻页只提供初级内容，既不显示也不等待
邻页评分或精修结果。第1页OCR未完成而第2、3、4页已就绪时，第3页的JEV/LLM可以启动；
正文交付仍保持页序。跨窗口脚注身份和正文前驱在组段时绑定，不反向污染请求输入。
任务乱序完成后按输入页序增量梳理正文和脚注，必要跨页输入到齐、自然段或
定义闭合就交付；无需精修的页直接使用初级结果。两条通道分别有序、恰好一次，结束时不重复回放。

这替代 2.6.7 hotfix 暂时恢复的精修前预览：启用精修时，回调来自处理后的页面，不先发一份
初级结果再重发修正版。初步标题层级、目录页和章节组织仍可能不同于最终 PCEX；TOC 的全局
整理不要求反映到已交付自然段，也不能阻塞它们。回调不是最终章节 XML 的镜像。

生产使用有界页窗口和背压，等待输入不占供应商请求槽；已完成内容与通知积压落磁盘。
回调异常和取消终止操作，最终文件只在校验封装成功后发布，收到内容不表示任务成功。
维护时必须用屏障回归验证：后续 OCR、其他页 JEV、TOC 尚未完成时，已就绪的下游工作和内容
仍持续产生。设计动机及明确舍弃邻页评分的工程取舍见[PDF 提取流式设计](../design/pdf-extraction-streaming.md)。

### 分析缓存迁移

原始 OCR 页继续作为来源证据，沿用已有 OCR 缓存规则。目录、章节、furniture 等派生文件
从本次已采用内容重新生成；仅存在 `toc.xml` 不代表有效。旧派生产物没有输入／配置有效性
指纹，因此不作为本次分析输入。中间结果和降级语义结果不能覆盖 OCR 证据。
既有 PCEX 格式及身份不变；重新提取 PDF 时，章节 ID 根据最终采用的标题集合重新分配。

## EPUB 翻译

```python
from pdf_craft import EpubFragmentContent, SubmitKind

async def translated(item: EpubFragmentContent):
    print(item.original, item.translated)
    if item.epubcfi is not None:
        print(item.epubcfi)

await craft.translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE,
    llm=llm, executor=executor, on_body=translated,
)
```

`EpubFragmentContent` 是不可变值对象，仅有 `epubcfi`、`original`、`translated` 三个字段。
每次回调对应一个真正采纳的原子翻译片段，不等待整章，也不会重复交付请求窗口中的重叠上下文。
原译文均为纯文本；译文来自结构填充、恢复后的结果，不含双语写回模式附加的原文。
纯文本中的空白规范化、公式文本化可能与原始 DOM 的字面文本不同。

CFI 范围定位的是**输入 EPUB**，按原始 DOM 的 text/tail 位置与 UTF-16 偏移生成，支持跨行内标签。
XML 改写不会改变已经捕获的来源位置。回调覆盖 spine 正文中的内容，包括其中的标题和注释；
不包含目录、OPF 元数据或文档 head 标题。从 2.5.1 起，`epubcfi` 的类型为 `str | None`：
`application/xhtml+xml` 保留精确定位；`text/html` 仍正常交付原译文，只将 `epubcfi` 设为 `None`。
明确缺少可靠来源定位信息时同样使用 `None`，不猜测位置。混合格式 EPUB 按片段判断，
不会关闭其他文档的可靠 CFI。调用方使用定位前应判断是否为 `None`。
CFI 不保证继续定位输出 EPUB，因为译文写回可能改变 DOM。仅降级明确的定位能力缺失；
解析、翻译、来源映射/偏移损坏等意外错误及用户回调异常，仍按原规则终止操作。

| 情况 | 定位与处理结果 |
| --- | --- |
| XHTML 且来源定位可靠 | CFI 字符串；文本回调正常继续 |
| 可正常解析的 `text/html`，或明确缺少来源定位能力 | `epubcfi=None`；文本回调正常继续 |
| 真实解析/翻译失败、意外映射/偏移错误或用户回调异常 | 异常继续传播，任务失败 |

## 调度与成本

交付端按顺序等待回调；生产和交付之间使用磁盘缓冲，慢回调不会占住 OCR／LLM 请求池。
磁盘积压可以增长，回调产物不会全部留在内存。EOF 后会排空在途工作、flush 尾部内容并完成
回调交付，才返回成功。未提供对应回调时，不生成额外的用户 XML 快照／值对象；EPUB 的额外
来源记录与 CFI 索引也仅在提供 `on_body` 时启用。

PCEX 翻译与渲染的段落回调见下文；最终文件仍是完整输出。


## PCEX 内容流、翻译和渲染

PCEX 读取与可选翻译共用内部内容流。Markdown 消费端直接写暂存片段，EPUB 消费端直接
转换并落盘 EPUB 记录，PDF 消费端通过磁盘输入队列交给原有 Qt 两阶段布局。它们不必先生成
一份完整译文 PCEX 或译文章节目录。`translate_extraction` 连接同一译文流的章节写入端，
最后追加译文层与 coverage；PCEX 格式不变。旧式自定义整章 translator 继续使用明确的兼容路径。

### Markdown、EPUB 渲染及 PCEX 翻译的回调

三者的可选 `on_body` / `on_footnote` 接收 `ParagraphContent`：

```python
from pdf_craft import ParagraphContent, SourceRegion

async def paragraph(content: ParagraphContent[SourceRegion]):
    print(content.text)       # 当前内容的 Markdown；翻译时为译文
    print(content.original)   # 可选原文 Markdown，原文-only 时为 None
    for region in content.regions:
        print(region.page.page_index, region.source_order_in_page, region.bbox)
        print(region.map_bbox(target_size=(800, 1100)))

await craft.render_markdown(extraction, "book.md", on_body=paragraph, on_footnote=paragraph)
await craft.render_epub(extraction, "book.epub", translator=translator, window=4,
                        on_body=paragraph, on_footnote=paragraph)
translated = await craft.translate_extraction(extraction, "translated.pcex", translator,
                                              on_body=paragraph, on_footnote=paragraph)
```

一次回调对应一整个逻辑自然段/标题，跨 bbox、跨页均不拆开。脚注定义含多个自然段时，
每段分别触发 `on_footnote`。独立图片、表格、段公式不伪造成自然段；段内资源仍在 Markdown 中。
EPUB 回调的 Markdown 是展示支路，不参与 EPUB 生成，也不包含 CFI。EPUB **输入翻译**的
`EpubFragmentContent` 三字段协议保持不变。

`text` 从不预先拼接原译文；双语展示由使用者组合 `original` 与 `text`。最终 Markdown/EPUB
仍按 `mode` 输出所选替换或双语形式。已有译文用 `mode` / `translation_id` 选择，不能同时传
`translator` 和 `translation_id`。新翻译默认为替换。公共列表独立持有，修改不会影响输出。

### 来源区域

`SourceRegion(page: PageGeometry, source_order_in_page: int, bbox: tuple[int,int,int,int])`
统一使用 `PageGeometry` 的来源坐标空间。区域数组按自然段阅读顺序排列，空译文片段也保留。
`source_order_in_page` 对应 PCEX XML 的 `source_order`，是原页面 fragment 身份，**不是数组下标**。

bbox 为原 OCR 位图中的整数像素边界 `(left, top, right, bottom)`，原点左上，x 向右、y 向下。
`map_bbox(target_size)` 按实际页面宽高比例返回浮点坐标，不取整，也不根据 DPI 比值猜测尺寸。
`render_dpi` 是提取时请求的 DPI，图片大小限制可能降低实际 DPI；以保存的页面像素尺寸为准。
映射要求目标与来源是同一裁剪范围/方向，不自动处理旋转、裁剪或 padding。

### PDF 回填的最终布局回调

```python
from pdf_craft import PDFParagraphContent

async def pdf_paragraph(content: PDFParagraphContent):
    for region in content.regions:  # PDFSourceRegion，继承 SourceRegion
        print(region.bbox, region.font_size)
        for line in region.lines:
            print(line.text, line.x, line.baseline)
        for mask in region.erasures:
            print(mask.bbox, mask.color)
        for formula in region.formulas:
            print(formula.latex, formula.x, formula.baseline, formula.descent)

await craft.patch_pdf_with_extraction("source.pdf", extraction, "book.pdf",
    translator=translator, window=4, on_body=pdf_paragraph, on_footnote=pdf_paragraph)
```

`PDFParagraphContent` 扩展相同段落结构，`regions` 为 `PDFSourceRegion` 数组。区域数、来源身份
和顺序保持；一次仍是完整自然段，而不是一个页或一行。回调在相关页的最终 Qt 排版定稿后交付，
跨页只延后、不拆分。未使用或 preserved 的区域 `lines=[]`、`font_size=None`；ignored 失败段落不
冒充成功排版。布局来自实际 Qt 决策，前端无需重新断行。

行 `x/baseline` 是页面绝对坐标；`font_size`、公式坐标/下降量、擦除框均为**来源 OCR 像素空间中的
浮点数**，不混用 PDF point。原始 bbox 仍为整数。擦除框及 RGB 颜色来自实际背景采样，前端不能
只在原字上叠译文。公式独立保存实际向量 atom 的 `pdf` 字节和到来源像素的 `scale_x/scale_y`，
同一行在公式两侧拆成各有 x 坐标的文字片段，保留公式占据的间隔，不暴露内部占位符。前端自行选择字体；不提供 font_id 或字体分发，也不承诺与 Qt
逐字形一致。回调不是整页 PDF 或最终文件。

### 顺序、取消与成本

PCEX 翻译只有在输入成功生产完毕后，才能根据队列为空判定结束。依赖标题的 furniture
等待标题的最终结果（包括明确的 fallback）；正文和脚注各自排空待交付内容后才结束通道。
输入扫描失败或取消不算正常 EOF。任务成功包括回调交付完成和最终文件完成，静默遗漏内容
不属于合法降级。

每条通道内按顺序交付，两条通道无总时序保证。异步回调会被等待，交付通过磁盘与 OCR/LLM
执行容量隔离；已完成乱序文档连同来源上下文落盘，不在队首阻塞时持有全书原文 DOM。
没有对应回调时跳过额外 Markdown 展示、公共区域/事件对象和 PDF 布局导出。
失败或取消终止操作；回调成功不表示文件成功。Markdown 正文和资源先暂存，提交失败回滚；
多个路径无法保证操作系统层面的同时可见。EPUB/PDF 最终以完整文件替换。

## 页面几何与资源的公共访问

```python
pages = await craft.read_pages(extraction)
page = pages[3]  # 页码从 1 开始，不是 source_order
print(page.width, page.height, page.render_dpi, page.coordinate_space)
image_bytes = await craft.read_asset(extraction, asset_hash)
```

`read_pages` 返回独立的 `dict[int, PageGeometry]`，每项为不可变值对象。坐标空间为
`ocr_pixels`；提取回调从 XML 读取 bbox，渲染/翻译回调从 SourceRegion 获取。页面实际宽高用于缩放。
`read_asset` 返回资源字节副本，不暴露临时目录；hash 必须是 64 位小写 SHA-256。
同步 `PDFCraft` 提供同名同步方法。对于完整 PCEX，可在渲染前一次读取页面信息并缓存。

PDF 提取期间还没有完整 PCEX，调用者可以提供一个 `ExtractionContext`：

```python
from pdf_craft import ExtractionContext, ExtractionOptions

context = ExtractionContext()

async def body(element):
    pages = context.read_pages()  # 纯内存快照，不需要 await
    for fragment in element.iter("fragment"):
        page = pages[int(fragment.get("page_index"))]
        print(page.width, page.height, fragment.get("bbox"))
    for asset in element.iter("asset"):
        if asset.get("asset_hash"):
            image = await context.read_asset(asset.get("asset_hash"))
            # 如需长期持有，保存这些 bytes。

extraction = await craft.extract_pdf(
    "book.pdf", "book.pcex",
    ExtractionOptions(context=context, on_body=body),
)
```

已交付内容涉及的页，在回调执行前已有页面几何。context 的几何快照在操作结束后仍可读取；
资源读取仅在提取调用活跃期间有效，结束后应通过完整 PCEX 的 `craft.read_asset` 读取。
一个 context 不可被并行提取操作共享。上述访问不把未完成提取伪装成可渲染的 PCEX 句柄。


## 书籍元信息的一次性通知

`on_metadata` 接收不可变的 `MetadataContent`：`metadata` 是完整的
`DocumentMetadata`，`original` 为翻译前的完整值（提取时为 `None`）。`DocumentMetadata`
与 `DocumentAuthor` 可从 `pdf_craft` 导入；未翻译字段仍保留，不需要用户合并 overlay。

```python
from pdf_craft import ExtractionOptions, MetadataContent

async def metadata(content: MetadataContent):
    print(content.metadata.title, content.metadata.authors, content.metadata.language)
    if content.original is not None:
        print(content.original.title)

# 原文提取通知；未启用 LLM 时也可使用。
options = ExtractionOptions(on_metadata=metadata)
extraction = await craft.extract_pdf("book.pdf", "book.pcex", options)
# 译文通知；也适用于 render_epub、translate_extraction、translate_pdf，
# 以及传入 translator 的 patch_pdf_with_extraction。
await craft.render_markdown(extraction, "book.md", translator=translator,
                            on_metadata=metadata)
```

PDF 提取未启用 LLM 时，读取原生 PDF metadata 后、开始 OCR 前通知一次，并把相同值写入
manifest；启用 LLM 时等待提取完成，与原生字段 fallback 合并后再通知一次。LLM 失败允许降级时
仍通知最终 fallback，两边都没有有效数据时通知空 `DocumentMetadata()`。不论是否注册回调，
均读取并保存相同的原生元信息；只注册进度回调不会改变 manifest。仅在提供 `on_metadata` 时
构造公共快照。OCR 元信息在前部证据落盘后启动，可与后续 OCR 重叠，只通知最终合并结果。

PCEX 翻译与在线翻译渲染在共享元信息阶段通知一次，含目标语言及未翻译字段。EPUB 输入翻译
同样支持 `on_metadata`：只通知 OPF 书籍元信息，不将目录或每个文档的 head/title 当成书籍通知。
EPUB 扩展字段仍保留在输出；标准化回调对象不是完整 OPF XML 的副本。
即使 EPUB 使用双语追加模式，回调的 `metadata` 也只合并译文字段与未翻译字段，
原文单独放在 `original`；最终 EPUB 的元信息仍按所选模式写回。
一键 PDF 转换的 `ExtractionOptions.on_metadata` 对应提取阶段，顶层 `on_metadata` 对应可选翻译阶段。
指定 `extraction_path` 时，原文 PCEX 在提取完成后独立提交；后续翻译或渲染失败不会撤回它。

同步回调直接调用，异步回调会被等待；异常或取消终止操作，不发布半成品。
通知不代表最终文件已提交。不传回调就不构造公共快照。

`EpubTranslation` 保留为 `EpubFragmentContent` 的兼容别名（同一个类型）；新代码使用新名称。
这次命名调整不改变片段粒度、CFI、空白或换行处理。

翻译进度使用原文 words 累计值，见[统一计数和事件约定](TRANSLATION_PROGRESS.md)。


PCEX 翻译的 metadata/正文/脚注/资产/furniture 就绪任务共用一个 `window`；依赖标题的家具
在磁盘等待，不占执行槽。标题定稿/fallback 立即释放对应依赖；资产只等待必要原文 lookahead，
上下文最多 480 字符。正文与脚注回调各自有序，可以早于按序渲染消费；进度事件按实际执行交错。
详见[进度与调度](TRANSLATION_PROGRESS.md#pcex-调度与交付)。

## 局部完成与回填实时性（2.6.7）

PAGE 的 stage 7 / COMPLETED 表示该页 OCR、JEV、按需 LLM 精修及必要校验结束，不等 TOC、自然段组装或文件发布。操作级完成才表示产物成功；后续恢复不会重开已完成的页单元。

EPUB 的独立原子片段通过翻译与填充验收后就进入有序交付，不按整章攒回调。同一原子单位跨多个分组，或外层单位还可能失败时，必须先收齐其自身结果；不会把后续需要撤回的半份译文发给调用者。

PDF 回填由正文、脚注、家具的就绪结果共同驱动。家具按页片段/共享模板处理，一份模板结果供引用它的页面复用。Metadata 不参与排版，在最后汇合。完整自然段第一阶段跨 bbox 统一排版，关联页的所有必要内容和家具齐备后进行第二阶段页内调整；每个自然段就绪即初排一次并按页落盘，每页只等待直接覆盖它的段落与家具，不等待传递连通的整组页面。跨页自然段的全部区域定稿后才回调，最终文件再按原页序组装。正文/脚注通知各自保序，不额外施加两个通道之间的全局顺序。

Words、内容回调与文件完成是不同观察面，各自尽早报告，不追求同步。慢回调、保序等待和必要的跨页依赖仍可能造成通知间隔；不能以此增加整章/全书屏障。自定义整章 translator 的兼容路径依然遵守其批接口，不承诺标准 XMLTranslator 的增量能力。

独立 PDF 页面排版不继承其他先完成页面的字号。标题只参考当前页的正文；没有正文参考时使用配置的标题 fallback。原有按序批量排版仍保留前文正文字号规则。
