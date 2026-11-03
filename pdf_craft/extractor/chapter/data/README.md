# JEV review prompts

Four root templates define source-specific structural checks:

| Footnotes | Base decisions | Supporting facts |
| --- | --- | --- |
| Disabled | [body_base.jinja](body_base.jinja) | [body_followup.jinja](body_followup.jinja) |
| Enabled | [footnote_base.jinja](footnote_base.jinja) | [footnote_followup.jinja](footnote_followup.jinja) |

Shared prose stays in `shared_base.jinja`, `shared_followup.jinja` and `evidence.jinja`.
Instructions are readable English; original document evidence keeps its language.
`# Instructions`, `# True` and `# False` delimit provider fields and are not sent.

`packed_review.py` shares evidence and repeated rules across independently named
questions. It uses at most one physical request without footnotes and two with
footnotes, including retries. Pages without applicable checks make no request.
The first footnote request checks roles and local predecessor hypotheses; the
second checks remaining relationships and source-specific supporting facts.

`packed_question.jinja`, `packed_check_context.jinja`, `packed_contract.jinja` and
`packed_text_encoding.jinja` describe lossless shared transport. Short answer keys
are restored before aggregation. `fault_orientation.jinja` states fault-oriented
questions; their scores are converted back before applying source-specific proofs.
`predecessor_candidate.jinja` and `role_confirmation.jinja` provide fallible local
hypotheses, never authority to overwrite source evidence or approve unrelated checks.

Missing scores, provider failures and exhausted request budgets follow the analysis
error policy. They cannot become a passing verdict. Review uses only immutable
current/direct-neighbor evidence and does not wait for other pages' review results.
