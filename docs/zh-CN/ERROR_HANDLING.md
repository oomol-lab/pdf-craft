# 重试、降级与任务成功

`PDFCraft` 和 `AsyncPDFCraft` 使用相同策略。策略属于一次执行；不会为了覆盖配置而修改共享 Translator。

```python
from pdf_craft import PDFCraft, RetryPolicy, ErrorPolicy

craft = PDFCraft(
    retry_policy=RetryPolicy(translation=3, analysis=3, fill=3),
    error_policy=ErrorPolicy(ignore_analysis_errors=True),
)
```

三个次数都表示**首次请求之外的额外重试**，默认 3；0 表示只尝试一次，负数和非整数非法。
`analysis` 控制目录/标题层级、元信息 JSON、页结构修复和 JEV 请求；`fill` 控制 XML 结构修复。
`translation` 预留给纯文本翻译的业务校验；目前不凭空新增语义质量修复循环。LLM 的网络重试
仍由 `LLM.retry_times` 独立控制，JEV 不再有独立重试次数。元信息正常追加读页不消耗修复额度。

操作可传入 `retry_policy` 和 `error_policy` 覆盖实例配置。不传参数时继承实例，显式
`error_policy=None` 或 `False` 恢复严格模式。实例也不配置时默认严格失败。

`error_policy` 接受 `ErrorPolicy | None | bool | ErrorCallback`：

- `False`、`None`：最终失败后抛异常。
- `True`：所有可降级类别都采用 fallback。
- 同步或异步 callback：对每个最终失败返回 bool，决定是否降级；异步判断会被 await。
- `ErrorPolicy`：按类别决定，未填字段默认 False。

字段为 `ignore_analysis_errors`、`ignore_translation_errors`、`ignore_fill_errors`、
`ignore_ocr_errors`、`ignore_pdf_render_errors`、`ignore_pdf_fill_errors`。
旧的各模块 `max_retries`、JEV `retry_times`、提取忽略开关、PDF `ignore_errors`、
`ignore_translated_error` 和 `on_fill_failed` 已移除。没有单独的 `on_error`；判断 callback 同时负责感知。

```python
async def allow_partial(failure):
    print(failure.operation, failure.stage, failure.error.type, failure.error.message)
    for target in failure.targets:
        print(target.member_path, target.unit_id, target.page_indexes, target.regions)
    print(failure.fallback, failure.affected)
    return failure.category.value == "analysis"

craft = PDFCraft(error_policy=allow_partial)
```

callback 收到独立的 `FailureContext`：任务标识、操作、类别、具体步骤、可序列化的
`FailureError`、失败目标 targets、降级影响 affected 和拟执行的 fallback；可获得时附带后端、
模型、阶段编号和上下文位置。targets 是失败操作处理的对象，不是假定的网络故障坐标；
affected 描述降级的影响，可能比 targets 更广。

自然段来源包含全部跨页区域，复用 `SourceRegion`、`PageGeometry`，可用 `map_bbox` 映射到
实际显示图片尺寸。页面尚未渲染时不伪造几何。非文字来源可以用单页 page/bbox 描述位置。
EPUB 提供归档内文件、片段身份、原文和可靠时的 CFI；HTML 的 `epubcfi` 可以为 None。
元信息字段、导航条目和复用页眉模板不必对应唯一页码。错误定位不依赖用户另传 `on_body`。
整页、整批或服务级失败如实提供相应范围，不任意归咎第一个片段。

分析可退回统计、原生 metadata 或算法结果；翻译、XML fill 失败表示**没有可信译文**；
OCR 可保留页图，PDF 页渲染失败可写占位，PDF 回填失败可保留原页。
PCEX 以 coverage 的 preserved/缺失表示未翻译，并非原文充当译文。替换模式在缺失处显示原文，
双语只显示一次原文；EPUB 保留失败片段的原始 XHTML，其余片段继续写回。

生成文件不代表任务成功。`AllFallbackError` 及其 `NoUsableOCRPagesError`、
`NoUsableFillPagesError` 特例表示主要目标全部因错误降级，不能由 True 放行。
书名/metadata 成功不能掩盖正文全部未翻译；可选分析全部降级也不影响已正常完成的提取。
缓存命中、合法同文、原生图片或真正没有处理目标不是失败。
取消、callback 自身异常、不可继续的服务错误、最终保底失败以及根本无法读写产物的错误
仍然抛出。`ErrorCallbackError` 通过 cause 保留 callback 的异常。

## 从 2.5.x 升级

**2.6.0 调整了公开参数与默认错误行为，升级前需要修改旧调用。** 重试策略决定尝试次数；
错误策略决定最终失败后是否降级，两者不能互相替代。

