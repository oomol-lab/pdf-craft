"""Atomic structural-review questions backed by shared Jinja templates."""
from typing import Any
from .review_templates import review_question


def question(kind: str, name: str) -> dict[str, Any]:
    """Create an isolated question for one named source decision."""
    return review_question('shared/' + kind, name=name)
