# Provider 请求观察

构造 `LLM`、`JEV`、`DeepSeekOCRVendorConfig`、`DeepSeekOCR2VendorConfig`、
`UnlimitedOCRVendorConfig` 时可传 `on_request`，默认不启用。本地 OCR 不属于 vendor 请求。

```python
from collections import Counter
from pdf_craft import LLM, RequestEvent

counts = Counter()
attempts_by_call = Counter()

def observed(event: RequestEvent) -> None:
    counts[event.outcome] += 1
    attempts_by_call[event.call_id] += 1

llm = LLM(key="...", url="...", model="...", token_encoding="o200k_base",
          on_request=observed)
```

每次实际传输尝试结束都会调用一次，包括失败和重试中的尝试。例如 429 → 502 → 成功产生
三条事件；缓存、等待并发名额和冷却不计请求。流式请求接收完或中断后报告，不逐 token 报告。
外层翻译业务校验不能改写已报告的请求结果。这是质量监测，不是最终任务成功信号。

`RequestEvent` 的字段：

- `provider`：调用者原配置对象（不进入 repr），模型等信息从中读取。不要把含凭证的整个对象序列化。
- `kind`：LLM/JEV/DeepSeek OCR 为 `RequestKind.MODEL`；Unlimited OCR 的鉴权、提交、
  查询、下载分别为 `OCR_AUTH`、`OCR_SUBMIT`、`OCR_QUERY`、`OCR_DOWNLOAD`，各自重试
  也分别报告。查询成功只代表查询响应有效，不代表 OCR 完成。不能把这些 HTTP 请求数统统
  当成收费的模型提交次数；没有额外的整个 Unlimited OCR 任务汇总事件。
- `call_id`：进程内唯一的自增整数，标识一次独立调用。网络、限流、IPv4 和调用内部响应重试
  沿用同一 ID；外层业务校验后重新生成属于新调用。共享 provider 或 LLM context 不合并调用。
  ID 可有空缺，完成顺序不必按 ID 排列，不保证跨进程或重启唯一。Unlimited OCR 的鉴权、提交、
  每次正常轮询和下载分别分配 ID，各自重试沿用 ID（同一次提交/查询刷新令牌后重试也沿用）；
  创建新的 OCR 任务提交则使用新 ID。调用者可按 ID 自行统计尝试次数；没有最终汇总事件，
  不能仅凭一条失败事件判定该调用不再重试。
- `started_at`：带时区的 UTC 开始时间；`duration_ms` 使用单调时钟，不包含排队、冷却或缓存访问。
- `outcome`：success、rate_limited、server_error、request_rejected、timeout、network_error、
  invalid_response、cancelled、unknown_error。收到 504 是 server_error，客户端超时是 timeout。
  空响应/协议错误是 invalid_response，不判断翻译语义好坏。
- `http_status`：实际取得的状态，否则 None。流式断开可保留 200，但 outcome 仍为失败。
- `usage`：独立的 `RequestUsage` 快照；未收到有效 token usage 时为 None。仅报告收到的部分，
  缺失 token 字段保持 0、不估算。失败也可有 usage。仅安装观察者也会请求流式 usage，
  不要求另传 UsageCollector。cached_input_tokens 属于 input_tokens 的子集。
  Unlimited OCR 当前没有 token usage。
- `error_type`：可选异常类型名，不含错误正文或响应内容。

UsageCollector 仍按业务最终采用区分 effective/wasted；观察不会改变其口径，也不把
Unlimited OCR 的轮询或鉴权纳入收费模型提交统计。

## 回调执行约定

这是同步、非阻塞的快速采集钩子；构造时拒绝异步回调。它在当前请求的执行上下文中调用，
适合更新小型计数器或转交宿主自有队列，不能在里面做网络/磁盘 I/O、sleep 或转换工作。
慢同步函数必然延迟调用者，属于违反此约定；库不通过无界队列、脱离生命周期的后台任务或
第二个 provider 并发池掩盖该问题。观察者异常（含回调自身抛出的取消）被隔离，不记录其可能
包含隐私的消息，不触发请求重试，不改变业务结果。取消时尽力通知，进程终止无法保证。
这是每次尝试调用一次的内存钩子，不提供持久化 exactly-once 投递。

没有全局 Job 状态。同一 provider 被多个任务共享时，会观察到这些任务的全部请求；宿主
可以在回调中读取自己的 ContextVar，或者分别构造配置。沿用既有上下文传播，不把回调搬到
后台线程。事件不复制模型正文、HTTP 头、URL 或错误消息；provider 对象本身由调用者持有，
可能包含凭证，上报时只取需要的安全字段。
