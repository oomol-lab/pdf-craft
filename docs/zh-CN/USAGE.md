# 任务规模与资源用量

从 2.6.5 开始，可以在正式提交前查询任务规模，并独立于进度事件收集远程资源用量。

## 提前查询规模

```python
from pdf_craft import pdf_pages_count, pcex_words_count, epub_words_count

pages = pdf_pages_count('book.pdf')
words = pcex_words_count('book.pcex', with_anchored=True, with_furniture=False)
epub_words = epub_words_count('book.epub')
```

这些函数是阻塞、只读操作，不调用模型、不创建译文。PCEX 支持 `.pcex` 路径或已有
`PDFCraftExtraction` 句柄，统计原文层及书籍元信息；`with_anchored` 决定是否包含图片/表格文字，
`with_furniture` 决定是否包含独立页眉页脚等文字，复用标题不重复计数。EPUB 统计正式翻译所用的
正文、导航、文档标题及可翻译 OPF 元信息，支持与翻译一致的 `submit` 选项。

两个接口直接复用正式翻译预扫描。同一文件、同一选项下，返回值等于操作开始事件的
`STARTED.total_words`。只返回整数 words，不返回 unit 数量。需要读取解析源内容，
不是只读元数据；非法输入传播解析/校验异常。当前没有 DOCX 输入能力。
扫描 PDF 需要先提取为 PCEX，才能查询待翻译 words。

计数沿用统一的[原文 words 规则](TRANSLATION_PROGRESS.md#原文-words)：汉字一字一 word，
其他连续字母/数字按 20 字符长词上限折算，不是 token 数。上下文提示不增加原文 words。
异步应用使用 `await AsyncPDFCraft().pcex_words_count(...)` 或 `.epub_words_count(...)`，
文件操作在工作线程执行；同步 `PDFCraft` 也提供对应方法。

## 收集一次任务的资源消耗

```python
from pdf_craft import UsageCollector

usage = UsageCollector()
try:
    extraction = await craft.extract_pdf(
        'book.pdf', 'book.pcex', options, usage=usage,
    )
finally:
    report = usage.report()
    for provider, consumed in report.items():
        print(consumed.effective.input_tokens, consumed.wasted.input_tokens)
```

`craft` 是已配置的 `AsyncPDFCraft`，`options` 是 `ExtractionOptions`。
支持模型调用的 Craft 操作及高级请求/Transformer 接口都可传 `usage=`，同步门面用法一致。
保持原来的函数返回值；内部 OCR、JEV、LLM 翻译/填充、分析及重试自动继承收集器。
每个独立任务新建收集器；主动复用同一个收集器会累计多次操作。

`report()` 返回独立 `UsageReport` 快照，以调用者传入的 **provider 配置对象身份**为键：
例如 `report[llm]`、`report[jev]`、`report[ocr_config]`。值相同但对象不同也分别累计；
同一配置对象创建多个 runtime 则汇总。未使用的 provider 没有条目。不包含模型名、价格、
密钥或逐请求历史。修改快照不会改变收集器。并发任务分别传收集器，即使共用 provider 也不会串账。

操作正常返回或抛异常前，所属请求已经收尾，`finally` 可以读取最终已知消耗，包含取消、
服务致命异常和回调异常路径。执行中的快照只包含已结算消耗；尚待校验的候选暂不归类。

## 统一结构及口径

```python
RequestUsage(requests=0, input_tokens=0, cached_input_tokens=0, output_tokens=0)
Usage(effective=RequestUsage(), wasted=RequestUsage())
```

`input_tokens` 包含 `cached_input_tokens`，普通输入按两者之差计价，缓存输入单独计价。
缓存指供应商的 prompt cache，不是库内缓存。缺失/未返回的 token 字段保持 0，
不估算、不提供 unknown 字段。这里是已知消耗，不是供应商账单；失败请求也可能收费却没有 usage。

- `requests`：实际业务请求次数，包括重试及没有 token usage 的请求。等待并发槽、本地缓存命中不计。
- `effective`：通过对应业务校验并被采纳的调用。正常多轮翻译、XML 填充、元信息补读都可以有效。
- `wasted`：未采纳的调用，包括网络/空响应重试、结构/完整性校验拒绝、候选放弃及已发出的取消请求。
  HTTP 成功不直接代表有效。

两部分相加是已知总消耗。有效消耗不等于理论最低成本，例如最终采纳的修复请求可能带有反馈上下文。
调用者自行选择价格计算金额，包括输出免费等差异。

### 各 provider 的边界

LLM 读取供应商实际返回的流式输入、输出及缓存输入用量，跨协议修复、适配器校验后归类；
后续相关校验否定的候选计损耗，不重复累计。

JEV 累计响应中的输入/输出 token，没有缓存字段就为 0。合法的低概率判断被用于决定是否精修，
也是有效消耗；后续精修失败不把已采纳的判断改成损耗。

OCR vendor 统计 **识别提交次数**，不是页数或全部 HTTP 次数。DeepSeek OCR/OCR2 一页两阶段
可各计一次有效请求，重试另计。Unlimited OCR 只计提交/重新提交，不计鉴权、轮询、结果下载；
当前适配器没有 token usage，因此 token 字段为 0。OCR 响应通过布局解析校验后才采纳，
后续 JEV/LLM 精修不否定已采纳的 OCR。本地 OCR、缓存页不产生 vendor 消耗。

现有 `OCRTokensMetering` 及其返回接口保留，仍表示原有页结果 token 汇总及预算行为。
按 provider、重试损耗、供应商缓存分析请使用 `UsageCollector`，两份统计不能互相替代。
