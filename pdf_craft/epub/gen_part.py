from xml.etree.ElementTree import tostring, Element
from .template import Template


def generate_part(template: Template, chapter_xml: Element) -> str:
  content_xml = chapter_xml.get("content")
  citations_xml = chapter_xml.get("citations")
  assert content_xml is not None
  return template.render(
    template="part.xhtml",
    content=list(_render_content(content_xml)),
    citations=list(_render_citations(citations_xml)),
  )

def _render_content(content_xml: Element):
  for child in content_xml:
    to_element = _create_main_text_element(child)
    _fill_text_and_citations(to_element, child)
    yield tostring(to_element, encoding="unicode")

def _render_citations(citations_xml: Element | None):
  if citations_xml is None:
    return
  for citation in citations_xml:
    to_div = Element("div")
    id = citation.get("id", None)
    label: str | None = None
    for child in citation:
      if child.tag == "label":
        label = child.text
      else:
        child = _create_main_text_element(child)
        if label is not None:
          child.text = f"[{label}] {child.text}"
          child.attrib["id"] = f"ref-{id}"
          label = None
        to_div.append(child)
    yield tostring(to_div, encoding="unicode")

def _create_main_text_element(origin: Element):
  html_tag: str
  src: str | None = None
  if origin.tag == "text":
    html_tag = "p"
  elif origin.tag == "headline":
    html_tag = "h1"
  else:
    html_tag = "img"
    hash = origin.get("hash", None)
    if hash is not None:
      src = f"../assets/{hash}.png"

  element = Element(html_tag)
  if src is not None:
    element.attrib["src"] = src
  return element

def _fill_text_and_citations(element: Element, origin: Element):
  element.text = origin.text
  for child in origin:
    if child.tag != "ref":
      continue
    id = child.get("id")
    assert id is not None
    anchor = Element("a")
    anchor.attrib["href"] = f"#ref-{id}"
    anchor.text = f"[{id}]"
    anchor.tail = child.tail