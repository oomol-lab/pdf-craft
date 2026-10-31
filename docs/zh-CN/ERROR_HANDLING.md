# 重试、降级与任务成功

## PDF 目录与章节恢复

先识别印刷目录页，再分析段落。传统／精修内容被采用后，才从最终标题集合分配章节锚点和 ID；
目录与章节使用同一份已采用标题。删除、降为正文或合并标题不是目录失败，不需要为此撤回精修。
旧派生目录直接重算，不靠保留失效 ID 来修补。既有 PCEX 的 ID 不变，这些规则只影响重新提取。

真正的结构错误仍按错误策略处理。获准的页面图片恢复先改变已采用内容，再生成目录；
全部主要页降级仍失败。最终校验继续拒绝章节、引用和资源不一致，不会跳过校验冒充成功。

## XML 填充文本与格式槽

EPUB 与 PCEX 共用友好 XML 填充协议。文本中的 `<`、`>`、`&` 分别写成
`&lt;`、`&gt;`、`&amp;`。先识别结构标签，再对文本做一次实体解码；原文如果故意包含
字面 `&lt;span&gt;`，则应传入 `&amp;lt;span&amp;gt;`。支持 XML 命名及数字实体，
未知或无效引用保留为原样文本。这是容错填充协议，不是通用 XML 解析器；不支持用 CDATA 转义文本。

译文较短时，仍保留必要格式标签和 ID，允许没有对应译文字元的行内格式槽为空。
不能为填满槽位而补入原文、占位文字或重复译文。PCEX 的严格全文与结构校验保持不变，
公式内容仍在填充循环接受后恢复。通用 XHTML 填充不附加 PCEX 专用的全文约束指令。

## 可选输入断言

转换不再自动执行输入门禁。输入有问题时，在实际操作遇到问题的位置报错；已有
ErrorPolicy 对受支持页面错误的处理保持不变。应用如果希望提前检查，可以主动调用
从 `pdf_craft` 顶层导出的阻塞函数：

| 函数 | 验证的操作 |
| --- | --- |
| `assert_pdf_readable(pdf_path)` | Poppler 能打开文件并获取有效的正数页数。 |
| `assert_pdf_renderable(pdf_path)` | Poppler 能以 72 DPI 逐页完成整份 PDF 的光栅化。 |
| `assert_pdf_editable(pdf_path)` | pypdf 能读取、克隆、试写并重新打开，具备基本 PDF 编辑能力。 |
| `assert_epub_readable(epub_path)` | 复用 EPUB 翻译的输入解析器，读取 ZIP、container/OPF、目录、元数据，并完整遍历解析 spine 正文。 |

2.6.3 起，原有自动输入门禁及 `PDFPreflightError` 均已移除。请删除旧的导入和捕获
分支；需要提前检查的应用应主动调用所需断言，并处理原后端或解析器异常。

按应用需要选择调用，无需全部执行。成功返回 `None`，失败直接抛出原后端或解析器
异常并保留 traceback，不返回 bool，不提供专用错误分类。断言不修改或修复输入，
成功和失败都会清理临时试写文件。两个 Poppler 断言可用关键字参数
`poppler_path=directory` 指定工具目录。

```python
from pdf_craft import assert_epub_readable

try:
    assert_epub_readable("input.epub")
except Exception as error:
    print(f"输入检查未通过：{error}")
else:
    print("输入可以完整解析")
```

通过仅表示表中对应操作走通：基本可读不保证完整 PDF → PCEX，低分辨率渲染不保证
高分辨率渲染或 OCR，基本编辑不保证所有 PDF 回填操作。EPUB 断言检查的是当前输入
解析链路的兼容性，不是完整 EPUB 标准合规校验，也不保证后续翻译成功。断言不运行
OCR、下载模型或调用 LLM。转换入口与页数查询不会自动调用这些断言。


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

## PDF 提取的最后保底

自 2.6.1 起提供。

段落分析或脚注恢复出现结构矛盾时，提取先尝试可信传统结果；仍不可用时，
以 `analysis` 类别、`chapter_recovery` 阶段、`page_image` 方案询问策略。
`targets` 是可定位的错误页，`affected` 包含跨页段落、脚注和引用牵涉的页面；
无法可靠定位时会保守扩大范围。允许 `algorithm_result` 不代表已经允许 `page_image`。
页面图片从原 PDF 重新渲染，不经过损坏的结构。如果渲染也失败，会另以
`pdf_render` 类别请求 `page_placeholder`。拒绝任一方案时异常继续传播。

