# Concurrent event consumption and resource ownership (#890)

## Reported failure and red/green evidence

Two production jobs raised `KeyError` in `LiveExtraction.event` while removing
an OCR-ready page. Both supplied archives passed ZIP verification and all their
page XML decoded. No original PDF or hosted service is needed to reproduce it.

The new self-contained `tests/test_event_state_concurrency.py` first ran against
unmodified package code (`9df7763`). Its three original tests failed:

- Concurrent COMPLETE(1) and START(2), gated at the XML read: duplicate page
  consumption and `KeyError(1)`.
- Two actual live consumers occupying a controlled two-worker IO pool: page
  completion times out until another consumer is explicitly closed in cleanup.
- Concurrent unit finish calls, gated in the first progress callback: completed
  unit count becomes 2 rather than 1.

Static audit found the same executor dependency in the vendor rendering worker:
its synchronous callback waits for loop code that needs the same IO pool. A
fourth test using the actual cached-page vendor producer, callback bridge and a
one-worker IO pool failed before the renderer fix. It makes no vendor request.
Timeouts are failure bounds after controlled entry, not timing guesses used to
create the races. Cleanup explicitly breaks the old wait cycles so red runs exit.

## Repair boundaries

Live page consumption is serial from cursor inspection through committed append
and cursor advancement. This lock does not serialize OCR requests. Terminal IO
failure/cancellation prevents a stale cursor from replaying an append that may
already have committed. Recoverable page decoding/analysis errors retain the
existing fallback path and page attribution.

Blocking live analysis and PDF rendering each own one execution-domain worker
per active operation, instead of borrowing slots from the short-IO pool they
need to feed or service callbacks. These are not enlarged global pools: each
owned domain is joined and closed at operation cleanup. Disk queues remain
bounded in memory; provider concurrency stays under its original executor.

Finish stops new events and shares one drain task between callers. It does not
hold the consumption lock while waiting for user deliveries. Cancel can interrupt
those deliveries, close the input stream and join owned workers before workspace
cleanup. A lifecycle test caught and prevented holding that lock during a blocked
callback in the initial implementation.

WorkUnit serializes progress and completion, including callback delivery. A
successful duplicate finish is idempotent. A failed/cancelled transition remains
terminal rather than retrying callbacks or incrementing counters again. Disk unit
adapters still need their existing load/update/save locks: a lock on a loaded
object cannot serialize two separately reconstructed objects.

## Static audit

- Vendor preparation callbacks and OCR-result iteration really are two concurrent
  event producers. The existing bridge waits for each callback but does not
  serialize different producers. Consumers must own their shared transitions.
- ExtractionProgress already locks disk unit load/update/save. Its final sweep
  follows extraction completion; no concurrent final sweep is started by normal
  orchestration.
- Translation round accounting has its own progress lock. Normal unit completion
  is generally owned by one adapter; duplicate finish is a proven component risk,
  not a proven second production incident.
- FrontPages readiness updates do not suspend; its clear/check/wait sequence has
  no intervening await. No lost-wakeup defect was found there.
- DiskStream protects file numbering and close with a condition. Its iterator is
  blocking, hence the executor separation above. DiskDelivery has one consumer
  per channel; PCEX scheduling likewise has one delivery loop per channel.
- Callback exceptions continue to propagate; cancellation still waits for running
  filesystem workers. No errors are hidden with discard, no result-count checks
  are removed, and no unrelated event schema or provider limit is changed.

The pool exhaustion and repeated completion risks have offline evidence, but
there is no evidence they caused either reported production incident.

## Verification

The new suite also covers out-of-order SKIP/FAILED/IGNORE/COMPLETE, cache replay,
concurrent EOF, cancellation while draining callbacks, cancellation after append,
storage failures, recoverable malformed XML, progress versus finish, and callback
failure/cancellation during settlement. Existing streaming, OCR-stage, vendor
concurrency and operation-event regressions remain enabled.

Exact commands and red/green logs are retained under the ignored directory
`pdf-craft-output/issue890-evidence/`. No paid OCR/LLM, network smoke, GPU or model
download was used for this fix.

Final results: **12 new tests passed**, **85 focused tests passed**, and the
complete **1,210-test suite passed**. Pyright reports **0 errors / 0 warnings**;
changed-file Pylint scores **10/10** and `git diff --check` is clean. The final
four core regression tests were also rerun unchanged against a separately
extracted `9df7763` package: **2 assertion failures and 2 timeout errors**, matching
the page duplication, duplicate settlement and two pool wait cycles. See
`final-tests-baseline.log`; the final tests do not depend on production archives.
