# 增量提取与翻译回调

操作仍然返回完整产物。回调只表示部分内容已经完成，不能代替整个任务成功的结果。
同步和异步回调均可使用；异步回调会被等待。回调抛错或取消任务会终止操作，最终 PCEX／EPUB
只在校验、封装成功后提交。

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
asset_hash 引用提取资源，并非内嵌图片数据。回调属于传统跨页解析阶段，早于全局目录页排除、
标题层级定稿、标点规范化和可选脚注修正，不是最终章节文件的逐节点重放。未确定归属的脚注
来源仍在正文中保留，并在页级归属闭合后按阅读顺序经 `on_body` 交付，不在后页之后作为
EOF 附录补交；必要等待与积压保存在磁盘。投影为空的 fragment 不丢失来源身份。
最终结构和资源以完整归档为准。

## EPUB 翻译

```python
from pdf_craft import EpubTranslation, SubmitKind

async def translated(item: EpubTranslation):
    print(item.epubcfi, item.original, item.translated)

await craft.translate_epub(
    "source.epub", "translated.epub",
    target_language="zh", submit=SubmitKind.REPLACE,
    llm=llm, executor=executor, on_body=translated,
)
```

`EpubTranslation` 是不可变值对象，仅有 `epubcfi`、`original`、`translated` 三个字段。
每次回调对应一个真正采纳的原子翻译片段，不等待整章，也不会重复交付请求窗口中的重叠上下文。
原译文均为纯文本；译文来自结构填充、恢复后的结果，不含双语写回模式附加的原文。
纯文本中的空白规范化、公式文本化可能与原始 DOM 的字面文本不同。

CFI 范围定位的是**输入 EPUB**，按原始 DOM 的 text/tail 位置与 UTF-16 偏移生成，支持跨行内标签。
XML 改写不会改变已经捕获的来源位置。回调覆盖 spine 正文中的内容，包括其中的标题和注释；
不包含目录、OPF 元数据或文档 head 标题。精确回调要求 spine 文档是 `application/xhtml+xml`；
`text/html` 仍可在不传回调时翻译，但请求精确回调时会明确拒绝，避免提供依赖阅读器修复行为的
错误位置。CFI 不保证继续定位输出 EPUB，因为译文写回可能改变 DOM。

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

三者的可选 `on_body` / `on_footnote` 现在接收 `ParagraphContent`，不再交付 PCEX XML：

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
`EpubTranslation` 三字段协议保持不变。

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
