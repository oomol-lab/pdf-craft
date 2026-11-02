"""Read prose JEV templates; Python owns the provider's question structure."""
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

_ENVIRONMENT = Environment(
    loader=PackageLoader('pdf_craft.extractor.chapter', 'data'),
    undefined=StrictUndefined,
    autoescape=False,
)


_ENTRIES = frozenset(('body_base', 'body_followup', 'footnote_base', 'footnote_followup'))


def review_text(path: str, **context: Any) -> str:
    """Render one prose template without JSON encoding or macro dispatch."""
    return _ENVIRONMENT.get_template(f'{path}.jinja').render(**context).strip()


def review_question(path: str, **context: Any) -> dict[str, Any]:
    """Separate readable instructions and criteria at their explicit headings."""
    if path not in _ENTRIES:
        raise ValueError(f'Not a JEV review entry: {path}')
    text = review_text(path, entry=path, **context)
    before_false, false_heading, false = text.rpartition('\n# False\n')
    instructions, true_heading, true = before_false.rpartition('\n# True\n')
    if not (text.startswith('# Instructions\n') and true_heading and false_heading):
        raise ValueError(f'Invalid JEV question sections: {path}')
    return dict(type='noul', instructions=_paragraphs(instructions.removeprefix('# Instructions\n')),
                criteria={'true': true.strip(), 'false': false.strip()})


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.strip().split('\n\n') if part.strip()]
