from pathlib import Path
from xml.etree.ElementTree import Element

from ...llm import LLM
from ..reporter import Reporter, AnalysingStep
from ..utils import read_xml_file, Context
from .common import Phase, State
from .type import Contents, Chapter
from .collection import collect
from .utils import normalize_layout_xml


def extract_contents(
      llm: LLM,
      reporter: Reporter,
      workspace_path: Path,
      sequence_path: Path,
      max_data_tokens: int,
    ) -> Contents | None:

  context: Context[State] = Context(
    reporter=reporter,
    path=workspace_path,
    init=lambda: {
      "phase": Phase.INIT,
      "page_indexes": [],
      "max_data_tokens": max_data_tokens,
    },
  )
  if context.state["phase"] == Phase.NO_CONTENTS:
    return None

  extracted_path = context.path.joinpath("extracted.xml")
  extracted_xml: Element

  if context.state["phase"] == Phase.GENERATED:
    extracted_xml = read_xml_file(extracted_path)
  else:
    # why not do it in 1 step? Because in extreme cases the contents will be
    # very long (the contents will not be processed in sections), splitting it into
    # 2 steps can reduce the prompt length and help LLM focus.
    md_content = _extract_md_contents(
      llm=llm,
      context=context,
      sequence_path=sequence_path,
    )
    if md_content is None:
      context.state = {
        **context.state,
        "phase": Phase.NO_CONTENTS.value,
      }
      return None

    extracted_xml = llm.request_xml(
      template_name="contents/format",
      user_data=f"```Markdown\n{md_content}\n```",
    )
    context.write_xml_file(extracted_path, extracted_xml)
    context.state = {
      **context.state,
      "phase": Phase.GENERATED.value,
    }
    context.reporter.go_to_step(AnalysingStep.ANALYSE_CONTENTS)

  return _parse_contents_xml(context, extracted_xml)

def _extract_md_contents(llm: LLM, context: Context[State], sequence_path: Path) -> str | None:
  md_path = context.path.joinpath("extracted.md")
  md_content: str | None = None

  if md_path.exists():
    with md_path.open("r", encoding="utf-8") as f:
      md_content = f.read()
  else:
    context.reporter.go_to_step(AnalysingStep.COLLECT_CONTENTS)
    request_xml = Element("request")
    for paragraph in collect(llm, context, sequence_path):
      layout = normalize_layout_xml(paragraph)
      if layout is not None:
        request_xml.append(layout)
      context.reporter.increment()

    if len(request_xml) > 0:
      md_content = llm.request_markdown(
        template_name="contents/extractor",
        user_data=request_xml,
      )
      context.atomic_write(md_path, md_content)

  return md_content

def _parse_contents_xml(context: Context[State], contents_xml: Element) -> Contents:
  prefaces: list[Chapter] = []
  chapters: list[Chapter] = []

  for l0_child in contents_xml:
    l0_chapters: list[Chapter]
    if l0_child.tag == "prefaces":
      l0_chapters = prefaces
    elif l0_child.tag == "chapters":
      l0_chapters = chapters
    else:
      continue
    for chapter_xml in l0_child:
      chapter = _parse_chapter(chapter_xml)
      if chapter is not None:
        l0_chapters.append(chapter)

  next_id = 1
  contents = Contents(
    prefaces=prefaces,
    chapters=chapters,
    page_indexes=context.state["page_indexes"],
  )
  for chapter in contents:
    chapter.id = next_id
    next_id += 1

  return contents

def _parse_chapter(chapter_xml: Element) -> Chapter | None:
  if chapter_xml.tag != "chapter":
    return None

  name = (chapter_xml.text or "").strip()
  if not name:
    return None

  children: list[Chapter] = []
  for child in chapter_xml:
    sub_chapter = _parse_chapter(child)
    if sub_chapter is not None:
      children.append(sub_chapter)

  return Chapter(
    id=-1,
    name=name,
    children=children,
  )