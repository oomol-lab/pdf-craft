"""Load JEV question text from packaged Jinja templates without shared mutations."""
import json
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

_ENVIRONMENT = Environment(
    loader=PackageLoader('pdf_craft.extractor.chapter', 'data'),
    undefined=StrictUndefined,
    autoescape=False,
)
# Preserve question/criterion order when a macro includes a structured value.
_ENVIRONMENT.policies['json.dumps_kwargs'] = {'sort_keys': False}


def review_template(family: str, block: str, **context: Any) -> Any:
    """Render one JSON value; every caller receives its own mutable containers."""
    template = _ENVIRONMENT.get_template(f'{family}.jinja')
    return json.loads(getattr(template.module, block)(**context))
