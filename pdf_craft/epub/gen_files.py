import os
import shutil

from xml.etree.ElementTree import fromstring, tostring, Element
from .gen_part import generate_part
from .gen_index import gen_index, NavPoint
from .template import Template


def generate_files(from_dir_path: str, output_dir_path: str):
  template = Template()
  index_path = os.path.join(from_dir_path, "index.json")
  assets_path = os.path.join(from_dir_path, "assets")
  head_chapter_path = os.path.join(from_dir_path, "chapter.xml")

  out_oebps_path = os.path.join(output_dir_path, "OEBPS")
  out_text_path = os.path.join(out_oebps_path, "Text")
  out_styles_path = os.path.join(out_oebps_path, "styles")
  out_meta_inf_path = os.path.join(output_dir_path, "META-INF")
  os.makedirs(out_text_path, exist_ok=True)
  os.makedirs(out_styles_path, exist_ok=True)
  os.makedirs(out_meta_inf_path, exist_ok=True)

  nav_points: list[NavPoint] = []
  has_head_chapter: bool = os.path.exists(head_chapter_path)
  has_cover: bool = os.path.exists(os.path.join(assets_path, "cover.png"))

  if os.path.exists(index_path):
    toc_ncx, nav_points = gen_index(
      template=template,
      file_path=index_path,
      has_cover=has_cover,
      check_chapter_exits=lambda id: os.path.exists(
        os.path.join(from_dir_path, f"chapter_{id}.xml"),
      ),
    )
    _write(
      os.path.join(out_oebps_path, "toc.ncx"),
      content=toc_ncx,
    )

  _write(
    path=os.path.join(output_dir_path, "mimetype"),
    content=template.render("mimetype"),
  )
  _write(
    path=os.path.join(out_meta_inf_path, "container.xml"),
    content=template.render("container.xml"),
  )
  _write(
    path=os.path.join(output_dir_path, "OEBPS", "content.opf"),
    content=template.render(
      template="content.opf",
      identifier="395188820", # TODO: ISBN
      nav_points=nav_points,
      has_head_chapter=has_head_chapter,
      has_cover=has_cover,
      asset_files=[
        f for f in os.listdir(assets_path)
        if f != "cover.png" and not f.startswith(".")
      ],
    ),
  )
  _write(
    path=os.path.join(out_styles_path, "style.css"),
    content=template.render("style.css"),
  )
  if has_cover:
    _write(
      path=os.path.join(out_text_path, "cover.xhtml"),
      content=template.render("cover.xhtml"),
    )
  if has_head_chapter:
    _write(
      path=os.path.join(out_text_path, "head.xhtml"),
      content=generate_part(template, _read_xml(head_chapter_path)),
    )
  for nav_point in nav_points:
    chapter_path = os.path.join(from_dir_path, f"chapter_{nav_point.index_id}.xml")
    if os.path.exists(chapter_path):
      _write(
        path=os.path.join(out_text_path, nav_point.file_name),
        content=generate_part(template, _read_xml(chapter_path)),
      )

  if os.path.exists(assets_path):
    shutil.copytree(
      src=os.path.join(from_dir_path, "assets"),
      dst=os.path.join(out_oebps_path, "assets"),
    )

def _read_xml(path: str) -> Element:
  with open(path, "r", encoding="utf-8") as file:
    return fromstring(file.read())

def _write(path: str, content: str | Element):
  if not isinstance(content, str):
    content = tostring(content, encoding="unicode")
  with open(path, "w", encoding="utf-8") as file:
    file.write(content)