import json

from os import PathLike
from pathlib import Path
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
      from_dir_path: PathLike,
      epub_file_path: PathLike,
      lan: Literal["zh", "en"] = "zh",
      table_render: TableRender = TableRender.HTML,
      latex_render: LaTeXRender = LaTeXRender.MATHML,
    ) -> None:

  i18n = I18N(lan)
  template = Template()
  from_dir_path = Path(from_dir_path)
  epub_file_path = Path(epub_file_path)
  index_path = from_dir_path / "index.json"
  meta_path = from_dir_path / "meta.json"
  assets_path: Path | None = from_dir_path / "assets"
  chapters_path: Path = from_dir_path / "chapters"
  head_chapter_path = chapters_path / "chapter.xml"

  toc_ncx: str
  nav_points: list[NavPoint] = []
  meta: dict = {}
  has_head_chapter: bool = head_chapter_path.exists()
  has_cover: bool = (from_dir_path / "cover.png").exists()

  if meta_path.exists():
    with open(meta_path, "r", encoding="utf-8") as f:
      meta = json.loads(f.read())

  if not assets_path.exists():
    assets_path = None

  toc_ncx, nav_points = gen_index(
    template=template,
    i18n=i18n,
    meta=meta,
    index_file_path=index_path,
    has_cover=has_cover,
    check_chapter_exits=lambda id: (chapters_path / f"chapter_{id}.xml").exists(),
  )
  epub_base_path = epub_file_path.parent
  epub_base_path.mkdir(parents=True, exist_ok=True)

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
      chapters_path=chapters_path,
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
    from_dir_path: Path,
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
      filename=from_dir_path / "cover.png",
      arcname="OEBPS/assets/cover.png",
    )
  context.add_used_asset_files()

def _write_chapters(
    context: Context,
    template: Template,
    i18n: I18N,
    nav_points: list[NavPoint],
    chapters_path: Path,
    has_head_chapter: bool,
    head_chapter_path: Path,
  ):

  if has_head_chapter:
    chapter_xml = _read_xml(head_chapter_path)
    data = generate_part(context, template, chapter_xml, i18n)
    context.file.writestr(
      zinfo_or_arcname="OEBPS/Text/head.xhtml",
      data=data.encode("utf-8"),
    )
  for nav_point in nav_points:
    chapter_path = chapters_path / f"chapter_{nav_point.index_id}.xml"
    if chapter_path.exists():
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

def _read_xml(path: Path) -> Element:
  with open(path, "r", encoding="utf-8") as file:
    return fromstring(file.read())