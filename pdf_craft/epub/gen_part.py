from xml.etree.ElementTree import tostring, Element
from .template import Template


def generate_part(template: Template, chapter_xml: Element) -> str:
  content_xml = chapter_xml.find("content")
  citations_xml = chapter_xml.find("citations")
  assert content_xml is not None
  return template.render(
    template="part.xhtml",
    content=list(_render_content(content_xml)),
    citations=list(_render_citations(citations_xml)),
  )

def _render_content(content_xml: Element):
  for child in content_xml:
    for to_element in _create_main_text_element(child):
      yield tostring(to_element, encoding="unicode")

def _render_citations(citations_xml: Element | None):
  if citations_xml is None:
    return

  for citation in citations_xml:
    to_div = Element("div")
    to_div.attrib["class"] = "citation"
    id = citation.get("id", None)
    is_first_child = True
    citation_children = [c for c in citation if c.tag != "label"]

    if len(citation_children) == 1:
      citation_children[0].tag = "text"

    for child in citation_children:
      for to_element in _create_main_text_element(child):
        if is_first_child:
          is_first_child = False
          if to_element.tag == "p":
            to_element.text = f"[{id}] {to_element.text}"
            to_element.attrib["id"] = f"ref-{id}"
          else:
            injected_element = Element("p")
            injected_element.text = f"[{id}]"
            injected_element.attrib["id"] = f"ref-{id}"
            to_div.append(injected_element)
        to_div.append(to_element)

    yield tostring(to_div, encoding="unicode")

def _create_main_text_element(origin: Element):
  html_tag: str | None = None
  if origin.tag == "text":
    html_tag = "p"
  elif origin.tag == "quote":
    html_tag = "p"
  elif origin.tag == "headline":
    html_tag = "h1"

  if html_tag is not None:
    element = Element(html_tag)
    _fill_text_and_citations(element, origin)
    if origin.tag == "quote":
      blockquote = Element("blockquote")
      blockquote.append(element)
      yield blockquote
    else:
      yield element
    return

  hash = origin.get("hash", None)
  if hash is None:
    return

  element = Element("p")
  image = Element("img")
  image.set("src", f"../assets/{hash}.png")

  alt: str | None = None
  if origin.text != "":
    alt = origin.text
  if alt is None:
    alt = "image"

  image.set("alt", alt)

  wrapper_div = Element("div")
  wrapper_div.set("class", "alt-wrapper")
  alt_div = Element("div")
  alt_div.set("class", "alt")
  alt_div.text = alt
  wrapper_div.append(alt_div)
  element.append(image)

  yield element
  yield wrapper_div

def _fill_text_and_citations(element: Element, origin: Element):
  element.text = origin.text
  for child in origin:
    if child.tag != "ref":
      continue
    id = child.get("id")
    assert id is not None
    anchor = Element("a")
    anchor.attrib["href"] = f"#ref-{id}"
    anchor.attrib["class"] = "super"
    anchor.text = f"[{id}]"
    anchor.tail = child.tail
    element.append(anchor)