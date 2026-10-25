# 翻译进度与原文 words

PCEX、PDF 和 EPUB 翻译通过 `on_translation_event` 报告进度。标准 `XMLTranslator`
流程在翻译前扫描实际待处理原文，`START` 提供 `total_words` 和 `completed_words=0`。
总量覆盖元数据、正文、启用的图片/表格字段和 furniture。EPUB 复用实际的目录、元数据、
文档标题与正文任务生成器预扫描，不调用模型，不保留整本书。额外成本是本地解析。

## 统一计数规则

包顶层的 `WordCounter`、`count_text_words` 与翻译进度使用同一实现，继承 Wiki Graph
的中英文混合计数约定：

- Unicode Han 一字一 word，包括扩展汉字。
- 其他连续的 Unicode 字母、数字组成一个单词。
- 空白、标点分词，不计数。
- 组合音标延续当前单词，不增加单词的长度。
- 长度为 L 的非汉字单词，计作 `ceil(L / MAX_WORD_LENGTH)` words。
  当前统一默认 **MAX_WORD_LENGTH = 20**：20 字符算 1，21 算 2，60 算 3，61 算 4。
  这是项目约定，不是语言学分词；连续假名和韩文也适用长词折算。

```python
from pdf_craft import WordCounter, count_text_words

assert count_text_words("hello world 中文") == 4
counter = WordCounter()
counter.feed("hel")
assert counter.feed("lo中文") == 3  # 累计值，与输入如何分片无关
counter.boundary()                 # 真实段落/字段边界
```

流式输入和 XML 内联标签不打断单词，真实段落/字段才结束当前词。PCEX 同一作者段落内的
来源 fragment 共享分词状态。prompt 上下文、临时公式/页码占位符和复用标题不作为新增
工作量；模型 tokenizer 和译文长度不影响统计。独立使用计数器时可以指定
`max_word_length`，翻译流程使用统一的项目默认值。

## 事件约定

| 事件 | 含义 |
| --- | --- |
| `START` | 已取得原文总量，尚未开始翻译。 |
| `ITEM_START` | TOC、METADATA、CHAPTER 或 FURNITURE 条目开始处理。 |
| `PROGRESS` | 一轮翻译已经得到最终采纳结果或允许的 fallback。 |
| `ITEM_COMPLETE` | 一个逻辑条目处理结束。 |
| `COMPLETE` | 整个翻译范围正常结束。 |

每个 `PROGRESS` 都携带整体的 `completed_words`、`total_words`，以及条目的
`item_completed_words`、`item_total_words` 和 `item_kind`、`item_id`。
数值是**累计值，不是增量**。一轮涉及多个条目时分别报告，完成数单调递增，正常结束时
达到总量。完成一轮也可能没有新增 words，例如处理先前已计数单词的后半个来源块。

PDF 的标题和 furniture 预处理包含在同一进度范围中。预翻译标题在正文或 furniture
复用时只统计一次；独立 furniture 模板按模板计数，不按打印出现次数重复计算。
Furniture 使用 `(FURNITURE, "furniture")` 条目，启用的图片/表格字段计入所属章节。
PCEX 的持久化目录属于推导/复用结果，不再单独重复统计；EPUB 导航与文档标题是独立
翻译的来源字段，需要计数。

仍在重试的失败尝试不增加完成数；最终 fallback、保留原文、跳过都会推进进度。
进度表示**已经处理的原文工作量**，不代表翻译成功率、质量、token 消耗或计费决策。
失败仍由 ErrorPolicy 和 coverage 表达。取消、致命错误及回调异常继续传播，不伪造
`COMPLETE`。失败任务可能已经报告了部分甚至全部原文的处理进度。

`on_body`、`on_footnote`、`on_metadata` 仍负责内容交付。自定义 Transformer 如果隐藏自己的
模型循环，只能在适配边界报告；每轮通知的保证适用于标准 `XMLTranslator` 引擎。
高级调用者若直接给 `XMLTranslator.translate_elements` 提供未经预扫描的动态任务流，
总量仍可能未知；高层 PCEX/PDF/EPUB 翻译会预统计。

## 不兼容变更

原来四个 `*_characters` 事件字段改为上述 `*_words` 字段；
`TranslationTask.character_count` 改为 `word_count`，高级翻译接口的
`completed_characters` / `total_characters` 参数改为 `completed_words` / `total_words`，
`NarrativeXMLTransformer.source_character_count` 改为 `source_word_count`。
不保留兼容别名，升级前需要同步调用端。
