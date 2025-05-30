from pathlib import Path
from json import dumps as json_dumps
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..sequence import read_paragraphs


def extract_meta(
      llm: LLM,
      workspace_path: Path,
      sequence_path: Path,
      max_request_tokens: int = 4090,
    ) -> Path:

  meta_path = workspace_path / "meta.json"

  if not meta_path.exists():
    workspace_path.mkdir(parents=True, exist_ok=True)
    request_element = _create_request_element(
      llm=llm,
      sequence_path=sequence_path,
      max_request_tokens=max_request_tokens,
    )
    meta_json = llm.request_json(
      template_name="meta",
      user_data=request_element,
    )
    with open(meta_path, "w", encoding="utf-8") as file:
      file.write(json_dumps(
        meta_json,
        ensure_ascii=False,
        indent=2,
      ))

  return meta_path

def _create_request_element(
      llm: LLM,
      sequence_path: Path,
      max_request_tokens: int = 4090,
    ) -> Element:

  tokens: int = 0
  request_element = Element("request")
  page_element: Element | None = None
  page_index: int = -1

  for paragraph, layout_element in _extract_layout_elements(sequence_path):
    layout_tokens = llm.count_tokens_count(encode_friendly(layout_element))
    if tokens > 0 and tokens + layout_tokens > max_request_tokens:
      break
    if page_index != paragraph.page_index:
      if page_element:
        request_element.append(page_element)
      page_element = Element("page")
      page_element.set("page-index", str(paragraph.page_index))
      page_element.set("type", paragraph.type.value)
      page_index = paragraph.page_index

    page_element.append(layout_element)
    tokens += layout_tokens

  if page_element:
    request_element.append(page_element)
  return request_element

def _extract_layout_elements(sequence_path: Path):
  for paragraph in read_paragraphs(sequence_path):
    layout_element: Element | None = None
    line_texts: list[str] = []

    for layout in paragraph.layouts:
      if layout_element is None:
        layout_element = Element(layout.kind.value)
      for line in layout.lines:
        line_texts.append(line.text)

    if layout_element is not None:
      layout_element.text = "\n".join(line_texts)
      yield paragraph, layout_element