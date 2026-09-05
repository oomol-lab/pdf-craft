# 故障排查指南

本文面向已经安装 pdf-craft、准备处理实际文件的用户。排查时建议先确认问题发生在
哪一层：PDF 读取、OCR 识别、文本 LLM、渲染输出，还是 PDF/EPUB 翻译。

## 先做三项检查

1. 确认输入文件确实存在，并且当前进程对它有读取权限。
2. 确认系统可以找到 Poppler。pdf-craft 通过它把 PDF 页面渲染成 OCR 所需的图像。
3. 确认你选择的 OCR 运行位置与设备匹配：local OCR 需要支持 CUDA 的 NVIDIA GPU，且需要
   安装 `pdf-craft[local]` 可选依赖；vendor OCR 需要网络、服务地址和凭据。

如果问题只发生在翻译流程，再单独检查文本 LLM 配置。OCR 服务和翻译 LLM 是两套独立配置，
OCR 请求成功并不代表翻译 LLM 已经配置正确。

## 问题定位表

| 现象 | 优先检查 |
| --- | --- |
| `Poppler not found in PATH` | Poppler 是否安装、命令是否在 PATH 中 |
| local OCR 报 CUDA 不可用 | PyTorch 是否为 CUDA 版本、驱动和 GPU 是否可见 |
| local OCR 提示缺少运行时 | 是否安装了 `pdf-craft[local]` |
| local OCR 报找不到模型 | 模型缓存路径、`local_only` 和模型是否已下载 |
| vendor OCR 请求失败 | endpoint、模型名、API key、网络和供应商配额 |
| EPUB/PDF 翻译时报 LLM 错误 | 文本 LLM 的 URL、模型、密钥和 token 编码 |
| 输出文件没有生成 | 输出目录权限、输入文件类型和前一步是否已经失败 |
| PDF 写回时报 extraction 或页面错误 | 原始 PDF 是否与 `.pcex` 匹配、`pages.xml` 是否完整 |

## PDF 和 Poppler 问题

### `Poppler not found in PATH`

pdf-craft 需要 Poppler 将 PDF 页面渲染为图像。这个错误与 OCR backend 无关，在切换
local/vendor OCR 之前都必须先解决。

先在系统终端确认 `pdfinfo` 可执行，并检查它输出的版本信息。如果命令不存在，将 Poppler
安装目录加入 PATH 后重新启动终端或 Python 进程。也要确认传入的是一个有效的 PDF，而不是
改了扩展名的其他文件。

### PDF 无法读取或只有部分页面失败

先用其他 PDF 阅读器打开输入文件，确认文件没有损坏，并确认当前用户有读取权限。密码保护、
损坏的交叉引用表、异常页面尺寸或极端复杂的 PDF 结构，都可能在页面渲染阶段失败。

pdf-craft 的提取选项支持 `ignore_pdf_errors`。设为 `True` 可以让单页错误不立即终止整个
流程；如果只想忽略特定错误，可以传入接收 `PDFError` 并返回布尔值的判断函数。忽略错误会
留下缺失或 fallback 页面，适合抢救长文档，不代表原始页面已经被正确识别。

OCR 也有对应的 `ignore_ocr_errors` 选项，既可以设为 `True`，也可以传入接收 `OCRError`
的判断函数。它只控制遇到识别错误时是否继续处理后续页面；被跳过的页面仍可能没有可用文字，
因此应在输出中检查这些页面，而不是把“流程完成”当成“每页都识别成功”。

## local OCR 问题

### 提示缺少 local OCR 运行时

local backend 的依赖没有随基础安装自动安装。若异常明确要求安装 `pdf-craft[local]`，说明
运行时包尚未安装；请在当前 Poetry 或虚拟环境中安装这个可选依赖，然后重新运行。安装了
该依赖但仍然失败，再继续检查 CUDA，而不要把两类问题混为一谈。

### CUDA 不可用

local OCR 直接使用本机 GPU。遇到 CUDA 错误时按以下顺序检查：

1. 运行 `nvidia-smi`，确认驱动能看到 NVIDIA GPU。
2. 在当前 Python 环境中检查 `torch.cuda.is_available()`。
3. 确认安装的是支持 CUDA 的 PyTorch，而不是 CPU-only wheel。
4. 确认当前用户、容器或远程会话确实能看到该 GPU。

如果设备没有可用 CUDA，切换到对应的 vendor OCR 配置。vendor OCR 会调用远程计算资源，
不需要在本机安装 local OCR 运行时。

### 显存不足或进程被系统终止

模型大小、页面分辨率、`ocr_size` 和同时启用的 GPU 任务都会影响显存。可以先处理少量页面
确认流程，再降低 `ocr_size` 或减少并发任务。不要把“显存不足”误判为 PDF 文件损坏；先用
vendor OCR 做一次对照运行，也能帮助区分设备问题和输入问题。

### 模型下载失败或找不到本地模型

