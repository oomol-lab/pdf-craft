import os
import json

from typing import Literal
from uuid import uuid4
from zipfile import ZipFile
from xml.etree.ElementTree import fromstring, Element
from .types import TableRender, LaTeXRender
from .gen_part import generate_part
from .gen_index import gen_index, NavPoint
from .i18n import I18N
from .template import Template
from .context import Context


def generate_epub_file(
      from_dir_path: str,
      epub_file_path: str,
      lan: Literal["zh", "en"] = "zh",
      table_render: TableRender = TableRender.HTML,
      latex_render: LaTeXRender = LaTeXRender.MATHML,
    ) -> None:

  i18n = I18N(lan)
  template = Template()
  index_path = os.path.join(from_dir_path, "index.json")
  meta_path = os.path.join(from_dir_path, "meta.json")
  assets_path: str | None = os.path.join(from_dir_path, "assets")
  head_chapter_path = os.path.join(from_dir_path, "chapter.xml")

  toc_ncx: str
  nav_points: list[NavPoint] = []
  meta: dict = {}
  has_head_chapter: bool = os.path.exists(head_chapter_path)
  has_cover: bool = os.path.exists(os.path.join(from_dir_path, "cover.png"))

  if os.path.exists(meta_path):
    with open(meta_path, "r", encoding="utf-8") as f:
      meta = json.loads(f.read())

  toc_ncx, nav_points = gen_index(
    template=template,
    i18n=i18n,
    meta=meta,
    index_file_path=index_path,
    has_cover=has_cover,
    check_chapter_exits=lambda id: os.path.exists(
      os.path.join(from_dir_path, f"chapter_{id}.xml"),
    ),
  )
  epub_base_path = os.path.dirname(epub_file_path)
  os.makedirs(epub_base_path, exist_ok=True)

  with ZipFile(epub_file_path, "w") as file:
    context = Context(
      file=file,
      assets_path=assets_path,
      table_render=table_render,
      latex_render=latex_render,
    )
    file.writestr(
      zinfo_or_arcname="mimetype",
      data=template.render("mimetype").encode("utf-8"),
    )
    file.writestr(
      zinfo_or_arcname="OEBPS/toc.ncx",
      data=toc_ncx.encode("utf-8"),
    )
    _write_chapters(
      context=context,
      template=template,
      i18n=i18n,
      nav_points=nav_points,
      from_dir_path=from_dir_path,
      has_head_chapter=has_head_chapter,
      head_chapter_path=head_chapter_path,
    )
    _write_basic_files(
      context=context,
      template=template,
      i18n=i18n,
      meta=meta,
      nav_points=nav_points,
      has_cover=has_cover,
      has_head_chapter=has_head_chapter,
    )
    _write_assets(
      context=context,
      template=template,
      i18n=i18n,
      from_dir_path=from_dir_path,
      has_cover=has_cover,
    )

def _write_assets(
    context: Context,
    template: Template,
    i18n: I18N,
    from_dir_path: str,
    has_cover: bool,
  ):
  context.file.writestr(
    zinfo_or_arcname="OEBPS/styles/style.css",
    data=template.render("style.css").encode("utf-8"),
  )
  if has_cover:
    context.file.writestr(
      zinfo_or_arcname="OEBPS/Text/cover.xhtml",
      data=template.render(
        template="cover.xhtml",
        i18n=i18n,
      ).encode("utf-8"),
    )
  if has_cover:
    context.file.write(
      filename=os.path.join(from_dir_path, "cover.png"),
      arcname="OEBPS/assets/cover.png",
    )
  context.add_used_asset_files()

def _write_chapters(
    context: Context,
    template: Template,
    i18n: I18N,
    nav_points: list[NavPoint],
    from_dir_path: str,
    has_head_chapter: bool,
    head_chapter_path: str,
  ):

  if has_head_chapter:
    chapter_xml = _read_xml(head_chapter_path)
    data = generate_part(context, template, chapter_xml, i18n)
    context.file.writestr(
      zinfo_or_arcname="OEBPS/Text/head.xhtml",
      data=data.encode("utf-8"),
    )
  for nav_point in nav_points:
    chapter_path = os.path.join(from_dir_path, f"chapter_{nav_point.index_id}.xml")
    if os.path.exists(chapter_path):
      chapter_xml = _read_xml(chapter_path)
      data = generate_part(context, template, chapter_xml, i18n)
      context.file.writestr(
        zinfo_or_arcname="OEBPS/Text/" + nav_point.file_name,
        data=data.encode("utf-8"),
      )

def _write_basic_files(
    context: Context,
    template: Template,
    i18n: I18N,
    meta: dict,
    nav_points: list[NavPoint],
    has_cover: bool,
    has_head_chapter: bool,
  ):
  context.file.writestr(
    zinfo_or_arcname="META-INF/container.xml",
    data=template.render("container.xml").encode("utf-8"),
  )
  content = template.render(
    template="content.opf",
    meta=meta,
    i18n=i18n,
    ISBN=meta.get("ISBN", str(uuid4())),
    nav_points=nav_points,
    has_head_chapter=has_head_chapter,
    has_cover=has_cover,
    asset_files=context.used_files,
  )
  context.file.writestr(
    zinfo_or_arcname="OEBPS/content.opf",
    data=content.encode("utf-8"),
  )

def _read_xml(path: str) -> Element:
  with open(path, "r", encoding="utf-8") as file:
    return fromstring(file.read())