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
取消、callback 自身异常、不可继续的服务错误、fallback 失败以及根本无法读写产物的错误
仍然抛出。`ErrorCallbackError` 通过 cause 保留 callback 的异常。