这条保底同样适用于未开启 LLM 精修的提取。原 OCR 缓存和已经交付的预览不改写、
不重复通知。部分页面保留图片后其余内容可正常交付；全部主要页因错误降级时仍会失败。
取消、用户回调错误、不可继续的服务故障及最终文件写入失败不会被这条保底吞掉。

## PDF 来源框重叠

来源框重叠本身不再让整页或关联自然段跳过回填。2.6.1 引入的面积比例门禁已取消，
不再产生 `pdf_fill/source_geometry/original_pdf_page` 通知，已有可信译文继续进入
正常排版与合成，既有排版障碍约束仍生效。擦除按来源矩形执行，因此不准确的大框仍
可能覆盖图片或无关文字；取消门禁不等于修复这些框。实际替换校验、排版及合成失败
仍遵循错误策略，全部主要回填目标失败时任务仍会失败。

## 从 2.5.x 升级

**2.6.0 调整了公开参数与默认错误行为，升级前需要修改旧调用。** 重试策略决定尝试次数；
错误策略决定最终失败后是否降级，两者不能互相替代。

| 已移除的参数或回调 | 替代方式 |
| --- | --- |
| `XMLTranslator(..., max_retries=N)`、`translate_epub(..., max_retries=N)` | 使用 `retry_policy=RetryPolicy(fill=max(0, N - 1))` 保持旧的总尝试次数。新默认值是额外重试 3 次，共 4 次尝试。 |
| `FootnoteRefinement(..., max_retries=N)` | 迁移为 `Refinement(...)`，并在 Craft 实例或提取调用中传入 `retry_policy=RetryPolicy(analysis=N)`。 |
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

暂时限流不消耗上述重试次数，由 executor 的 `RateLimitScope` 统一冷却与恢复。
超过每请求累计等待预算时抛出 `RateLimitTimeoutError`，原因链保留原限流错误；最终按对应业务
ErrorPolicy 决定是否降级。详见 [共享限流范围](API_REFERENCE.md#共享限流范围)。

## 可恢复的模型格式差异

XML 填充会先依据已知译文恢复无歧义的空白，再采纳结果。可修复的表面格式不应触发额外
模型请求；有歧义的归属、文字变化及受保护空白继续校验。详见
[LLM 验收原则与空白处理](LLM_ACCEPTANCE.md)，包含共同规则、HTML/CSS 标准及支持范围。


`FailureError.frames` 提供可序列化的文件名、函数名和可选行号，按外层异常、cause（无 cause 时为未抑制的 context）顺序展开，遇到循环停止。不包含源码行或局部变量；文件名可能是绝对路径。

Vendor OCR HTTP 失败通过 `FailureError.http_status`、`provider_code`、`provider_type`
和 `request_id` 独立暴露机器可读原因，来自响应 JSON 的 `error.code`/`error.type`
与响应头；缺失或类型不符时为 `None`。`image_url_unsafe` 是供应商错误码，并非库枚举。
`exception_chain` 保留各层异常的独立栈。异常消息与 `causes` 可能包含私有输入，
调用方写日志或分析事件前需采用适当脱敏策略。这些字段不改变降级、重试与可用性规则。


## LLM 完整性修复耗尽的现场记录

通过仅接受关键字的 `LLM(..., failure_log_path="analysis/llm-failures")`
启用记录；默认 `None` 关闭。它独立于响应缓存和普通 `log_dir_path` 事件，
不依赖 `analysing_path`，同步/异步 Craft 门面共用实现。仅完整性约束的修复预算
耗尽才保留本轮现场，不记录全部成功调用。

异常与 `FailureContext.error.failure_log_id` 指向
`<failure_log_path>/<failure_log_id>/`。`summary.json` 保存阶段、模型、可获得的
目标单元、次数、最终原因和 `complete` 标志；`attempts.jsonl` 按轮保存实际
messages、实际生效参数、完整响应和校验反馈。`transports.jsonl` 另记底层传输
请求/响应（包括传输重试）与可获得的供应商请求 ID。loop ID 不覆盖 `request_id`。

首次或修复后成功会删除暂存；上层允许降级并最终成功时，已耗尽 loop 的目录和 ID
仍保留，可放入诊断包。取消或普通传输失败会清理暂存，不伪装成完整性耗尽。
日志逐轮落盘，内存只持有当前轮。传输事件在请求结束并释放供应商容量后按序写盘，
取证写盘耗时不计入 LLM 传输总超时预算；取消会等待已开始的写盘结束再清理暂存。
写盘故障通过 `pdf_craft.llm.failure_log`
warning 报告，不替换原业务结果。保留记录可能 `complete=false`；发布失败时可能
没有 ID。API Key、Authorization 值和带凭证 URL 会脱敏，但其余文档上下文仍属
私有数据，宿主应控制访问、归档和保留期限。
