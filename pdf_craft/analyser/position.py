from json import dumps
from xml.etree.ElementTree import Element
from ..llm import LLM
from .index import Index
from .utils import normalize_xml_text, parse_page_indexes


def analyse_position(llm: LLM, index: Index | None, chunk_xml: Element) -> Element:
  if index is None:
    return chunk_xml # TODO: implements citations position

  content_xml = chunk_xml.find("content")
  raw_pages_root = Element("pages")
  origin_headlines: list[Element] = []

  for child in content_xml:
    if child.tag != "headline":
      continue

    page_indexes = parse_page_indexes(child)
    if not index.after_first_index_page(page_indexes[0]):
      continue

    headline = Element("headline")
    headline.text = normalize_xml_text(child.text)
    raw_pages_root.append(headline)
    origin_headlines.append(child)

  response_xml = llm.request_xml("position", raw_pages_root, {
    "index": dumps(
      obj=index.json,
      ensure_ascii=False,
      indent=2,
    ),
  })

  for i, headline in enumerate(response_xml):
    id = headline.get("id")
    if id is not None and i < len(origin_headlines):
      origin_headline = origin_headlines[i]
      origin_headline.set("id", id)
      origin_headline.text = normalize_xml_text(headline.text)

  return chunk_xml