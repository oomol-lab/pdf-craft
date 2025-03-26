import os
import shutil

from json import dumps, loads
from typing import Iterable, Callable
from xml.etree.ElementTree import tostring, fromstring, Element
from resource_segmentation import Resource, Incision

from ..llm import LLM
from ..pdf import PDFPageExtractor
from .common import PageInfo, PageRef
from .chunk_file import ChunkFile
from .ocr_extractor import extract_ocr_page_xmls
from .page import analyse_page
from .index import analyse_index, Index
from .citation import analyse_citations
from .main_text import analyse_main_texts
from .position import analyse_position
from .meta import extract_meta
from .chapter import generate_chapters
from .asset_matcher import ASSET_TAGS
from .types import AnalysingStep, AnalysingProgressReport, AnalysingStepReport
from .utils import search_xml_and_indexes


def analyse(
  llm: LLM,
  pdf_page_extractor: PDFPageExtractor,
  pdf_path: str,
  analysing_dir_path: str,
  output_dir_path: str,
  report_step: AnalysingStepReport | None = None,
  report_progress: AnalysingProgressReport | None = None,
):
  state_machine = _StateMachine(
    llm=llm,
    pdf_page_extractor=pdf_page_extractor,
    pdf_path=pdf_path,
    analysing_dir_path=analysing_dir_path,
    output_dir_path=output_dir_path,
    report_progress=report_progress,
    report_step=report_step,
  )
  state_machine.start()

_IndexXML = tuple[str, int, int]
_MARK_FILE_NAME = "MARK_DONE"

