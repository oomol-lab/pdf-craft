from xml.etree.ElementTree import Element
from latex2mathml.converter import convert_to_element


def try_gen_formula(element: Element) -> Element | None:
  latex: Element | None = None
  for child in element:
    if child.tag == "latex":
      latex = child
      break
  if latex is None:
    return None

  try:
    return convert_to_element(latex.text)
  except SystemError:
    return None