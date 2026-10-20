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
来源仍在最终正文中保留，并在 EOF 时经 `on_body` 补交（保留原始来源位置）；投影为空的 fragment 不丢失来源身份。最终结构和资源以完整归档为准。

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

本次不增加 PCEX 翻译回调。PCEX 到 Markdown／EPUB／PDF 的渲染继续作为完整输出操作。
