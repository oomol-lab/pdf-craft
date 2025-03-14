from json import dumps
from xml.etree.ElementTree import Element
from .llm import LLM
from .index import Index
from .utils import encode_response, normalize_xml_text


def analyse_position(llm: LLM, index: Index | None, chunk_xml: Element) -> Element:
  if index is None:
    return chunk_xml # TODO: implements citations position

  content_xml = chunk_xml.find("content")
  raw_pages_root = Element("pages")
  origin_headlines: list[Element] = []

  for child in content_xml:
    if child.tag != "headline":
      continue
    page_index = int(child.get("idx", "-1"))
    if page_index <= index.end_page_index:
      # the reader has not yet read the catalogue.
      continue
    headline = Element("headline")
    headline.text = normalize_xml_text(child.text)
    raw_pages_root.append(headline)
    origin_headlines.append(child)

  response = llm.request("position", raw_pages_root, {
    "index": dumps(
      obj=index.json,
      ensure_ascii=False,
      indent=2,
    ),
  })
  response_xml = encode_response(response)

  for i, headline in enumerate(response_xml):
    id = headline.get("id")
    if id is not None and i < len(origin_headlines):
      origin_headline = origin_headlines[i]
      origin_headline.set("id", id)
      origin_headline.text = normalize_xml_text(headline.text)

  return chunk_xml