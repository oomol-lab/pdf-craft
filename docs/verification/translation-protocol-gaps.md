# Blank translation units and free artist responses

Related service investigations: oomol/pdf-craft-service#310 and
 oomol/pdf-craft-service#313. The earlier source-math output protocol is retired;
[translation/fill boundaries](../../references/translation-fill-boundary.md)
are the current contract.

## Current acceptance and recovery

Unicode whitespace alone has no source translation work. The translator tests
for work without rewriting source text, then submits cloned source segments.
NBSP, special spaces, inline nodes and empty paragraphs remain intact. Read-only
context does not turn an otherwise blank primary unit into a model request.

A completed artist request is different from a blank source. Its response,
including empty text, arbitrary markup and apparent protocol references, enters
fill unchanged. There is no translation envelope parser, source-math guard or
content repair loop. Transport errors retain their normal network policy.

The single XML fill receives the complete immutable response and original
source evidence. Its own bounded repairs do not retranslate. Source formulas
are restored using explicit semantic identity bindings; additional artist math
without a source binding remains literal text. XML fill then checks structural
and canonical integrity. Accepted empty allocations replace the source with an
empty result rather than being mistaken for the failure/fallback sentinel.

Ordinary final rendering still applies its existing CSS whitespace rules.
Source-backed U+2060 padding recovery retains tags and the complete canonical
stream; embedded, invented or required format characters are not discarded.

## Historical evidence

The #310 investigation found many U+00A0-only source units. Avoiding unnecessary
requests for those units remains valid. #313 showed invented source-math markers
under the old translation protocol. That protocol and its first-stage rejection
and repair tests have been removed, rather than retaining translation scrutiny.
The 315a/315b investigations also motivated deterministic fill when a canonical
unit has one provable destination; that engineering optimization remains.
Private documents and model logs are not committed.

## Offline regressions

```sh
.venv/bin/python -m unittest tests.test_translation_protocol_gaps tests.test_source_math_identity tests.test_online_formula_allocation tests.test_request_batch -q
```

These exercise real runtime/request entry points with stub HTTP providers:
blank-source preservation, arbitrary artist output reaching fill unchanged,
empty accepted results, engineering retries without retranslation, semantic
formula identity and reordering, engineering envelopes and bounded requests.
PCEX/EPUB aggregation and actual provider call counts are covered separately by
`tests.test_translation_grouping`.
