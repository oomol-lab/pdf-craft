"""Atomic structural-review questions backed by shared Jinja templates."""
from typing import Any
from .review_templates import review_template


def question(kind: str, name: str) -> dict[str, Any]:
    """Create an isolated question for one named source decision."""
    return review_template('shared_review', kind, name=name)
