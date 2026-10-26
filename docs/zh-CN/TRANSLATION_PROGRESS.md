# 提取与翻译的统一进度事件

`ExtractionOptions.on_extraction_event` 与 `on_translation_event` 都接收 `ProgressEvent`，
支持同步或异步回调。`on_body`、`on_footnote`、`on_metadata` 继续交付内容，不代表进度。

## 三种事件，两层生命周期

`ProgressEventKind` 只有 `STARTED`、`PROGRESS`、`COMPLETED`。
`operation` 为 `OperationKind.EXTRACTION` 或 `TRANSLATION`。
`unit_id=None` 表示整个操作，整数表示某个工作单元。ID 从 0 开始，在本次操作各类型之间唯一，
随单元启动才分配，不是源页码，也不是跨运行稳定的文档身份。

操作 `STARTED` 提供**必填 `unit_counts`**，未启用的类型记 0；翻译还提供准确 `total_words`。
预扫描只累计各类型数量和 words，不创建全量 unit、ID、stage 对象、工作计划，也不保留整书文本。
运行时按需创建单元，结束后不在内存注册表中保留。跨越整批等待的页状态及乱序 EPUB 交付状态可暂存磁盘。

| 操作 | UnitKind | 计数口径 |
| --- | --- | --- |
| extraction | PAGE | 所选源页各一个，直到最终采用结果确定。 |
| extraction | TOC | 一次目录分析，包含统计分析或缓存复用。 |
| extraction | METADATA | `extract_book_metadata=True` 时一次书籍元信息提取。 |
| translation | CONTENT | PCEX 中含 narrative words 的 flow/脚注条目；EPUB 的正文任务。 |
| translation | ASSET | 适配器中每批最多四个有文字的图片/表格。 |
| translation | FURNITURE | 独立 pattern position，或同一页的普通 sections。 |
| translation | METADATA | 书籍元信息聚合为 0 或 1 个单元。 |
| translation | NAVIGATION | EPUB 导航及文档标题聚合为 0 或 1 个单元。 |

PCEX 提前翻译的标题仍是同一 CONTENT 单元，正文消费缓存或 furniture 复用时不重复计数。
PCEX 目录从标题派生，不单独翻译计数。只交付 PDF 原生元信息不创建 metadata 提取单元。
排除页不在 PAGE 总数中；所选页命中缓存仍要完成一个 PAGE。
不透明的自定义整章 Transformer 以整章为 CONTENT，只能在适配器可观察边界报告进度。

单元 `STARTED` 提供 `unit_kind`、`unit_id`、`max_stage`，翻译另提供该单元的 `total_words`。
PAGE 事件保留真实的 1-based `page_index`。
单元 `PROGRESS` 提供 `stage`，适用时提供 `unit_completed_words`；翻译还携带整体 `completed_words`。
words 是累计值，不是增量。总量取开始事件，不要求每次重复。

## 整数 stage

`max_stage` 是最终步骤的**编号**，不是耗时或百分比。stage 属于单元，不存在全局当前 stage。

| 单元 | 编号含义 |
| --- | --- |
| extraction PAGE | 0：开始/渲染；1：已渲染、OCR；2：OCR 结果落盘；3：JEV；4：LLM 精修；5：最终采用结果。`max_stage=5`。 |
| extraction TOC / METADATA | 0：分析，可包含多轮请求；1：最终接受或 fallback。`max_stage=1`。 |
| translation | 0：翻译及 XML 填充循环；1：可观察的适配器定稿；2：单元处理结束。`max_stage=2`。 |

JEV 和精修是条件步骤，缓存页可直接进入 2，无精修页可直接进入 5。跳过时跨过编号，不实例化
optional 或阶段清单。同一个 stage 可以多次更新 words，一轮没有新增 words 也允许通知。
标准 XML 翻译每轮最终采纳或最终 fallback 后更新，不把每次失败重试都计入。

OCR 完成不等于页最终提取完成。按序跨页解析与 OCR 重叠；当前 metadata、TOC 在 OCR 后执行，
页精修依赖目录和跨页分析。JEV/LLM 候选可能在全局校验中撤回，因此 PAGE 要等最终恢复、校验及
产物发布后才报告完成，避免撤回完成事件。进度系统不改变实际调度和恢复依赖。

