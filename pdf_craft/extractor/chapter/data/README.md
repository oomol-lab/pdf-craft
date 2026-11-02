# JEV review prompts

Four root templates cover the two footnote modes and two review rounds:

| Footnotes | Base decisions | Supplementary facts |
| --- | --- | --- |
| Disabled | [body_base.jinja](body_base.jinja) | [body_followup.jinja](body_followup.jinja) |
| Enabled | [footnote_base.jinja](footnote_base.jinja) | [footnote_followup.jinja](footnote_followup.jinja) |

Entry-specific wording stays inline. Only cross-entry content is shared:
[shared_base.jinja](shared_base.jinja), [shared_followup.jinja](shared_followup.jinja)
and [evidence.jinja](evidence.jinja). Do not split prose merely because it is long,
use a macro library or assemble prompt instructions in Python.

## Runtime contract

Python selects question kinds and raw evidence, batches requests and aggregates
scores. A template entry is not an HTTP request: a round may have multiple batches,
each containing at most four named questions.

`# Instructions`, `# True` and `# False` delimit provider fields; they are not sent.
Blank lines separate instructions. A high True score supports the proposition,
which may establish a fault rather than approve the page. Source-specific proofs
apply only to their corresponding test, never to the whole page.

Selection and aggregation live in [local_review.py](../local_review.py),
[structural_review.py](../structural_review.py), [body_review_facts.py](../body_review_facts.py)
and [footnote_review.py](../footnote_review.py), with supplementary evidence in
[review_evidence.py](../review_evidence.py), [footnote_quotation.py](../footnote_quotation.py)
and [footnote_completion.py](../footnote_completion.py).

Instructions are English; document evidence keeps its original language. File-only
refactors must preserve complete rendered payloads, field order, instruction strings
and score-driven question selection. Wording changes require separate evaluation.
See [review principles](../../../../docs/design/jev-structural-review.md).
