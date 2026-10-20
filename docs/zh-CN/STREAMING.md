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

PCEX 翻译写入不增加内容回调。渲染的增量内容通知见下文；最终文件仍是完整输出。


## PCEX 内容流与渲染

PCEX 的读取与可选翻译使用同一个内部内容流，包含现有正文内容项和脚注定义；
章节位置、页面几何、目录、元数据和资源属于文档上下文。流不作为公共的 push/EOF API 暴露。
`translate_extraction()` 也消费这条译文流，追加完整译文层及 coverage，保留 source。
自定义的旧式整章 task-translator 扩展继续兼容原契约；标准 `XMLTranslator` 使用增量路径。

```python
async def body(element):
    for fragment in element.iter("fragment"):
        print(fragment.get("page_index"), fragment.get("bbox"), fragment.text)

async def footnote(element):
    print(element.get("id"), element.find("mark").text)

await craft.render_markdown(extraction, "book.md", on_body=body, on_footnote=footnote)
await craft.render_epub(extraction, "book.epub", on_body=body, on_footnote=footnote)
await craft.patch_pdf_with_extraction(
    "source.pdf", extraction, "book.pdf", on_body=body, on_footnote=footnote,
)

# 边读边翻译；首个译文内容项可在后续内容尚未翻译时交付。
await craft.render_markdown(
    extraction, "translated.md", translator=translator, window=4,
    on_body=body, on_footnote=footnote,
)
```

`render_markdown` 和 `render_epub` 新增 `translator`、`window`、`with_anchored`；
`patch_pdf_with_extraction` 可传 `translator`、`window`，也可调用已有的 `translate_pdf`。
已有 PCEX 译文通过 `mode` / `translation_id` 选择；不能同时传 `translator` 和 `translation_id`。
新翻译的默认渲染为译文替换；Markdown/EPUB 也可指定双语模式。翻译中的回调始终交付纯译文
PCEX 内容，双语组合留在最终展示视图。直接渲染已有层时回调交付所选视图的内容。

三种渲染的 `on_body` / `on_footnote` 与提取回调采用同一种 PCEX XML 语义，保留来源
fragment、页码、bbox、资源和引用身份；回调获得独立快照，修改它不影响文件。
脚注是完整 `<ref><mark>…</mark><flow>…</flow></ref>` 定义，不是每个请求窗口的返回值。
回调表示选用的内容，不是 Markdown 字节、EPUB ZIP 片段或 PDF 排版后的字形坐标。
尤其 PDF 的 bbox 是原文来源区域，不保证是译文最终绘制区域。

每条回调通道内按顺序交付，两条通道之间不承诺总时序。慢回调通过磁盘缓冲与翻译并发隔离；
乱序完成的翻译也落盘，队首慢请求不限制后续空闲执行槽位。失败或取消终止操作，完整文件才是
任务成功的结果。Markdown 正文和资源先暂存，再提交，提交失败时回滚已有输出；两个路径无法
保证操作系统层面的跨路径原子可见性。EPUB 和 PDF 以完整文件替换。

## 页面几何与资源的公共访问

```python
pages = await craft.read_pages(extraction)
page = pages[3]  # 页码从 1 开始，不是 source_order
print(page.width, page.height, page.render_dpi, page.coordinate_space)
image_bytes = await craft.read_asset(extraction, asset_hash)
```

`read_pages` 返回独立的 `dict[int, PageGeometry]`，每项为不可变值对象。坐标空间为
`ocr_pixels`，bbox 直接从回调 XML 获取，页面宽高和 DPI 用于缩放或换算。
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
