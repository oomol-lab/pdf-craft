from pathlib import Path
from xml.etree.ElementTree import Element
from shutil import rmtree

from ...llm import LLM
from ..reporter import Reporter, AnalysingStep
from ..contents import Contents, Chapter
from ..data import ASSET_LAYOUT_KINDS, Paragraph
from ..utils import xml_files, Context, MultiThreads
from .common import State, Phase
from .contents_mapper import map_contents
from .patcher import read_paragraphs, read_paragraphs_with_patches


def generate_chapters(
      llm: LLM,
      reporter: Reporter,
      threads: MultiThreads,
      contents: Contents | None,
      sequence_path: Path,
      workspace_path: Path,
      max_request_tokens: int,
    ) -> tuple[Path, Contents | None]:

  map_path: Path = workspace_path / "map"
  output_path = workspace_path / "output"
  context: Context[State] = Context(
    reporter=reporter,
    path=workspace_path,
    init=lambda: {
      "phase": Phase.MAPPER.value,
      "has_contents": False,
      "max_request_tokens": max_request_tokens,
      "completed_ranges": [],
    },
  )
  if context.state["phase"] == Phase.MAPPER:
    has_contents = False
    if contents is not None:
      has_contents = True
      context.reporter.go_to_step(AnalysingStep.MAPPING_CONTENTS)
      map_contents(
        llm=llm,
        context=context,
        threads=threads,
        contents=contents,
        sequence_path=sequence_path,
        map_path=map_path,
      )

    context.state = {
      **context.state,
      "phase": Phase.CHAPTER.value,
      "has_contents": has_contents,
    }

  used_chapter_ids: set[int] = set()

  if context.state["phase"] == Phase.CHAPTER:
    contents_and_map_path: tuple[Contents, Path] | None = None
    if contents and context.state["has_contents"]:
      contents_and_map_path = (contents, map_path)

    rmtree(output_path, ignore_errors=True)
    _generate_chapter_xmls(
      context=context,
      contents_and_map_path=contents_and_map_path,
      used_chapter_ids=used_chapter_ids,
      sequence_path=sequence_path,
      output_path=output_path,
    )
    context.state = {
      **context.state,
      "phase": Phase.COMPLETED.value,
    }
  elif output_path.exists():
    for _, _, chapter_id, _ in xml_files(output_path):
      used_chapter_ids.add(chapter_id)

  if contents is not None:
    contents = _filter_contents(
      contents=contents,
      used_chapter_ids=used_chapter_ids,
    )
  return output_path, contents

def _generate_chapter_xmls(
      context: Context[State],
      contents_and_map_path: tuple[Contents, Path] | None,
      used_chapter_ids: set[int],
      sequence_path: Path,
      output_path: Path,
    ):

  chapter: Chapter | None = None
  chapter_element = Element("chapter")

  def save_chapter():
    nonlocal chapter, chapter_element
    if len(chapter_element) == 0:
      return

    file_name = "chapter.xml"
    if chapter is not None:
      file_name = f"chapter_{chapter.id}.xml"
      used_chapter_ids.add(chapter.id)

    context.write_xml_file(
      file_path=output_path / file_name,
      xml=chapter_element,
    )
    chapter_element = Element("chapter")

  if contents_and_map_path is None:
    for paragraph in read_paragraphs(sequence_path):
      for layout_element in _flat_layouts_from_paragraph(paragraph):
        chapter_element.append(layout_element)
  else:
    contents, map_path = contents_and_map_path

    for this_chapter, paragraph in read_paragraphs_with_patches(
      paragraph_path=sequence_path,
      contents=contents,
      map_path=map_path,
    ):
      if chapter is None:
        if this_chapter is not None:
          save_chapter()
          chapter = this_chapter
      elif this_chapter is not None and this_chapter.id != chapter.id:
        save_chapter()
        chapter = this_chapter

      for layout_element in _flat_layouts_from_paragraph(paragraph):
        chapter_element.append(layout_element)

  save_chapter()

def _flat_layouts_from_paragraph(paragraph: Paragraph):
  layout_element: Element | None = None
  for layout in paragraph.layouts:
    if layout_element is not None and \
       layout_element.tag != layout.kind:
      yield layout_element
      layout_element = None

    if layout.kind in ASSET_LAYOUT_KINDS:
      asset_element = layout.to_xml()
      if asset_element.get("hash", None):
        # TODO: 临时做法，为了排除 hash 为空的情况而做的防御性工作。
        #       之所以会出现此现象，是因为 https://github.com/oomol-lab/pdf-craft/issues/238 。
        yield asset_element
      continue

    if layout_element is None:
      layout_element = Element(layout.kind.value, {
        "id": layout.id,
      })
    for line in layout.lines:
      layout_element.append(line.to_xml())

  if layout_element is not None:
    yield layout_element

def _filter_contents(contents: Contents, used_chapter_ids: set[int]) -> Contents:
  return Contents(
    page_indexes=contents.page_indexes,
    chapters=_filter_chapters(
      raw_chapters=contents.chapters,
      used_chapter_ids=used_chapter_ids,
    ),
    prefaces=_filter_chapters(
      raw_chapters=contents.prefaces,
      used_chapter_ids=used_chapter_ids,
    ),
  )

def _filter_chapters(raw_chapters: list[Chapter], used_chapter_ids: set[int]) -> list[Chapter]:
  chapters: list[Chapter] = []
  for raw_chapter in raw_chapters:
    chapter = _filter_chapter(raw_chapter, used_chapter_ids)
    if chapter is not None:
      chapters.append(chapter)
  return chapters

def _filter_chapter(raw_chapter: Chapter, used_chapter_ids: set[int]) -> Chapter | None:
  filtered_chapter: Chapter | None = None
  children: list[Chapter] = []

  for sub_chapter in raw_chapter.children:
    sub_chapter = _filter_chapter(sub_chapter, used_chapter_ids)
    if sub_chapter is not None:
      children.append(sub_chapter)

  if len(children) > 0 or raw_chapter.id in used_chapter_ids:
    filtered_chapter = Chapter(
      id=raw_chapter.id,
      name=raw_chapter.name,
      children=children,
    )
  return filtered_chapter