local OCR 首次运行可能需要从 Hugging Face 下载模型。检查：

- `models_cache_path` 指向的目录是否可写；
- 当前进程是否能访问模型下载地址；
- 磁盘空间是否足够；
- `local_only=True` 是否被错误地用于尚未下载模型的环境。

`local_only=True` 会禁止缺失模型触发网络下载，因此只能用于模型已经完整放入缓存的环境。
如果下载在中途失败，清理对应的不完整模型目录后重新下载，或改用一个新的缓存目录。

### OCR preset 不适用于当前 backend

`ocr_size` 的默认值是 `gundam`，但可用值由 backend 决定，不要假设所有模型都支持同一组
preset：

- Unlimited OCR local 支持 `base` 和 `gundam`；
- DeepSeek OCR 2 local 的已验证路径使用 `base`；
- DeepSeek OCR 2 local 显式使用 `tiny` 会被 pdf-craft 的前置校验拒绝，并提示使用 `base`；
  这不是上游模型已经开始运行后才发生的错误。

遇到 preset 错误时，先切换到该 backend 已验证的 preset，再判断是否存在其他问题。

### 分辨率、页面范围或 OCR token 设置不合适

这些 `ExtractionOptions` 会直接改变运行时间、显存占用和生成结果：

- `page_indexes` 只处理指定页。排查复杂输入时先给一个小范围，确认单页流程后再扩大范围；
  页码使用从 1 开始的页索引（不是从 0 开始）。
- `dpi` 默认按 300 DPI 渲染扫描页。显存不足或渲染过慢时可降低它；文字过小、识别质量明显
  下降时则应恢复较高分辨率。
- `max_page_image_file_size` 限制单页渲染图像大小。页面被过度压缩或渲染失败时，检查是否
  设置得过小；不确定时可先恢复默认值 `None`。
- `max_ocr_tokens` 是跨页面累计的 OCR token 总预算：每页的输入 token 和输出 token 都会从
  这个预算中扣除；`max_ocr_output_tokens` 则只累计限制输出 token。预算在进入下一页前耗尽
  时，提取会以 `TokenLimitError` 中断，而不是继续处理剩余页面。此时先记录异常和已发出的
  OCR 事件，再减少 `page_indexes` 或提高相应上限；提高上限也会增加供应商费用或本地显存压力。
- `includes_cover=True` 才会把识别到的封面图写入 extraction；`includes_footnotes=True` 才会
  请求并保留脚注内容。遇到“正文有了但封面或脚注缺失”时，先检查这两个选项，而不是重复
  下载模型或更换 OCR backend。

### 目录识别或输出结构异常

`toc_assumed=True` 会把输入视为已经有目录信息；如果输入并没有可用目录，章节划分可能不符合
预期。需要用 LLM 分析目录时传入 `toc_llm`，并检查它的 endpoint、模型和凭据。目录分析失败
时，先关闭 `toc_assumed` 或缩小 `page_indexes` 验证目录页，再处理 OCR 本身的问题。

`generate_plot=True` 会额外生成分析图并写入 analysis 的 `plots` 目录；这些诊断信息不进入 `.pcex`。输出体积突然
变大或目录中出现额外资源时，这是预期行为。若磁盘空间不足，先关闭该选项。

### 用 OCR 事件定位具体页面

需要判断任务卡在哪一页时，可通过 `on_ocr_event` 注册回调，记录每个 `OCREvent` 的
`page_index`、`total_pages`、`kind`、耗时以及 `input_tokens`/`output_tokens`。常见事件包括：

- `START`：开始处理一个页面；
- `RENDERED`：页面已渲染成图像，尚未完成 OCR；
- `COMPLETE`：页面识别完成；
- `FAILED`：页面识别失败，`error` 字段包含异常；
- `SKIP`：页面已有缓存结果而跳过 OCR；`IGNORE`：页面不在 `page_indexes` 范围内而不执行识别。

把这些事件与失败页面的 PDF 页码、异常消息一起记录，能快速区分“没有选中该页”“渲染失败”
和“OCR 服务失败”。`COMPLETE` 事件中的 token 字段也可用来核对计量是否异常；事件回调只
用于观测，不会自动修复失败页面。

### 处理过程中被中断

如果通过 `aborted` 回调主动停止任务，底层会直接抛出 `AbortError`；token 预算耗尽时则抛出
`TokenLimitError`。这两个异常不会自动转换成 pdf-craft 导出的 `InterruptedError`，除非调用方
显式使用 `to_interrupted_error` helper；普通调用应直接按实际异常类型捕获和记录。先检查回调
是否把任务标记为中止，再决定是否从头重跑。对于单纯想缩小问题范围的情况，优先减少页数或先
处理一小段输入，而不是在中断后直接复用不确定的输出。

## vendor OCR 问题

vendor OCR 使用远程服务，常见问题通常发生在配置或网络层，而不是本地显卡：

### 请求认证失败或返回 401/403

