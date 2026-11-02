# JEV review prompts

Start with one of these four entry templates. Each contains the complete wording for
its mode and review round. Read the `if` / `elif` branches to see the question kinds
selected for the current source; the static prose stays inline.

| Footnote extraction | Review round | Entry |
| --- | --- | --- |
| Disabled | Base decisions | [body_base.jinja](body_base.jinja) |
| Disabled | Supplementary facts | [body_followup.jinja](body_followup.jinja) |
| Enabled | Base decisions | [footnote_base.jinja](footnote_base.jinja) |
| Enabled | Supplementary facts | [footnote_followup.jinja](footnote_followup.jinja) |

Four entries do **not** mean four HTTP requests. A round can produce multiple batches
of named questions, with at most four questions per request. Supplementary facts are
selected from the current page's evidence and base scores.

## Shared content

Only content used across entries is extracted:

- [shared_base.jinja](shared_base.jinja): word and syntax checks for proposed breaks,
  shared by the two base entries.
- [shared_followup.jinja](shared_followup.jinja): facts shared by the two supplementary
  entries. Small mode-specific qualifications stay beside the rule they qualify.
- [evidence.jinja](evidence.jinja): coordinate, lossless-text and provisional asset-text association
  descriptions used in the evidence fields. This is not another review entry.

Do not split a question into another file just because it is long. Keep entry-specific
wording inline; use ordinary conditions for genuine selection. Avoid macros, generated
JSON and Python-side concatenation of prompt instructions.

## Selection and score polarity

`True` means the proposition under `# True` holds. It does **not** always mean
that the page passes. Keep the named question's meaning and its aggregation rule
together when changing a prompt.

| Entry | Question selection | Score aggregation |
| --- | --- | --- |
| Body base | [local_review.py](../local_review.py) | `review_body_page` in [structural_review.py](../structural_review.py) |
| Body follow-up | [review_evidence.py](../review_evidence.py), [body_review_facts.py](../body_review_facts.py) | `aggregate_risks` in [structural_review.py](../structural_review.py), `apply_body_facts` in [body_review_facts.py](../body_review_facts.py) |
| Footnote base | `footnote_review_requests` in [footnote_review.py](../footnote_review.py) | `footnote_pass_scores` in the same module |
| Footnote follow-up | `supplementary_footnote_requests` in [footnote_review.py](../footnote_review.py), [footnote_quotation.py](../footnote_quotation.py), [footnote_completion.py](../footnote_completion.py) | `footnote_pass_scores` and its source-specific proof handlers |

Examples of the different polarities:

| Question family | Meaning of a high True score |
| --- | --- |
| Body base ownership and paragraph edges | Supports the existing decision; base risk is `1 - score`. |
| Footnote base definitions, references and paragraph edges | Supports the audited existing decision. |
| Footnote base `furniture`, `main_reading`, `footnote_role`, `new_note` | Establishes a role or note opening that conflicts with the existing decision; increases risk. |
| `redundancy` | Establishes that the source is not redundant; risk is `1 - score`. |
| `publisher_apparatus`, `nonreading_page_trace` | Establishes an independently checked fault; adds risk. |
| Paired image, dialogue, numbered-instruction or illustration facts | Adds risk only when the corresponding required facts jointly hold. |
| Source-specific completion, attribution or section proofs | Can lower the corresponding suspicion, or establish a conflicting role, as defined by the named handler; never grants a page-wide exemption. |

The tables are a reading guide. The named aggregation functions define how paired
facts, thresholds and source identities combine; do not replace them with a global
interpretation of True or False.

## Rendering contract

Python selects the entry, `kind`, source identifiers and applicable conditions, prepares
raw evidence, batches requests and aggregates scores. It does not append prompt prose.
The entry is rendered for each selected question. `# Instructions`, `# True` and `# False`
separate the provider fields; the headings and Jinja comments are not sent. Blank lines
separate individual instructions. Do not silently change paragraph boundaries during a
layout refactor: they affect the actual provider payload.

Prompt instructions are English; raw document evidence keeps its original language.
Treat wording changes separately from file organization. Verify an organization-only
change by generating both modes and both rounds from fixed evidence and comparing full
request payloads, field order, instruction strings and score-driven follow-up selection.
This comparison can replay saved scores and requires no provider calls.
