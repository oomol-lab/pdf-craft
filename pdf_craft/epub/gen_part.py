from xml.etree.ElementTree import tostring, Element
from .i18n import I18N
from .template import Template


def generate_part(template: Template, chapter_xml: Element, i18n: I18N) -> str:
  content_xml = chapter_xml.find("content")
  citations_xml = chapter_xml.find("citations")
  assert content_xml is not None
  return template.render(
    template="part.xhtml",
    i18n=i18n,
    content=list(_render_content(content_xml)),
    citations=list(_render_citations(citations_xml)),
  )

def _render_content(content_xml: Element):
  used_ref_ids: set[str] = set()
  for child in content_xml:
    for to_element in _create_main_text_element(child, used_ref_ids):
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

    used_citation_ids: set[str] = set()

    for child in citation_children:
      for to_element in _create_main_text_element(child):
        ref_element = Element("a")
        ref_element.text = f"[{id}]"
        ref_element.attrib = {
          "href": f"#ref-{id}",
          "class": "citation",
        }
        if id not in used_citation_ids:
          used_citation_ids.add(id)
          ref_element.attrib = {
            "id": f"citation-{id}",
            **ref_element.attrib,
          }
        if is_first_child:
          is_first_child = False
          if to_element.tag == "p":
            ref_element.tail = to_element.text
            to_element.text = None
            to_element.append(ref_element)
          else:
            injected_element = Element("p")
            to_div.append(injected_element)
            injected_element.append(ref_element)

        to_div.append(to_element)

    yield tostring(to_div, encoding="unicode")

def _create_main_text_element(origin: Element, used_ref_ids: set[str] | None = None):
  html_tag: str | None = None
  if origin.tag == "text":
    html_tag = "p"
  elif origin.tag == "quote":
    html_tag = "p"
  elif origin.tag == "headline":
    html_tag = "h1"

  if html_tag is not None:
    element = Element(html_tag)
    _fill_text_and_citations(element, origin, used_ref_ids)
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

  image = Element("img")
  image.set("src", f"../assets/{hash}.png")

  alt: str | None = None
  if origin.text != "":
    alt = origin.text
  if alt is None:
    image.set("alt", "image")
  else:
    image.set("alt", alt)

  wrapper_div = Element("div")
  wrapper_div.set("class", "alt-wrapper")
  wrapper_div.append(image)

  if alt is not None:
    alt_div = Element("div")
    alt_div.set("class", "alt")
    alt_div.text = alt
    wrapper_div.append(alt_div)

  yield wrapper_div

def _fill_text_and_citations(element: Element, origin: Element, used_ref_ids: set[str] | None):
  element.text = origin.text
  for child in origin:
    if child.tag != "ref":
      continue
    id = child.get("id")
    assert id is not None
    anchor = Element("a")
    anchor.attrib = {
      "id": f"ref-{id}",
      "href": f"#citation-{id}",
      "class": "super",
    }
    if used_ref_ids is not None:
      if id in used_ref_ids:
        anchor.attrib.pop("id", None)
      else:
        used_ref_ids.add(id)

    anchor.text = f"[{id}]"
    anchor.tail = child.tail
    element.append(anchor)