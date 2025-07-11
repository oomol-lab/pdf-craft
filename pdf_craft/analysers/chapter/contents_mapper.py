from typing import Generator
from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ...xml import encode_friendly
from ..data import Layout, LayoutKind
from ..sequence import read_paragraphs
from ..contents import Contents, Chapter
from ..utils import remove_file, Context, Partition, PartitionTask, MultiThreads
from .common import State
from .fragment import Fragment, FragmentRequest


def map_contents(
      llm: LLM,
      context: Context[State],
      threads: MultiThreads,
      contents: Contents,
      sequence_path: Path,
      map_path: Path,
    ) -> None:

  mapper = _ContentsMapper(
    llm=llm,
    context=context,
    threads=threads,
    contents=contents,
    sequence_path=sequence_path,
    map_path=map_path,
  )
  mapper.do()

_MAX_ABSTRACT_CONTENT_TOKENS = 150

class _ContentsMapper:
  def __init__(
        self,
        llm: LLM,
        context: Context[State],
        threads: MultiThreads,
        contents: Contents,
        sequence_path: Path,
        map_path: Path,
      ) -> None:

    self._llm: LLM = llm
    self._ctx: Context[State] = context
    self._threads: MultiThreads = threads
    self._contents: Contents = contents
    self._sequence_path: Path = sequence_path
    self._map_path: Path = map_path

  def do(self):
    contents_tokens_count = self._llm.count_tokens_count(
      text=encode_friendly(self._get_contents_xml()),
    )
    self._ctx.reporter.set(
      max_count=sum(1 for _ in self._gen_request(contents_tokens_count)),
    )
    partition: Partition[tuple[int], State, FragmentRequest] = Partition(
      dimension=1,
      context=self._ctx,
      sequence=self._gen_request(contents_tokens_count),
      done=lambda _, __: self._ctx.reporter.increment(),
      remove=lambda begin, end: remove_file(
        self._map_path / f"pages_{begin}_{end}.xml"
      ),
    )
    with partition:
      self._threads.run(
        next_task=partition.pop_task,
        thread_payload=lambda: None,
        invoke=lambda _, task: self._emit_request(task),
      )

  def _gen_request(self, contents_tokens_count: int) -> Generator[tuple[int, int, FragmentRequest], None, None]:
    request = FragmentRequest()
    request_tokens = 0
    max_request_tokens = self._ctx.state["max_request_tokens"]
    max_request_tokens = max(
      max_request_tokens - contents_tokens_count,
      int(max_request_tokens * 0.25),
    )
    for fragment in self._read_fragment():
      id = 1 # only for calculate tokens. won't be used in request
      request_text = encode_friendly(fragment.to_request_xml(id))
      tokens = len(self._llm.encode_tokens(request_text))
      if request_tokens > 0 and request_tokens + tokens > max_request_tokens:
        yield request.begin_page_index, request.end_page_index, request
        request = FragmentRequest()
        request_tokens = 0

      request_tokens += tokens
      request.append(fragment)

    if request_tokens > 0:
      yield request.begin_page_index, request.end_page_index, request

  # running in background thread
  def _emit_request(self, task: PartitionTask[tuple[int], State, FragmentRequest]):
    with task:
      request = task.payload
      request_xml = request.complete_to_xml()
      request_xml.insert(0, self._get_contents_xml())
      resp_xml = self._llm.request_xml(
        template_name="contents/mapper",
        user_data=request_xml,
        params={
          "fragments_count": request.fragments_count,
        },
      )
      page_indexes_set: set[int] = set()
      map_element = Element("map")
      patch_element = Element("patch")

      for page_index, sub_patch_element in request.generate_patch_xmls(resp_xml):
        page_indexes_set.add(page_index)
        patch_element.append(sub_patch_element)

      for headline_id, chapter_id in request.generate_matched_mapper(resp_xml):
        mapper = Element("mapper")
        map_element.append(mapper)
        mapper.set("headline-id", headline_id)
        mapper.set("chapter-id", str(chapter_id))

      if page_indexes_set:
        page_indexes = sorted(list(page_indexes_set))
        map_element.set("page-indexes", ",".join(map(str, page_indexes)))

      if len(patch_element) > 0:
        map_element.append(patch_element)

      file_name = f"pages_{request.begin_page_index}_{request.end_page_index}.xml"
      file_path = self._map_path / file_name
      self._ctx.write_xml_file(file_path, map_element)

  def _read_fragment(self) -> Generator[Fragment, None, None]:
    fragment: Fragment | None = None
    fragment_tokens_count: int = 0

    for layout in self._read_headline_and_text():
      if layout.kind == LayoutKind.HEADLINE:
        if fragment is not None:
          if fragment.is_abstracts_empty or \
             fragment.page_index != layout.page_index:
            yield fragment
            fragment = None

        if fragment is None:
          fragment = Fragment(layout.page_index)
        fragment.append_headline(layout)

      elif fragment is not None and layout.kind == LayoutKind.TEXT:
        for line in layout.lines:
          tokens = self._llm.encode_tokens(line.text)
          next_tokens_count = fragment_tokens_count + len(tokens)
          if next_tokens_count <= _MAX_ABSTRACT_CONTENT_TOKENS:
            fragment.append_abstract_line(
              parent_layout=layout,
              text=line.text,
            )
            fragment_tokens_count += len(tokens)
          else:
            can_added_tokens_count = next_tokens_count - _MAX_ABSTRACT_CONTENT_TOKENS
            tokens = tokens[:can_added_tokens_count]
            fragment.append_abstract_line(
              parent_layout=layout,
              text=self._llm.decode_tokens(tokens) + "...",
              splitted=True,
            )
            fragment_tokens_count = _MAX_ABSTRACT_CONTENT_TOKENS

          if fragment_tokens_count >= _MAX_ABSTRACT_CONTENT_TOKENS:
            yield fragment
            fragment = None
            fragment_tokens_count = 0
            break

    if fragment is not None:
      yield fragment

  def _read_headline_and_text(self) -> Generator[Layout, None, None]:
    for paragraph in read_paragraphs(self._sequence_path):
      if paragraph.page_index in self._contents.page_indexes:
        continue
      for layout in paragraph.layouts:
        if layout.kind not in (LayoutKind.HEADLINE, LayoutKind.TEXT):
          continue
        if all(self._is_empty(line.text) for line in layout.lines):
          continue
        yield layout

  def _get_contents_xml(self) -> Element:
    contents_element = Element("contents")
    for tag, chapters in (
      ("prefaces", self._contents.prefaces),
      ("chapters", self._contents.chapters),
    ):
      if chapters:
        chapters_element = Element(tag)
        contents_element.append(chapters_element)
        for chapter in chapters:
          chapter_element = self._to_chapter_xml(chapter)
          chapters_element.append(chapter_element)
    return contents_element

  def _to_chapter_xml(self, chapter: Chapter) -> Element:
    chapter_element = Element("chapter")
    chapter_element.set("id", str(chapter.id))
    chapter_element.text = chapter.name
    for child in chapter.children:
      child_xml = self._to_chapter_xml(child)
      chapter_element.append(child_xml)
    return chapter_element

  def _is_empty(self, text: str) -> bool:
    for char in text:
      if char not in (" ", "\n", "\r", "\t"):
        return False
    return True