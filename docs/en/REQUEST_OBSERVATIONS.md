# Provider request observations

Set `on_request` when constructing `LLM`, `JEV`, `DeepSeekOCRVendorConfig`,
`DeepSeekOCR2VendorConfig` or `UnlimitedOCRVendorConfig`. No observer is installed
by default. Local OCR is not a vendor request and does not expose this hook.

```python
from collections import Counter
from pdf_craft import LLM, RequestEvent

counts = Counter()

def observed(event: RequestEvent) -> None:
    counts[event.outcome] += 1

llm = LLM(key="...", url="...", model="...", token_encoding="o200k_base",
          on_request=observed)
```

Each **actual transport attempt** invokes the observer once when it ends, including
failed attempts hidden by retries. A 429, followed by a 502, followed by success
produces three events. Cache hits, capacity waits and cooldowns are not requests.
Stream observations finish after the stream completes or breaks, not per token.
Outer translation validation does not revise an already reported request result.
This is request-quality monitoring, not a final task-success signal.

`RequestEvent` contains:

- `provider`: the original caller-owned configuration object (excluded from repr).
  Read model identity from it; do not serialize the whole object or its credentials.
- `kind`: `RequestKind.MODEL` for LLM/JEV/DeepSeek OCR. Unlimited OCR reports
  `OCR_AUTH`, `OCR_SUBMIT`, `OCR_QUERY`, `OCR_DOWNLOAD` separately, including
  retries. Query successes mean a valid query response, not completed OCR. Do not
  count all these HTTP attempts as billable model submissions. There is no extra
  aggregate event for the entire Unlimited OCR task.
- `started_at`: timezone-aware UTC time; `duration_ms`: monotonic elapsed time
  for the attempt, excluding capacity/cooldown waits and cache access.
- `outcome`: `success`, `rate_limited`, `server_error`, `request_rejected`,
  `timeout`, `network_error`, `invalid_response`, `cancelled`, `unknown_error`.
  A received 504 is a server error; a client timeout is a timeout. Empty/protocol-
  invalid responses are invalid, but translation semantics are not assessed here.
- `http_status`: actual observed status, or `None`. A broken stream can retain 200;
  this does not make its outcome successful. Custom transports may not expose it.
- `usage`: a detached `RequestUsage` snapshot, or `None` when no valid token usage
  was received. Partial reported counts are retained; absent token fields remain
  zero and are not estimated. Failed requests can have usage. Observer-only calls
  request streaming usage too; `UsageCollector` is not required. Cached input tokens
  remain a subset of input tokens. Unlimited OCR currently reports no token usage.
- `error_type`: optional exception class name, without its message or response body.

`UsageCollector` continues to decide effective/wasted consumption separately at
business adoption. Observations neither change that decision nor turn Unlimited
OCR polling/authentication into billable usage entries.

## Callback execution contract

`on_request` is a **synchronous, non-blocking collection hook**, invoked in the
calling request's execution context. Async callbacks are rejected at construction.
Update small counters or hand off to a host-owned queue; do not perform disk/network
I/O, sleep, or run conversion work here. A slow synchronous callback necessarily
delays its caller, so it violates this contract. PDF Craft does not hide that delay
behind an unbounded queue, detached tasks, or another provider concurrency pool.
Exceptions from the observer (including callback-raised cancellation) are suppressed
without logging their potentially sensitive contents; they cannot retry the request
or change its result. Delivery is best effort on cancellation, never guaranteed on
process termination. The library invokes once, not a durable exactly-once transport.

There is no global Job context. Shared provider objects observe requests from all
using tasks; the host may use its own ContextVar in the hook, or construct separate
configurations. Existing context propagation remains in effect; the library does
not transfer the callback to a background thread. Model/request payloads, headers,
URLs and error messages are not copied into the event. Providers themselves are
caller-owned objects and can contain secrets: aggregate only selected safe fields.
