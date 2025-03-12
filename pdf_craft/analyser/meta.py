import re
import json

from xml.etree.ElementTree import Element
from .llm import LLM


def extract_meta(llm: LLM, page_xmls: list[Element]) -> dict:
  raw_pages_xml = Element("pages")
  for i, page_xml in enumerate(page_xmls):
    raw_pages_xml.append(page_xml)
    page_xml.set("idx", str(i + 1))

  response = llm.request("meta", raw_pages_xml, {})
  response = re.sub(r"^```JSON", "", response)
  response = re.sub(r"```$", "", response)
  try:
    response_json = json.loads(response)
  except json.JSONDecodeError as e:
    print(response)
    raise e

  if not isinstance(response_json, dict):
    return None

  if len(response_json) == 0:
    return None

  return response_json