import os
import re

from enum import auto, Enum
from json import dumps, loads
from tqdm import tqdm
from typing import Iterable
from xml.etree.ElementTree import tostring, fromstring, Element
from doc_page_extractor import PaddleLang
from ..pdf import PDFPageExtractor
from .llm import LLM
from .types import PageInfo, TextInfo, TextIncision
from .ocr_extractor import extract_ocr_page_xmls
from .page import analyse_page
from .index import analyse_index, Index


_IndexXML = tuple[str, int, int]

class _Phase(Enum):
  OCR = auto()
  PAGES = auto()
  INDEX = auto()
  CITATIONS = auto()
  MAIN_TEXTS = auto()
  POSITION = auto()
  CHAPTERS = auto()

_MARK_FILE_NAME = "MARK_DONE"
_PHASE_DIR_NAME_MAP = (
  ("ocr", _Phase.OCR),
  ("pages", _Phase.PAGES),
  ("index", _Phase.INDEX),
  ("citations", _Phase.CITATIONS),
  ("main_texts", _Phase.MAIN_TEXTS),
  ("position", _Phase.POSITION),
)

class StateMachine:
  def __init__(
      self,
      llm: LLM,
      pdf_page_extractor: PDFPageExtractor,
      pdf_path: str,
      lang: PaddleLang,
      analysing_dir_path: str,
      output_dir_path: str,
    ):
    self._llm: LLM = llm
    self._pdf_page_extractor: PDFPageExtractor = pdf_page_extractor
    self._pdf_path: str = pdf_path
    self._lang: PaddleLang = lang
    self._analysing_dir_path: str = analysing_dir_path
    self._output_dir_path: str = output_dir_path
    self._phase: _Phase = self._recover_phase()
    self._index: Index | None = None
    self._index_did_load: bool = False
    self._pages: list[PageInfo] | None = None

  def start(self):
    while self._phase != _Phase.CHAPTERS:
      if self._phase == _Phase.OCR:
        self._extract_ocr()
      elif self._phase == _Phase.PAGES:
        self._analyse_pages()
      elif self._phase == _Phase.INDEX:
        self._analyse_index()
      elif self._phase == _Phase.CITATIONS:
        self._analyse_citations()
      elif self._phase == _Phase.MAIN_TEXTS:
        self._analyse_main_texts()
      elif self._phase == _Phase.POSITION:
        self._analyse_position()

      self._mark_step_done(self._phase)
      self._phase = self._next_phase(self._phase)

    self._generate_chapters()

  def _recover_phase(self):
    for name, phase in reversed(_PHASE_DIR_NAME_MAP):
      mark_path = os.path.join(self._analysing_dir_path, name, _MARK_FILE_NAME)
      if os.path.exists(mark_path):
        return self._next_phase(phase)
    return _Phase.OCR

  def _next_phase(self, phase: _Phase) -> _Phase:
    if phase == _Phase.OCR:
      return _Phase.PAGES
    elif phase == _Phase.PAGES:
      return _Phase.INDEX
    elif phase == _Phase.INDEX:
      return _Phase.CITATIONS
    elif phase == _Phase.CITATIONS:
      return _Phase.MAIN_TEXTS
    elif phase == _Phase.MAIN_TEXTS:
      return _Phase.POSITION
    elif phase == _Phase.POSITION:
      return _Phase.CHAPTERS
    else:
      return _Phase.CHAPTERS

  def _mark_step_done(self, phase: _Phase):
    dir_name: str | None = None
    for name, p in _PHASE_DIR_NAME_MAP:
      if p == phase:
        dir_name = name
        break
    if dir_name is not None:
      mark_path = os.path.join(self._analysing_dir_path, dir_name, _MARK_FILE_NAME)
      self._atomic_write(mark_path, "")

  def _extract_ocr(self):
    dir_path = self._ensure_dir_path(os.path.join(self._analysing_dir_path, "ocr"))
    assets_path = self._ensure_dir_path(os.path.join(self._output_dir_path, "assets"))
    index_xmls = self._list_index_xmls("page", dir_path)

    for page_index, page_xml in extract_ocr_page_xmls(
      extractor=self._pdf_page_extractor,
      pdf_path=self._pdf_path,
      lang=self._lang,
      expected_page_indexes=set(i for _, i, _ in index_xmls),
      cover_path=os.path.join(dir_path, "cover.png"),
      assets_dir_path=assets_path,
    ):
      self._atomic_write(
        file_path=os.path.join(dir_path, f"page_{page_index + 1}.xml"),
        content=tostring(page_xml, encoding="unicode"),
      )

  def _analyse_pages(self):
    from_path = os.path.join(self._analysing_dir_path, "ocr")
    dir_path = self._ensure_dir_path(os.path.join(self._analysing_dir_path, "pages"))
    done_page_indexes: set[int] = set()
    done_page_names: dict[int, str] = {}

    for file_name, i, _ in self._search_index_xmls("page", dir_path):
      done_page_indexes.add(i)
      done_page_names[i] = file_name

    for raw_name, i, _ in tqdm(self._list_index_xmls("page", from_path)):
      if i in done_page_indexes:
        continue

      raw_page_xml = self._read_xml(os.path.join(from_path, raw_name))
      previous_response_xml: Element | None = None
      if i > 0:
        file_name = done_page_names[i - 1]
        file_path = os.path.join(dir_path, file_name)
        previous_response_xml = self._read_xml(file_path)

      response_xml = analyse_page(
        llm=self._llm,
        raw_page_xml=raw_page_xml,
        previous_page_xml=previous_response_xml,
      )
      self._atomic_write(
        file_path=os.path.join(dir_path, raw_name),
        content=tostring(response_xml, encoding="unicode"),
      )
      done_page_names[i] = f"page_{i + 1}.xml"

  def _analyse_index(self):
    from_path = os.path.join(self._analysing_dir_path, "pages")
    dir_path = self._ensure_dir_path(os.path.join(self._analysing_dir_path, "index"))
    json_index, index = analyse_index(
      llm=self._llm,
      raw=(
        (i, self._read_xml(os.path.join(from_path, file_name)))
        for file_name, i, _ in self._list_index_xmls("page", from_path)
      )
    )
    if json_index is not None:
      self._atomic_write(
        file_path=os.path.join(dir_path, "index.json"),
        content=dumps(
          obj=json_index,
          ensure_ascii=False,
          indent=2,
        ),
      )
    if index is not None:
      self._index = index
      self._index_did_load = True

  def _analyse_citations(self):
    from_path = os.path.join(self._analysing_dir_path, "pages")
    dir_path = self._ensure_dir_path(os.path.join(self._analysing_dir_path, "citations"))

  def _analyse_main_texts(self):
    raise NotImplementedError()

  def _analyse_position(self):
    raise NotImplementedError()

  def _generate_chapters(self):
    raise NotImplementedError()

  def _ensure_dir_path(self, dir_path: str) -> str:
    os.makedirs(dir_path, exist_ok=True)
    return dir_path

  def _load_index(self) -> Index | None:
    if not self._index_did_load:
      index_file_path = os.path.join(self._analysing_dir_path, "index", "index.json")
      self._index_did_load = True

      if os.path.exists(index_file_path):
        with open(index_file_path, "r", encoding="utf-8") as file:
          self._index = Index(loads(file.read()))

    return self._index

  def _list_index_xmls(self, kind: str, dir_path: str) -> list[_IndexXML]:
    index_xmls = list(self._search_index_xmls(kind, dir_path))
    index_xmls.sort(key=lambda x: x[1])
    return index_xmls

  def _load_page_infos(self) -> list[PageInfo]:
    if self._pages is None:
      pages: list[PageInfo] = []
      pages_path = os.path.join(self._analysing_dir_path, "pages")

      for file_name, page_index, _ in self._list_index_xmls("page", pages_path):
        file_path = os.path.join(pages_path, file_name)
        page_xml = self._read_xml(file_path)
        page = self._parse_page_info(file_path, page_index, page_xml)
        pages.append(page)

      pages.sort(key=lambda p: p.page_index)
      self._pages = pages

    return self._pages

  def _parse_page_info(self, file_path: str, page_index: int, root: Element) -> PageInfo:
    main_children: list[Element] = []
    citation: TextInfo | None = None

    for child in root:
      if child.tag == "citation":
        citation = self._parse_text_info(page_index, child)
      else:
        main_children.append(child)

    return PageInfo(
      page_index=page_index,
      citation=citation,
      file=lambda: open(file_path, "rb"),
      main=self._parse_text_info(page_index, main_children),
    )

  def _parse_text_info(self, page_index: int, children: Iterable[Element]) -> TextInfo:
    # When no text is found on this page, it means it is full of tables or
    # it is a blank page. We cannot tell if there is a cut in the context.
    start_incision: TextIncision = TextIncision.UNCERTAIN
    end_incision: TextIncision = TextIncision.UNCERTAIN
    first: Element | None = None
    last: Element | None = None

    for child in children:
      if first is None:
        first = child
      last = child

    if first is not None and last is not None:
      if first.tag == "text":
        start_incision = self._attr_value_to_kind(first.attrib.get("start-incision"))
      if last.tag == "text":
        end_incision = self._attr_value_to_kind(last.attrib.get("end-incision"))

    tokens = self._count_elements_tokens(children)

    return TextInfo(
      page_index=page_index,
      tokens=tokens,
      start_incision=start_incision,
      end_incision=end_incision,
    )

  def _count_elements_tokens(self, elements: Iterable[Element]) -> int:
    root = Element("page")
    root.extend(elements)
    xml_content = tostring(root, encoding="unicode")
    return self._llm.count_tokens_count(xml_content)

  def _attr_value_to_kind(self, value: str | None) -> TextIncision:
    if value == "must-be":
      return TextIncision.MUST_BE
    elif value == "most-likely":
      return TextIncision.MOST_LIKELY
    elif value == "impossible":
      return TextIncision.IMPOSSIBLE
    elif value == "uncertain":
      return TextIncision.UNCERTAIN
    else:
      return TextIncision.UNCERTAIN

  def _search_index_xmls(self, kind: str, dir_path: str):
    for file_name in os.listdir(dir_path):
      matches = re.match(r"^[a-zA-Z]+_\d+(_\d+)?\.xml$", file_name)
      if not matches:
        continue
      file_kind: str
      index1: str
      index2: str
      cells = re.sub(r"\..*$", "", file_name).split("_")
      if len(cells) == 3:
        file_kind, index1, index2 = cells
      else:
        file_kind, index1 = cells
        index2 = index1
      if kind != file_kind:
        continue
      yield file_name, int(index1) - 1, int(index2) - 1

  def _read_xml(self, file_path: str) -> Element:
    with open(file_path, "r", encoding="utf-8") as file:
      return fromstring(file.read())

  def _atomic_write(self, file_path: str, content: str):
    try:
      with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)
    except Exception as e:
      if os.path.exists(file_path):
        os.unlink(file_path)
      raise e
