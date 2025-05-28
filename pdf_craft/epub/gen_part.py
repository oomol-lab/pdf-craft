from typing import Generator
from xml.etree.ElementTree import tostring, Element

from .i18n import I18N
from .template import Template
from .context import Context
from .gen_asset import try_gen_table, try_gen_formula, try_gen_asset


def generate_part(
      context: Context,
      template: Template,
      chapter_xml: Element,
      i18n: I18N,
    ) -> str:

  return template.render(
    template="part.xhtml",
    i18n=i18n,
    content=[
      tostring(child, encoding="unicode")
      for child in _render_contents(context, chapter_xml)
    ],
    citations=[
      tostring(child, encoding="unicode")
      for child in _render_footnotes(context, chapter_xml)
    ],
  )

_XML2HTML_TAGS: dict[str, str] = {
  "headline": "h1",
  "quote": "p",
  "text": "p",
}

def _render_contents(context: Context, chapter_element: Element) -> Generator[Element, None, None]:
  for child in chapter_element:
    layout = _render_layout(context, child)
    if layout is not None:
      yield layout

def _render_footnotes(context: Context, chapter_element: Element):
  for footnote in chapter_element:
    if footnote.tag != "footnote":
      continue

    found_mark = False
    citation_div = Element("div", attrib={
      "class": "citation",
    })
    for child in footnote:
      if child.tag == "mark":
        found_mark = True
      else:
        layout = _render_layout(context, child)
        if layout is not None:
          citation_div.append(layout)

    if not found_mark or len(citation_div) == 0:
      continue

    footnote_id = int(footnote.get("id"))
    ref = Element("a")
    ref.text = f"[{footnote_id}]"
    ref.attrib = {
      "id": f"mark-{footnote_id}",
      "href": f"#ref-{footnote_id}",
      "class": "citation",
    }
    first_layout = citation_div[0]
    if first_layout.tag == "p":
      ref.tail = first_layout.text
      first_layout.text = None
      first_layout.insert(0, ref)
    else:
      inject_p = Element("p")
      inject_p.append(ref)
      citation_div.insert(0, inject_p)

    yield citation_div

def _render_layout(context: Context, raw_layout: Element) -> Element | None:
  if raw_layout.tag == "footnote":
    pass

  elif raw_layout.tag in _XML2HTML_TAGS:
    layout = Element(_XML2HTML_TAGS[raw_layout.tag])
    layout.text = raw_layout.text
    for mark in raw_layout:
      assert mark.tag == "mark"
      mark_id = int(mark.get("id"))
      anchor = Element("a")
      anchor.attrib = {
        "id": f"ref-{mark_id}",
        "href": f"#mark-{mark_id}",
        "class": "super",
      }
      layout.append(anchor)
      anchor.text = f"[{mark_id}]"
      anchor.tail = mark.tail

    if raw_layout.tag == "quote":
      blockquote = Element("blockquote")
      blockquote.append(layout)
      return blockquote
    else:
      return layout

  else:
    asset_wrapper = Element("div", attrib={
      "class": "alt-wrapper",
    })
    if raw_layout.tag == "table":
      asset_wrapper.extend(try_gen_table(context, raw_layout))
    elif raw_layout.tag == "formula":
      formula = try_gen_formula(context, raw_layout)
      if formula is not None:
        asset_wrapper.append(formula)

    if len(asset_wrapper) == 0:
      asset = try_gen_asset(context, raw_layout)
      if asset is not None:
        asset_wrapper.append(asset)

    if len(asset_wrapper) > 0:
      return asset_wrapper

  return None