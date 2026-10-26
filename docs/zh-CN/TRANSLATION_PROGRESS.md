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
运行时按需创建单元，结束后不在内存注册表中保留。XML 单元在执行组开始时才创建，
不在预扫描或预取输入时分配 ID。资产按独立图片/表格计数，不随请求拆批大小改变。跨越整批等待的页状态及乱序 EPUB 交付状态可暂存磁盘。

| 操作 | UnitKind | 计数口径 |
| --- | --- | --- |
| extraction | PAGE | 所选源页各一个，直到最终采用结果确定。 |
| extraction | TOC | 一次目录分析，包含统计分析或缓存复用。 |
| extraction | METADATA | 一次书籍元信息提取：原生 PDF 字段，或 OCR 证据加原生 fallback。 |
| translation | CONTENT | PCEX 中含 narrative words 的 flow/脚注条目；EPUB 的正文任务。 |
| translation | ASSET | 独立的图片或表格，包含该资产的 title/content/caption 字段。 |
| translation | FURNITURE | 独立 pattern position，或同一页的普通 sections。 |
| translation | METADATA | 书籍元信息聚合为 0 或 1 个单元。 |
| translation | NAVIGATION | EPUB 导航及文档标题聚合为 0 或 1 个单元。 |

PCEX 标题是普通 CONTENT 单元；定稿立即释放对应 furniture 依赖，复用不重复计 unit、words 或 LLM 请求。
PCEX 目录从标题派生，不单独翻译计数。EPUB XHTML 的 head/title 始终属于 NAVIGATION，
即使没有目录或 OPF 元信息；只有 OPF 书籍字段归入 METADATA。原生 PDF 元信息也在 OCR 前创建并完成 METADATA 单元；空结果或允许的 fallback 同样结清。
排除页不在 PAGE 总数中；所选页命中缓存仍要完成一个 PAGE。
不透明的自定义整章 Transformer 以整章为 CONTENT，只能在适配器可观察边界报告进度。
不透明的整书批处理无法透露内部启动时机，在逐项采纳返回章节时才创建并完成其单元，不提前分配全书 ID。

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

OCR 完成不等于页最终提取完成。按序跨页解析与 OCR 重叠；OCR metadata 在前 3 个输入页正式落盘后启动，不足 3 页时使用实际输入；补读只等待所需页，最多 12 页，完整/部分缓存沿用同一规则。TOC 仍在 OCR 后执行，
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
公开 XML task 在实际消费后若没有 segment，仍计一个单元，以 SKIPPED 结束，不请求模型。
自定义 source projection 移除所有片段也遵守此规则，并结清其声明的原文 words。

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

高级调用者直接向 `XMLTranslator.translate_elements` 提供一次性 iterator 或异步任务流且需要回调时，必须显式提供
预统计的 `unit_counts` 和 `total_words`；未知计数在模型执行前拒绝，不能用 None 或伪 0 代替。
可重复遍历的同步 iterable 可以直接统计，不额外收集任务列表。高层 PCEX/PDF/EPUB 自动进行标量预扫描。

移除旧高级接口的 `completed_words` 偏移及 `emit_scope_events` / `emit_item_events` 开关；启用回调即使用一致的作用域生命周期。


## PCEX 调度与交付

metadata、正文、脚注、独立图片/表格文字与 furniture 共用一个 PCEX `window`，限制活跃业务任务。
每个任务内部 XML group 的窗口为 1；所有请求仍使用原 translator 的共享 executor 和限流，
不会因增加流复制容量或让内外窗口相乘。`window=1` 时活跃工作自然串行。

扫描把原文任务、等待标题的依赖与乱序结果写入临时 SQLite；只在实际执行时创建 unit/整数 ID。
就绪任务按通道轮转入场；pending 不占窗口，也不挡住后面的 ready。标题只翻译一次，定稿后
立即释放对应家具；fallback/无可信译文也会释放并保留原文，输入结束后未知标题同样结清。
复用没有额外 LLM 请求、unit 或 words，保留的原文不能冒充可信译文。

资产继续携带原文上下文：段内资产使用最近标题和所在段落，独立资产使用前后最近正文，
最多 480 字符。只等必要原文 lookahead，不等正文译文；上下文不计 words、不写回产物。
脚注定义仍走 narrative 翻译，本次没有新增脚注内资产翻译能力。

进度按实际执行交错；`on_body`、`on_footnote` 各自在通道内按原文顺序，通过独立磁盘交付，
不受另一通道或按序章节写入的慢队首约束。慢回调/乱序结果不占执行槽，回调异常仍终止并收敛并行任务。
渲染器可以先等待最终 metadata/furniture，再消费已落盘的正文。整体完成仍要求完整消费及成功发布；
提前关闭流不能伪造整体完成。EPUB 输入保留原统一任务流及 `EpubFragmentContent` 阅读顺序。
翻译 stage 0 有意涵盖翻译与 XML 填充/校验多轮调用，不逐次区分两类请求，也不代表全书串行阶段。