| 已移除的参数或回调 | 替代方式 |
| --- | --- |
| `XMLTranslator(..., max_retries=N)`、`translate_epub(..., max_retries=N)` | 使用 `retry_policy=RetryPolicy(fill=max(0, N - 1))` 保持旧的总尝试次数。新默认值是额外重试 3 次，共 4 次尝试。 |
| `FootnoteRefinement(..., max_retries=N)` | 在 Craft 实例或提取调用中传入 `retry_policy=RetryPolicy(analysis=N)`。 |
| `JEV(..., retry_times=N)` | 使用 `RetryPolicy(analysis=N)`，与其他分析步骤共享配置。 |
| `ExtractionOptions(ignore_pdf_errors=...)` | 在执行的 `error_policy` 中使用 `ErrorPolicy(ignore_pdf_render_errors=...)`。 |
| `ExtractionOptions(ignore_ocr_errors=...)` | 在执行的 `error_policy` 中使用 `ErrorPolicy(ignore_ocr_errors=...)`。 |
| PDF 翻译／回填的 `ignore_errors=...` | 改为 `error_policy=ErrorPolicy(ignore_pdf_fill_errors=...)`。 |
| `XMLTranslator(..., ignore_translated_error=...)` | 显式配置 `ErrorPolicy.ignore_translation_errors` 和／或 `ignore_fill_errors`；旧参数并未保证 XML 填充失败时严格抛错。 |
| `on_fill_failed`、`FillFailedEvent` | 在 `error_policy` 或 `ErrorPolicy.ignore_fill_errors` 中传判断回调。它只接收最终失败的 `FailureContext`，必须返回 bool。 |

目录／标题推断与元信息提取不再默认静默 fallback。允许它们采用统计或原生 metadata
结果时，显式设置 `ignore_analysis_errors=True`，或通过 callback 逐次判断。同一类别也控制
JEV 评估和脚注精修失败后保留算法结果；JEV 服务失败不会被伪造成质量低分。

旧判断函数接收异常对象。新函数接收 `FailureContext`，其中的 `error` 是可序列化摘要，
不是原异常。应读取 `failure.category`、`failure.error.type`、`failure.error.code` 和来源位置，
不能继续对 payload 使用 `isinstance(failure, OCRError)`：

```python
from pdf_craft import ErrorPolicy, PDFCraft, RetryPolicy

async def allow_selected_pages(failure):
    pages = {page for target in failure.targets for page in target.page_indexes}
    return bool(pages) and 1 not in pages

craft = PDFCraft(
    retry_policy=RetryPolicy(analysis=3, fill=3),
    error_policy=ErrorPolicy(
        ignore_analysis_errors=True,
        ignore_ocr_errors=allow_selected_pages,
    ),
)
```

返回 `False` 抛异常；返回 `True` 才执行拟定的 fallback。判断函数自身异常或未返回 bool
也会使任务失败。顶层 `error_policy=True` 只放行可恢复类别，不能忽略取消、不可继续的服务
错误或任务全部降级。`on_body`、`on_footnote` 等内容回调的既有 payload 保持不变。
本次仍使用 PCEX v4，不需要迁移归档格式。

### PDF 提取的最后保底

段落分析或脚注恢复出现结构矛盾时，提取先尝试可信传统结果；仍不可用时，
以 `analysis` 类别、`chapter_recovery` 阶段、`page_image` 方案询问策略。
`targets` 是可定位的错误页，`affected` 包含跨页段落、脚注和引用牵涉的页面；
无法可靠定位时会保守扩大范围。允许 `algorithm_result` 不代表已经允许 `page_image`。
页面图片从原 PDF 重新渲染，不经过损坏的结构。如果渲染也失败，会另以
`pdf_render` 类别请求 `page_placeholder`。拒绝任一方案时异常继续传播。

这条保底同样适用于未开启 LLM 精修的提取。原 OCR 缓存和已经交付的预览不改写、
不重复通知。部分页面保留图片后其余内容可正常交付；全部主要页因错误降级时仍会失败。
取消、用户回调错误、不可继续的服务故障及最终文件写入失败不会被这条保底吞掉。


### PDF 来源几何保护

PCEX 回填在擦除前检查文本框是否大面积覆盖无关内容，包括图片及未翻译的原文。
冲突时以 `pdf_fill` 类别、`source_geometry` 阶段请求 `original_pdf_page` 方案。
目标提供冲突来源 bbox；affected 包含完整跨页自然段的页面。接受后直接保留这些
实际原 PDF 页，其余页继续处理；跳过的替换不提供虚假的译文排版预览，既有译文
缺失通知仍可返回原文和空 lines。全部主要目标因此降级时任务仍会失败。
拒绝方案或判断函数抛异常时，在发布输出前中止。
