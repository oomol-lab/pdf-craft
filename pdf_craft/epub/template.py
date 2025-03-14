from importlib.resources import files
from jinja2 import Environment, Template as JinjaTemplate
from ..template import create_env

class Template:
  def __init__(self):
    templates_path = files("pdf_craft").joinpath("data/templates")
    self._env: Environment = create_env(templates_path)
    self._templates: dict[str, JinjaTemplate] = {}

  def render(self, template: str, **params) -> str:
    template: JinjaTemplate = self._template(template)
    return template.render(**params)

  def _template(self, name: str) -> JinjaTemplate:
    template: JinjaTemplate = self._templates.get(name, None)
    if template is None:
      template = self._env.get_template(name)
      self._templates[name] = template
    return template