## 完成与错误

单元 `COMPLETED.outcome` 为 `PROCESSED`、`REUSED`、`FALLBACK` 或 `SKIPPED`。
**只要整个方法继续执行并正常返回，所有已统计的 unit 都必须结清。** ignore、缓存、跳过、
保留原文、图片或占位都完成相应单元，words 也结清。重试不创建新单元，不重复累计原文。
这些是处理事实，不是质量评分或商业计费规则。

操作 `COMPLETED` 在所有单元结束且目标产物成功后发出：PDFExtractor 等 PCEX 导出成功，
翻译 facade 等目标写入成功。一键 PDF 转换在已校验的内部 workspace 边界完成 extraction，
随后可选翻译并渲染。异常、取消、致命服务错误和回调异常向外传播，不补造操作完成事件。
失败任务可能已经完成部分单元，甚至已处理全部 words。主动提前停止消费的低层内容流不报告操作完成。

不提供百分比或阶段权重。客户端可以只累计已完成数量：

```python
from collections import Counter
from pdf_craft import ProgressEventKind

completed = Counter()
expected = {}

def report(event):
    global expected
    if event.unit_id is None and event.kind == ProgressEventKind.STARTED:
        expected = dict(event.unit_counts)
        completed.clear()
    elif event.unit_id is not None and event.kind == ProgressEventKind.COMPLETED:
        completed[event.unit_kind] += 1
        print(completed[event.unit_kind], expected[event.unit_kind])
```

提取传 `ExtractionOptions(on_extraction_event=report)`，翻译传 `on_translation_event=report`。
必须区分操作和单元的完成。`OCRTokensMetering` 仍由带 metering 的接口返回；最小进度载荷不包含
逐页 token、耗时或异常详情，失败诊断使用 ErrorPolicy。

## 原文 words

包顶层 `WordCounter`、`count_text_words`、`MAX_WORD_LENGTH` 与翻译统计共用同一实现：
Han 一字一 word；其他连续 Unicode Letter/Number 按 `ceil(长度/20)` 计数；空白、标点分词但不计数；
组合附加符号延续单词，不增加长度。连续假名和韩文也使用长词规则，这不是语言学分词。
流式 chunk 和 XML inline 标签不打断单词，真实段落/字段边界才断词；上下文、临时公式标记和
复用标题不算新增工作量。统计原文，不依赖译文长度或 tokenizer。

```python
from pdf_craft import WordCounter, count_text_words
assert count_text_words('hello world 中文') == 4
counter = WordCounter()
counter.feed('hel')
assert counter.feed('lo中文') == 3
counter.boundary()
```

## 不兼容迁移

包顶层的 `OCREvent`、`OCREventKind`、`TranslationEvent`、`TranslationEventKind`、
`TranslationItemKind` 替换为 `ProgressEvent`、`ProgressEventKind`、`OperationKind`、`UnitKind`、`UnitOutcome`。
`ExtractionOptions.on_ocr_event` 改为 `on_extraction_event`。原生 OCR 事件仅作为解析和 token
计量的内部适配事实，不再是公开提取协议。

旧 `START` / `ITEM_START` 改为区分作用域的 `STARTED`，旧 `ITEM_COMPLETE` / `COMPLETE`
改为区分作用域的 `COMPLETED`。旧页失败、缓存和 ignore 事件改为完成结果；排除页不创建单元。
`item_id` 改为瞬时整数 `unit_id`，`item_completed_words` 改为 `unit_completed_words`，
条目总量放到单元开始的 `total_words`。章节数、目录存在等旧标记改为 `unit_counts`。
前一轮修改已将字符统计替换为 words。

高级调用者直接向 `XMLTranslator.translate_elements` 提供动态异步任务流且需要回调时，必须显式提供
预统计的 `unit_counts` 和 `total_words`；未知计数在模型执行前拒绝，不能用 None 或伪 0 代替。
同步任务 iterable 可以直接统计。高层 PCEX/PDF/EPUB 自动进行标量预扫描。

移除旧高级接口的 `completed_words` 偏移及 `emit_scope_events` / `emit_item_events` 开关；启用回调即使用一致的作用域生命周期。