确认配置对象中的服务地址、模型名和 API key 属于同一家服务，并且 key 没有过期、被撤销或
超出权限。不要把文本 LLM 的 key 填入 OCR 配置，也不要把 OCR endpoint 当作翻译 LLM endpoint。

### 请求超时、限流或余额不足

确认网络可以访问 endpoint，并检查供应商控制台中的余额、配额和限流状态。减少一次提交的页面
数量可以帮助判断是单页问题还是请求规模问题。DeepSeek OCR 和 DeepSeek OCR 2 使用
`timeout_seconds` 控制单次请求超时；百度 Unlimited OCR 还可以用 `poll_interval_seconds`
控制轮询间隔，并用 `timeout_seconds` 控制等待上限。只调整适用 backend 的字段，避免把
一个 backend 的配置名套用到另一个 backend。

DeepSeek OCR 和 DeepSeek OCR 2 使用 OpenAI-compatible 配置；百度 Unlimited OCR 使用独立的
AK/SK 配置。不要把两种认证格式混用。

## 文本 LLM 和翻译问题

### EPUB 翻译或 PDF 翻译提示缺少 LLM

翻译已有 EPUB 时，需要传入 `LLM`、`translation_llm` 或 `fill_llm`。只配置 OCR 不会自动提供
文本翻译能力。`translation_llm` 负责翻译文字，`fill_llm` 负责在需要时修复 EPUB XML 结构；
只传一个 `llm` 时，它会承担两项工作。

### LLM endpoint 或模型不兼容

pdf-craft 的 `LLM` 使用 OpenAI-compatible chat completions 接口。检查 URL 是否是该服务要求的
base URL，模型名是否可用，token encoding 是否与模型匹配。确认文本 LLM 的响应包含可读文本；
空响应会在达到重试上限后抛出 `LLMEmptyResponseError`。

### 请求失败后重复重试或最终失败

`LLM` 默认会对可重试的传输错误进行重试。可以根据服务限流和网络情况调整
`retry_times` 与 `retry_interval_seconds`。如果错误持续发生，先检查 endpoint、密钥和服务状态，
再增加重试次数；重试不能修复错误的模型名或错误的请求格式。

### 翻译结果为空或 XML 修复失败

EPUB 翻译要求 LLM 返回完整、可解析的结构化结果。使用 `on_fill_failed` 接收
`FillFailedEvent`，并重点关注 `over_maximum_retries=True` 的事件；这表示该结构修复已经超过
最大重试次数，可能影响最终 EPUB。降低并发、缩短自定义提示词或更换结构化输出更稳定的模型，
通常比盲目增加重试更有效。

### 缓存命中旧结果或无法恢复

为 `LLM` 设置 `cache_path` 后，成功的请求会保存到本地缓存，重复运行可以复用已经完成的请求。
如果怀疑缓存与当前任务不匹配，为不同书籍或不同翻译任务使用新的缓存目录。不要在多个不相干
的任务之间共享一个正在写入的缓存目录。

## 输出文件和临时目录问题

`convert_pdf_to_markdown` 与 `convert_pdf_to_epub` 默认创建系统临时 analysis 目录，并在成功或
异常后清理。需要调试时传入可写的 `analysing_path`；需要复用中间结果时传入 `.pcex`
`extraction_path`。后端不接受普通目录；调用方提供的 analysis 和 `.pcex`
由调用者负责管理，不会自动删除。

如果最终输出没有生成，先确认输出路径的父目录存在且可写，并检查异常是否发生在 OCR、翻译或
渲染阶段。不要只根据生成了部分文件就判断转换成功。

## PDF 写回问题

PDF 翻译写回需要原始 PDF 和与它匹配的提取结果。写回阶段依赖提取时记录的页面几何信息和
文字来源区域，因此不能仅凭翻译后的中间结果生成一个全新的 PDF。

常见原因包括：

- 写回时使用了不同版本或不同页数的原始 PDF；
- 提取结果缺少页面几何元数据；
- 中间结果来自另一份输入文件；
- 翻译文本无法放入原始文字区域。

先确认原始 PDF 没有被替换，并使用同一份输入文件完成提取、翻译和写回。PDF 写回只支持
替换式提交；`APPEND_BLOCK` 适用于 Markdown/EPUB 等结构化输出，不适用于 PDF 页面写回。

## 仍然无法定位时

收集以下信息再提交问题：

- 操作系统、Python 版本和 pdf-craft 版本；
- 选择的 OCR backend 以及 local/vendor 运行位置；
- 是否使用 CUDA、`torch.cuda.is_available()` 的结果和 GPU 型号；
- 输入 PDF/EPUB 的页数或章节数，以及失败发生在哪一步；
- 完整异常类型和消息（删除 API key、token、文件内容等敏感信息）；
- 是否使用显式 `analysing_path`、`extraction_path`、`models_cache_path` 或 LLM `cache_path`。

不要把真实密钥、完整 PDF、模型缓存或生成日志直接提交到代码仓库。
