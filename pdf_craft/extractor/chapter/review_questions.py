"""Choose a review entry and render one named structural question."""
from typing import Any
from .review_templates import review_question


def question(kind: str, name: str, *, entry: str | None = None, **context: Any) -> dict[str, Any]:
    """Python selects the job; the entry template owns its complete wording."""
    if entry is None:
        entry = 'body_base' if kind in ('gap_word', 'gap_syntax') else 'body_followup'
    return review_question(entry, kind=kind, name=name, **context)
