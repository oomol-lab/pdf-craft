from dataclasses import dataclass
from pathlib import Path
from typing import Generator
from xml.etree.ElementTree import fromstring, Element

from .common import get_truncation_attr, State, SequenceType, Truncation
from .draft import TruncationKind, ParagraphDraft
from ...llm import LLM
from ...xml import encode, encode_friendly
from ..data import ParagraphType
from ..utils import (
  remove_file,
  read_xml_file,
  xml_files,
  Context,
  Partition,
  PartitionTask,
  MultiThreads,
)


def join(
      llm: LLM,
      context: Context[State],
      threads: MultiThreads,
      type: SequenceType,
      extraction_path: Path,
      join_path: Path,
    ) -> None:

  joint = _Joint(
    llm=llm,
    context=context,
    threads=threads,
    type=type,
    extraction_path=extraction_path,
    join_path=join_path,
  )
  joint.do()

@dataclass
class _SequenceMeta:
  paragraph_type: ParagraphType
  page_index: int
  truncations: tuple[Truncation, Truncation]

_MetaTruncationDict = dict[int, tuple[_SequenceMeta, TruncationKind]]

class _Joint:
  def __init__(
        self,
        llm: LLM,
        context: Context[State],
        threads: MultiThreads,
        type: SequenceType,
        extraction_path: Path,
        join_path: Path,
      ) -> None:

    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._threads: MultiThreads = threads
    self._type: SequenceType = type
    self._extraction_path: Path = extraction_path
    self._join_path: Path = join_path

  def do(self):
    metas = self._extract_sequence_metas()
    truncations = list(self._extract_truncations(metas))
    meta_truncation_dict: _MetaTruncationDict = {}

    for i, meta in enumerate(metas):
      truncation: TruncationKind = TruncationKind.NO
      if i < len(truncations):
        truncation = truncations[i]
      meta_truncation_dict[meta.page_index] = (meta, truncation)

    self._request_llm_to_verify(meta_truncation_dict)

    last_page_index = 0
    next_paragraph_id = 1

    for paragraph in self._generate_paragraphs(meta_truncation_dict):
      page_index = paragraph.page_index
      if last_page_index != page_index:
        last_page_index = page_index
        next_paragraph_id = 1

      save_dir_path = self._ctx.path.joinpath("output", self._type.value)
      save_dir_path.mkdir(parents=True, exist_ok=True)

      paragraph_id = f"{page_index}_{next_paragraph_id}"
      file_path = save_dir_path / f"paragraph_{paragraph_id}.xml"
      next_paragraph_id += 1

      self._ctx.atomic_write(
        file_path=file_path,
        content=encode(paragraph.to_xml()),
      )

  def _extract_sequence_metas(self) -> list[_SequenceMeta]:
    metas: list[_SequenceMeta] = []
    for page_index, sequence in self._extract_sequences():
      truncation_begin = get_truncation_attr(sequence, "truncation-begin")
      truncation_end = get_truncation_attr(sequence, "truncation-end")
      metas.append(_SequenceMeta(
        paragraph_type=ParagraphType(sequence.get("type")),
        page_index=page_index,
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
        yield TruncationKind.NO
        continue

      if any(t == Truncation.YES for t in truncations) and \
         all(t in (Truncation.YES, Truncation.PROBABLY) for t in truncations):
        yield TruncationKind.VERIFIED
        continue

      yield TruncationKind.UNCERTAIN

  def _request_llm_to_verify(self, meta_truncation_dict: _MetaTruncationDict):
    uncertain_truncations_count = sum(
      1 for _, kind in meta_truncation_dict.values()
      if kind == TruncationKind.UNCERTAIN
    )
    self._join_path.mkdir(parents=True, exist_ok=True)
    self._ctx.reporter.set(uncertain_truncations_count)
    partition: Partition[tuple[int], State, Element] = Partition(
      dimension=1,
      context=self._ctx,
      sequence=(
        (min(page_indexes), max(page_indexes), request_element)
        for page_indexes, request_element in self._search_uncertain_request(
          meta_truncation_dict=meta_truncation_dict,
        )
      ),
      done=lambda begin, end: self._ctx.reporter.increment(
        count=len(read_xml_file(
          file_path=self._join_path / f"truncation_{begin[0]}_{end[0]}.xml"
        ))
      ),
      remove=lambda begin, end: remove_file(
        file_path=self._join_path / f"truncation_{begin[0]}_{end[0]}.xml"
      ),
    )
    with partition:
      self._threads.run(
        next_task=partition.pop_task,
        thread_payload=lambda: None,
        invoke=lambda _, task: self._emit_request(task),
      )

    for file_path, file_prefix, _, _ in xml_files(self._extraction_path):
      if file_prefix != "truncation":
        continue

      for child in read_xml_file(file_path):
        if child.tag != "paragraph":
          continue
        page_index = int(child.get("first-page-index", "-1"))
        if page_index not in meta_truncation_dict:
          continue
        conclusion = child.get("conclusion", None)
        meta, _ = meta_truncation_dict[page_index]
        truncation = TruncationKind.NO
        if conclusion == Truncation.YES:
          truncation = TruncationKind.VERIFIED
        meta_truncation_dict[page_index] = (meta, truncation)

  def _search_uncertain_request(self, meta_truncation_dict: _MetaTruncationDict):
    max_verify_paragraphs_count = self._ctx.state["max_verify_paragraphs_count"]
    request_element: Element | None = None
    request_page_indexes: list[int] = []

    for page_index, text1, text2 in self._search_uncertain_texts(meta_truncation_dict):
      if request_element is not None and len(request_element) > max_verify_paragraphs_count:
        yield request_page_indexes, request_element
        request_element = None
        request_page_indexes = []

      if request_element is None:
        request_element = Element("request")

      paragraph_element = Element("paragraph")
      paragraph_element.set("first-page-index", str(page_index))
      paragraph_element.set("second-page-index", str(page_index + 1))

      for text in (text1, text2):
        text_element = Element("text")
        text_element.text = text
        paragraph_element.append(text_element)

      request_element.append(paragraph_element)
      request_page_indexes.append(page_index)

    if request_element is not None:
      yield request_page_indexes, request_element

  def _emit_request(self, task: PartitionTask[tuple[int], State, Element]) -> None:
    with task:
      begin = task.begin[0]
      end = task.end[0]
      request_element = task.payload
      resp_element = self._llm.request_xml(
        template_name="sequence/truncation",
        user_data=request_element,
        params={
          "count": len(request_element),
        },
      )
      self._ctx.write_xml_file(
        file_path=self._join_path / f"truncation_{begin}_{end}.xml",
        xml=resp_element,
      )

  def _search_uncertain_texts(self, meta_truncation_dict: _MetaTruncationDict):
    max_verify_paragraph_tokens = self._ctx.state["max_verify_paragraph_tokens"]
    tail: Element | None = None

    for page_index, sequence in self._extract_sequences():
      _, truncation = meta_truncation_dict[page_index]
      head, body = self._split_sequence(sequence)
      if tail is not None:
        text1 = "".join((line.text or "").strip() for line in tail)
        text2 = "".join((line.text or "").strip() for line in head)
        tail = None

        raw_tokens1 = self._llm.encode_tokens(text1)
        raw_tokens2 = list(reversed(self._llm.encode_tokens(text2)))
        tokens1: list[int] = []
        tokens2: list[int] = []

        while len(tokens1) + len(tokens2) < max_verify_paragraph_tokens:
          if raw_tokens1:
            tokens1.append(raw_tokens1.pop())
          if raw_tokens2:
            tokens2.append(raw_tokens2.pop())
          if not raw_tokens1 and not raw_tokens2:
            break

        text1 = self._llm.decode_tokens(list(reversed(tokens1)))
        text2 = self._llm.decode_tokens(tokens2)

        if raw_tokens1:
          text1 = "..." + text1
        if raw_tokens2:
          text2 = text2 + "..."

        yield page_index - 1, text1, text2

      if truncation == TruncationKind.UNCERTAIN:
        tail = head if len(body) == 0 else body[-1]

  def _generate_paragraphs(self, meta_truncation_dict: _MetaTruncationDict) -> Generator[ParagraphDraft, None, None]:
    max_request_data_tokens = self._ctx.state["max_request_data_tokens"]
    for paragraph in self._join_and_collect_paragraphs(meta_truncation_dict):
      if paragraph.tokens <= max_request_data_tokens:
        yield paragraph
      else:
        # TODO: 临时解决 Paragraph 过大导致后续请求拆分拆不动的问题
        #      https://github.com/oomol-lab/pdf-craft/issues/227
        for forked in paragraph.fork(max_request_data_tokens):
          if forked.tokens > max_request_data_tokens:
            print(f"Warning: paragraph at page {forked.page_index} has too many tokens: {forked.tokens}")
          yield forked

  def _join_and_collect_paragraphs(self, meta_truncation_dict: _MetaTruncationDict) -> Generator[ParagraphDraft, None, None]:
    last_paragraph: ParagraphDraft | None = None
    for page_index, sequence in self._extract_sequences():
      meta, truncation = meta_truncation_dict[page_index]
      head, body = self._split_sequence(sequence)

      if last_paragraph is not None and \
         meta.paragraph_type != last_paragraph.type:
        yield last_paragraph
        last_paragraph = None

      tokens = self._llm.count_tokens_count(encode_friendly(head))
      if last_paragraph is not None:
        last_paragraph.append(meta.page_index, head, tokens)
      else:
        last_paragraph = ParagraphDraft(meta.paragraph_type)
        last_paragraph.append(meta.page_index, head, tokens)

      for element in body:
        if last_paragraph is not None:
          yield last_paragraph
        tokens = self._llm.count_tokens_count(encode_friendly(element))
        last_paragraph = ParagraphDraft(meta.paragraph_type)
        last_paragraph.append(meta.page_index, element, tokens)

      if last_paragraph is not None:
        last_paragraph.set_tail_truncation(truncation)
        if truncation == TruncationKind.NO:
          yield last_paragraph
          last_paragraph = None

    if last_paragraph is not None:
      yield last_paragraph

  def _extract_sequences(self) -> Generator[tuple[int, Element], None, None]:
    for file_path, _, _, _ in xml_files(self._extraction_path):
      with open(file_path, mode="r", encoding="utf-8") as file:
        raw_page_xmls = fromstring(file.read())

      page_pairs: list[tuple[int, Element]] = []
      for page in raw_page_xmls:
        page_index = int(page.get("page-index", "-1"))
        page_pairs.append((page_index, page))

      page_pairs.sort(key=lambda x: x[0])
      for page_index, page in page_pairs:
        sequence = next(
          (found for found in page if found.get("type", None) == self._type),
          None
        )
        if sequence is None or len(sequence) == 0:
          continue

        paragraph_type = ParagraphType.TEXT
        if self._type == SequenceType.TEXT:
          paragraph_type = ParagraphType(page.get("type", "text"))

        sequence.set("type", paragraph_type.value)
        sequence.set("page-index", str(page_index))

        for line in sequence:
          if line.tag == "abandon":
            line.tag = "text"

        for i, layout in enumerate(sequence):
          layout.set("id", f"{page_index}/{i + 1}")

        yield page_index, sequence

  def _split_sequence(self, sequence: Element) -> tuple[Element, list[Element]]:
    head: Element | None = None
    body: list[Element] = []

    for i, element in enumerate(sequence):
      if i == 0:
        head = element
      else:
        body.append(element)

    return head, body