class _StateMachine:
  def __init__(
      self,
      llm: LLM,
      pdf_page_extractor: PDFPageExtractor,
      pdf_path: str,
      analysing_dir_path: str,
      output_dir_path: str,
      report_step: AnalysingStepReport | None,
      report_progress: AnalysingProgressReport | None,
    ):
    self._llm: LLM = llm
    self._pdf_page_extractor: PDFPageExtractor = pdf_page_extractor
    self._pdf_path: str = pdf_path
    self._analysing_dir_path: str = analysing_dir_path
    self._output_dir_path: str = output_dir_path
    self._report_step: AnalysingStepReport | None = report_step
    self._report_progress: AnalysingProgressReport | None = report_progress
    self._index: Index | None = None
    self._index_did_load: bool = False
    self._pages: list[PageInfo] | None = None

  def start(self):
    self._run_analyse_step("ocr", self._extract_ocr)
    self._run_analyse_step("pages", self._analyse_pages)
    self._run_analyse_step("index", self._analyse_index)
    self._run_analyse_step("citations", self._analyse_citations)
    self._run_analyse_step("main_texts", self._analyse_main_texts)
    self._run_analyse_step("position", self._analyse_position)
    self._run_analyse_step("meta", self._extract_meta)
    self._generate_chapters()

  def _run_analyse_step(self, dir_name: str, func: Callable[[str], None]):
    mark_path = os.path.join(self._analysing_dir_path, dir_name, _MARK_FILE_NAME)
    if not os.path.exists(mark_path):
      dir_path = self._ensure_dir_path(
        dir_path=os.path.join(self._analysing_dir_path, dir_name),
      )
      func(dir_path)
      self._atomic_write(mark_path, "")

  def _extract_ocr(self, dir_path: str):
    assets_path = self._ensure_dir_path(os.path.join(self._analysing_dir_path, "assets"))
    index_xmls = self._list_index_xmls("page", dir_path)

    for page_index, page_xml in extract_ocr_page_xmls(
      extractor=self._pdf_page_extractor,
      pdf_path=self._pdf_path,
      expected_page_indexes=set(i for _, i, _ in index_xmls),
      cover_path=os.path.join(dir_path, "cover.png"),
      assets_dir_path=assets_path,
      report_step=self._report_step,
      report_progress=self._report_progress,
    ):
      self._atomic_write(
        file_path=os.path.join(dir_path, self._xml_name("page", page_index)),
        content=tostring(page_xml, encoding="unicode"),
      )

  def _analyse_pages(self, dir_path: str):
    from_path = os.path.join(self._analysing_dir_path, "ocr")
    done_page_indexes: set[int] = set()
    done_page_names: dict[int, str] = {}

    for file_name, page_index, _ in search_xml_and_indexes("page", dir_path):
      done_page_indexes.add(page_index)
      done_page_names[page_index] = file_name

    index_xmls = self._list_index_xmls("page", from_path)

    if self._report_step is not None:
      self._report_step(AnalysingStep.ANALYSE_PAGE, len(index_xmls))

    for i, (raw_name, page_index, _) in enumerate(index_xmls):
      if page_index not in done_page_indexes:
        raw_page_xml = self._read_xml(os.path.join(from_path, raw_name))
        previous_response_xml: Element | None = None
        if page_index > 0:
          file_name = done_page_names[page_index - 1]
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
        done_page_names[page_index] = f"page_{page_index + 1}.xml"

      if self._report_progress is not None:
        self._report_progress(i + 1)

  def _analyse_index(self, dir_path: str):
    if self._report_step is not None:
      self._report_step(AnalysingStep.EXTRACT_INDEX, 0)

    from_path = os.path.join(self._analysing_dir_path, "pages")
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

  def _analyse_citations(self, dir_path: str):
    with ChunkFile(dir_path) as file:
      analyse_citations(
        llm=self._llm,
        file=file,
        pages=self._load_pages(),
        request_max_tokens=8000,
        tail_rate=0.15,
        report_step=self._report_step,
        report_progress=self._report_progress,
      )

  def _analyse_main_texts(self, dir_path: str):
    citations_dir_path = os.path.join(self._analysing_dir_path, "citations")
    with ChunkFile(dir_path) as file:
      analyse_main_texts(
        llm=self._llm,
        file=file,
        index=self._load_index(),
        pages=self._load_pages(),
        citations_dir_path=citations_dir_path,
        request_max_tokens=10000,
        gap_rate=0.1,
        report_step=self._report_step,
        report_progress=self._report_progress,
      )

  def _analyse_position(self, dir_path: str):
    main_texts_path = os.path.join(self._analysing_dir_path, "main_texts")
    with ChunkFile(dir_path) as file:
      chunk_xmls = file.filter_origin_files(main_texts_path)
      if self._report_step is not None:
        chunk_xmls = list(chunk_xmls)
        self._report_step(AnalysingStep.MARK_POSITION, len(chunk_xmls))

      for i, (start_idx, end_idx, chunk_xml) in enumerate(chunk_xmls):
        position_xml = analyse_position(self._llm, self._load_index(), chunk_xml)
        file.atomic_write_chunk(start_idx, end_idx, position_xml)
        if self._report_progress is not None:
          self._report_progress(i + 1)

  def _extract_meta(self, dir_path: str):
    page_xmls: list[Element] = []
    page_dir_path = os.path.join(self._analysing_dir_path, "pages")
    head_count = 5

    if self._report_step is not None:
      self._report_step(AnalysingStep.ANALYSE_META, 0)

    for file_name, page_index, _ in search_xml_and_indexes("page", page_dir_path):
      if page_index >= head_count:
        break
      file_path = os.path.join(page_dir_path, file_name)
      page_xml = self._read_xml(file_path)
      page_xmls.append(page_xml)

    meta_json = extract_meta(self._llm, page_xmls)
    if meta_json is not None:
      meta_file_path = os.path.join(dir_path, "meta.json")
      self._atomic_write(
        file_path=meta_file_path,
        content=dumps(meta_json, ensure_ascii=False, indent=2),
      )

  def _generate_chapters(self):
    if self._report_step is not None:
      self._report_step(AnalysingStep.GENERATE_CHAPTERS, 0)

    if self._index is not None:
      file_path = os.path.join(self._output_dir_path, "index.json")
      self._atomic_write(
        file_path=file_path,
        content=dumps(self._index.json, ensure_ascii=False),
      )
    asset_hash_set: set[str] = set()

    for id, chapter_xml in generate_chapters(
      llm=self._llm,
      chunks_path=os.path.join(self._analysing_dir_path, "position"),
    ):
      if id is None:
        file_name = "chapter.xml"
      else:
        file_name = f"chapter_{id}.xml"

      self._atomic_write(
        file_path=os.path.join(self._output_dir_path, file_name),
        content=tostring(chapter_xml, encoding="unicode"),
      )
      content_xml = chapter_xml.find("content")
      for child in content_xml:
        if child.tag in ASSET_TAGS:
          hash = child.get("hash", None)
          if hash is not None:
            asset_hash_set.add(hash)

    self._copy_file(
      src_path=os.path.join(self._analysing_dir_path, "ocr", "cover.png"),
      dst_path=os.path.join(self._output_dir_path, "cover.png"),
    )
    self._copy_file(
      src_path=os.path.join(self._analysing_dir_path, "index", "index.json"),
      dst_path=os.path.join(self._output_dir_path, "index.json"),
    )
    self._copy_file(
      src_path=os.path.join(self._analysing_dir_path, "meta", "meta.json"),
      dst_path=os.path.join(self._output_dir_path, "meta.json"),
    )

    if len(asset_hash_set) > 0:
      asset_path = self._ensure_dir_path(os.path.join(self._output_dir_path, "assets"))
      for hash in asset_hash_set:
        src_path = os.path.join(self._analysing_dir_path, "assets", f"{hash}.png")
        dst_path = os.path.join(asset_path, f"{hash}.png")
        if os.path.exists(src_path):
          shutil.copy(src_path, dst_path)

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
    index_xmls = list(search_xml_and_indexes(kind, dir_path))
    index_xmls.sort(key=lambda x: x[1])
    return index_xmls

  def _load_pages(self) -> list[PageInfo]:
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
    citation: Resource[PageRef] | None = None

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

  def _parse_text_info(self, page_index: int, children: Iterable[Element]) -> Resource[PageRef]:
    # When no text is found on this page, it means it is full of tables or
    # it is a blank page. We cannot tell if there is a cut in the context.
    start_incision: Incision = Incision.UNCERTAIN
    end_incision: Incision = Incision.UNCERTAIN
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

    return Resource(
      count=tokens,
      start_incision=start_incision,
      end_incision=end_incision,
      payload=PageRef(page_index)
    )

  def _count_elements_tokens(self, elements: Iterable[Element]) -> int:
    root = Element("page")
    root.extend(elements)
    xml_content = tostring(root, encoding="unicode")
    return self._llm.count_tokens_count(xml_content)

  def _attr_value_to_kind(self, value: str | None) -> Incision:
    if value == "must-be":
      return Incision.MUST_BE
    elif value == "most-likely":
      return Incision.MOST_LIKELY
    elif value == "impossible":
      return Incision.IMPOSSIBLE
    elif value == "uncertain":
      return Incision.UNCERTAIN
    else:
      return Incision.UNCERTAIN

  def _xml_name(self, kind: str, page_index: int, page_index2: int | None = None) -> str:
    if page_index2 is None:
      return f"{kind}_{page_index + 1}.xml"
    else:
      return f"{kind}_{page_index + 1}_{page_index2 + 1}.xml"

  def _read_xml(self, file_path: str) -> Element:
    with open(file_path, "r", encoding="utf-8") as file:
      return fromstring(file.read())

  def _copy_file(self, src_path: str, dst_path: str):
    if os.path.exists(src_path):
      shutil.copy(src_path, dst_path)

  def _atomic_write(self, file_path: str, content: str):
    try:
      with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)
        file.flush()
    except Exception as e:
      if os.path.exists(file_path):
        os.unlink(file_path)
      raise e
