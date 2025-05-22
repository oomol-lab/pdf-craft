from pathlib import Path
from typing import TypedDict

from ...pdf import PDFPageExtractor
from ..utils import Context
from .extractor import extract_ocr_page_xmls


class _State(TypedDict):
  completed_scanning: bool
  completed_pages: list[int] = []

def generate_ocr_pages(
      extractor: PDFPageExtractor,
      pdf_path: Path,
      ocr_path: Path,
      assets_path: Path,
    ) -> None:

  context: Context[_State] = Context(ocr_path, lambda: {
    "completed_scanning": False,
    "completed_pages": [],
  })
  if context.state["completed_scanning"]:
    return

  for path in (context.path, assets_path):
    path.mkdir(parents=True, exist_ok=True)

  for page_index, page_xml in extract_ocr_page_xmls(
    extractor=extractor,
    pdf_path=pdf_path,
    expected_page_indexes=set(context.state["completed_pages"]),
    cover_path=assets_path / "cover.png",
    assets_dir_path=assets_path,
  ):
    file_name = f"page_{page_index + 1}.xml"
    file_path = context.path / file_name
    context.write_xml_file(file_path, page_xml)
    context.state = {
      **context.state,
      "completed_pages": sorted([
        *context.state["completed_pages"],
        page_index,
      ]),
    }
  context.state = {
    **context.state,
    "completed_pages": [],
    "completed_scanning": True,
  }