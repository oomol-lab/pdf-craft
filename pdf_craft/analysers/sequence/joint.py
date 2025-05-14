from dataclasses import dataclass
from pathlib import Path
from typing import Generator
from enum import auto, Enum
from xml.etree.ElementTree import fromstring, Element

from ...llm import LLM
from ...xml import encode
from ..context import Context
from ..utils import xml_files
from .common import State, SequenceType, Truncation
from .paragraph import ParagraphType


def join(llm: LLM, context: Context[State], type: SequenceType, extraction_path: Path):
  _Joint(llm, context, type, extraction_path).do()

@dataclass
class _SequenceMeta:
  paragraph_type: ParagraphType
  page_index: int
  truncations: tuple[Truncation, Truncation]

class _TruncationKind(Enum):
  NO = auto()
  VERIFIED = auto()
  UNCERTAIN = auto()

class _Paragraph:
  def __init__(self, type: ParagraphType, page_index: int, element: Element):
    self._type: ParagraphType = type
    self._page_index: int = page_index
    self._children: list[Element] = [element]

  def append(self, element: Element):
    self._children.append(element)

  @property
  def page_index(self) -> int:
    return self._page_index

  @property
  def type(self) -> ParagraphType:
    return self._type

  def to_xml(self) -> Element:
    element = Element("paragraph")
    element.set("type", self._type.value)
    for child in self._children:
      element.append(child)
    return element

class _Joint:
  def __init__(self, llm: LLM, context: Context[State], type: SequenceType, extraction_path: Path):
    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._type: SequenceType = type
    self._extraction_path: Path = extraction_path

  def do(self):
    metas = self._extract_sequence_metas()
    truncations = list(self._extract_truncations(metas))

    for i in range(0, len(truncations) - 1):
      truncation = truncations[i]
      if truncation == _TruncationKind.UNCERTAIN:
        # TODO: 用 LLM 来进一步确认
        # 此外还要考虑连续跨越多个段落的特殊情况，涉及合适的 prompt
        # 目前个人理解，应该关注在断裂口处，让 LLM 根据断裂口 id 来做出结论
        truncations[i] = _TruncationKind.NO

    meta_truncation_dict: dict[int, tuple[_SequenceMeta, _TruncationKind]] = {}
    for i, meta in enumerate(metas):
      truncation: _TruncationKind = _TruncationKind.NO
      if i < len(truncations):
        truncation = truncations[i]
      meta_truncation_dict[meta.page_index] = (meta, truncation)

    last_page_index = 0
    next_paragraph_id = 1

    for paragraph in self._join_and_get_sequences(meta_truncation_dict):
      page_index = paragraph.page_index
      if last_page_index != page_index:
        last_page_index = page_index
        next_paragraph_id = 1

      save_dir_path = self._ctx.path.joinpath("output", self._type.value)
      save_dir_path.mkdir(parents=True, exist_ok=True)

      paragraph_id = f"{page_index}_{next_paragraph_id}"
      file_path = save_dir_path / f"paragraph_{paragraph_id}.xml"
      next_paragraph_id += 1

      with open(file_path, mode="w", encoding="utf-8") as file:
        file.write(encode(paragraph.to_xml()))

  def _extract_sequence_metas(self) -> list[_SequenceMeta]:
    metas: list[_SequenceMeta] = []
    for sequence in self._extract_sequences():
      truncation_begin = Truncation(sequence.get("truncation-begin", Truncation.UNCERTAIN.value))
      truncation_end = Truncation(sequence.get("truncation-end", Truncation.UNCERTAIN.value))
      metas.append(_SequenceMeta(
        paragraph_type=ParagraphType(sequence.get("type")),
        page_index=int(sequence.get("page-index")),
        truncations=(truncation_begin, truncation_end),
      ))

    pre_page_index = 0 # page-index is begin from 1
    pre_meta: _SequenceMeta | None = None
    for meta in metas:
      if pre_page_index + 1 != meta.page_index:
        # The pages are not continuous, and it is impossible to cross pages in the middle,
        # so this assertion is made
        meta.truncations = (Truncation.NO, meta.truncations[1])
        if pre_meta is not None:
          pre_meta.truncations = (pre_meta.truncations[0], Truncation.NO)
      pre_page_index = meta.page_index
      pre_meta = meta

    return metas

  def _extract_truncations(self, metas: list[_SequenceMeta]):
    for i in range(0, len(metas) - 1):
      meta1 = metas[i]
      meta2 = metas[i + 1]
      _, truncation1 = meta1.truncations
      truncation2, _ = meta2.truncations
      truncations = (truncation1, truncation2)

      if all(t == Truncation.NO for t in truncations):
        yield _TruncationKind.NO
        continue

      if any(t == Truncation.YES for t in truncations) and \
         all(t in (Truncation.YES, Truncation.PROBABLY) for t in truncations):
        yield _TruncationKind.VERIFIED
        continue

      yield _TruncationKind.UNCERTAIN

  def _join_and_get_sequences(
        self,
        meta_truncation_dict: dict[int, tuple[_SequenceMeta, _TruncationKind]],
      ) -> Generator[_Paragraph, None, None]:

    last_paragraph: _Paragraph | None = None
    for sequence in self._extract_sequences():
      page_index = int(sequence.get("page-index"))
      meta, truncation = meta_truncation_dict[page_index]
      head, body = self._split_sequence(sequence)

      if last_paragraph is not None and \
         meta.paragraph_type != last_paragraph.type:
        yield last_paragraph
        last_paragraph = None

      if last_paragraph is not None:
        last_paragraph.append(head)
      else:
        last_paragraph = _Paragraph(
          type=meta.paragraph_type,
          page_index=meta.page_index,
          element=head,
        )

      for element in body:
        if last_paragraph is not None:
          yield last_paragraph
        last_paragraph = _Paragraph(
          type=meta.paragraph_type,
          page_index=meta.page_index,
          element=element,
        )
      if last_paragraph is not None and truncation == _TruncationKind.NO:
        yield last_paragraph
        last_paragraph = None

    if last_paragraph is not None:
      yield last_paragraph

  def _extract_sequences(self) -> Generator[Element, None, None]:
    for file_path, _, _, _ in xml_files(self._extraction_path):
      with open(file_path, mode="r", encoding="utf-8") as file:
        raw_page_xmls = fromstring(file.read())

      page_pairs: list[tuple[int, Element]] = []
      for page in raw_page_xmls:
        paragraph_type = ParagraphType.TEXT
        page_index = int(page.get("page-index", "-1"))
        page_pairs.append((page_index, page))

      page_pairs.sort(key=lambda x: x[0])
      for page_index, page in page_pairs:
        if self._type == SequenceType.TEXT:
          paragraph_type = ParagraphType(page.get("type", "text"))

        sequence = next(
          (found for found in page if found.get("type", None) == self._type),
          None
        )
        if sequence is None:
          continue

        sequence.set("type", paragraph_type.value)
        sequence.set("page-index", str(page_index))

        for line in sequence:
          if line.tag == "abandon":
            line.tag = "text"

        for i, layout in enumerate(sequence):
          layout.set("id", f"{page_index}/{i + 1}")

        yield sequence

  def _split_sequence(self, sequence: Element) -> tuple[Element, list[Element]]:
    head: Element | None = None
    body: list[Element] = []

    for i, element in enumerate(sequence):
      if i == 0:
        head = element
      else:
        body.append(element)

    return head, body
