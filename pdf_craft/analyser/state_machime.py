import os
import re

from enum import auto, Enum
from .llm import LLM


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
      analysing_dir_path: str,
      output_dir_path: str,
    ):
    self._llm: LLM = llm
    self._analysing_dir_path: str = analysing_dir_path
    self._output_dir_path: str = output_dir_path
    self._phase: _Phase = self._recover_phase()

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
    dir_path = self._ensure_dir_path("ocr")
    index_xmls = self._list_index_xmls("page", dir_path)
    index_xmls = self._cut_needless_tail(dir_path, index_xmls)


  def _analyse_pages(self):
    pass

  def _analyse_index(self):
    pass

  def _analyse_citations(self):
    pass

  def _analyse_main_texts(self):
    pass

  def _analyse_position(self):
    pass

  def _generate_chapters(self):
    pass

  def _ensure_dir_path(self, dir_name: str) -> str:
    dir_path = os.path.join(self._output_dir_path, dir_name)
    os.makedirs(dir_path, exist_ok=True)
    return dir_path

  def _cut_needless_tail(self, dir_path: str, index_xmls: list[_IndexXML]):
    last_index: int = -1
    cut_index: int = -1
    for i, (file_name, start_idx, end_idx) in index_xmls:
      if start_idx != last_index + 1:
        cut_index = i
        break
      if end_idx < start_idx:
        cut_index = i
        break
      last_index = end_idx

    if cut_index != -1:
      for file_name, _, _ in index_xmls[cut_index:]:
        file_path = os.path.join(dir_path, file_name)
        if os.path.exists(file_path):
          os.unlink(file_path)
      index_xmls = index_xmls[:cut_index]

    return index_xmls

  def _list_index_xmls(self, kind: str, dir_path: str) -> list[_IndexXML]:
    index_xmls: list[_IndexXML] = []
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
      index_xmls.append((file_name, int(index1), int(index2)))
    index_xmls.sort(key=lambda x: x[1])
    return index_xmls

  def _atomic_write(self, file_path: str, content: str):
    try:
      with open(file_path, "w", encoding="utf-8") as file:
        file.write("")
    except Exception as e:
      os.unlink(file_path)
      raise e
