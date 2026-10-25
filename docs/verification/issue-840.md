# GitHub #526 / #527: deterministic red → green

Baseline: `0373f2a799fb066a4d35cddd64ea457e6cbbd6dc` (main). No version bump or release preparation changes included.

## Before implementation changes

Added `tests/test_fill_text_protocol.py` first; ran:

```sh
.venv/bin/python -m pytest -q tests/test_fill_text_protocol.py
```

Result: **4 failed, 2 passed**. No production code or prompt changes existed at this point.

- Named/numeric entity decoding: actual `&lt;span&gt;`, expected `<span>`.
- Codec round trip: actual `Use &lt;span&gt; and &lt;xml&gt;.`, expected `Use <span> and <xml>.` (formatting indentation excluded).
- Real PCEX fill: `XMLFillError`, expected canonical `使用 <span> 标签。`, received `使用 &lt;span&gt; 标签。` after the configured two attempts.
- Real assembled system prompt: the required empty-inline-slot instruction was absent; the prompt instead required source padding and forbade empty spans.
- Controls already passed: tolerant raw comparisons/ampersands; the actual fill loop rejects source padding, then accepts the corrected empty slots.

Tests use real codec, prompt loading/assembly, structural validation and canonical validation. Only provider responses/first-stage translation are fixed; no network/model calls are made. The initial test-only commit allows reviewers to reproduce this baseline independently.
