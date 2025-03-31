import os
import json

from typing import Literal
from uuid import uuid4
from zipfile import ZipFile
from xml.etree.ElementTree import fromstring, Element
from .gen_part import generate_part
from .gen_index import gen_index, NavPoint
from .i18n import I18N
from .template import Template


def generate_epub_file(
    from_dir_path: str,
    epub_file_path: str,
    lan: Literal["zh", "en"] = "zh") -> None:

  i18n = I18N(lan)
  template = Template()
  index_path = os.path.join(from_dir_path, "index.json")
  meta_path = os.path.join(from_dir_path, "meta.json")
  assets_path: str | None = os.path.join(from_dir_path, "assets")
  head_chapter_path = os.path.join(from_dir_path, "chapter.xml")

  toc_ncx: str | None = None
  nav_points: list[NavPoint] = []
  meta: dict = {}
  has_head_chapter: bool = os.path.exists(head_chapter_path)
  has_cover: bool = os.path.exists(os.path.join(from_dir_path, "cover.png"))

  if os.path.exists(meta_path):
    with open(meta_path, "r", encoding="utf-8") as f:
      meta = json.loads(f.read())

  if not os.path.exists(assets_path):
    assets_path = None

  if os.path.exists(index_path):
    toc_ncx, nav_points = gen_index(
      template=template,
      i18n=i18n,
      meta=meta,
      file_path=index_path,
      has_cover=has_cover,
      check_chapter_exits=lambda id: os.path.exists(
        os.path.join(from_dir_path, f"chapter_{id}.xml"),
      ),
    )
  with ZipFile(epub_file_path, "w") as file:
    _write_basic_files(
      file=file,
      template=template,
      i18n=i18n,
      meta=meta,
      nav_points=nav_points,
      assets_path=assets_path,
      has_cover=has_cover,
      has_head_chapter=has_head_chapter,
    )
    if toc_ncx is not None:
      file.writestr("OEBPS/toc.ncx", toc_ncx.encode("utf-8"))

    _write_assets(
      file=file,
      template=template,
      i18n=i18n,
      from_dir_path=from_dir_path,
      assets_path=assets_path,
      has_cover=has_cover,
    )
    _write_chapters(
      file=file,
      template=template,
      i18n=i18n,
      nav_points=nav_points,
      from_dir_path=from_dir_path,
      has_head_chapter=has_head_chapter,
      head_chapter_path=head_chapter_path,
    )

def _write_basic_files(
    file: ZipFile,
    template: Template,
    i18n: I18N,
    meta: dict,
    nav_points: list[NavPoint],
    assets_path: str | None,
    has_cover: bool,
    has_head_chapter: bool,
  ):

  file.writestr(
    zinfo_or_arcname="mimetype",
    data=template.render("mimetype").encode("utf-8"),
  )
  file.writestr(
    zinfo_or_arcname="META-INF/container.xml",
    data=template.render("container.xml").encode("utf-8"),
  )
  asset_files: list[str]
  if assets_path is None:
    asset_files = []
  else:
    asset_files = [
      f for f in os.listdir(assets_path)
      if not f.startswith(".")
    ]
  file.writestr(
    zinfo_or_arcname="OEBPS/content.opf",
    data=template.render(
      template="content.opf",
      meta=meta,
      i18n=i18n,
      ISBN=meta.get("ISBN", str(uuid4())),
      nav_points=nav_points,
      has_head_chapter=has_head_chapter,
      has_cover=has_cover,
      asset_files=asset_files,
    ).encode("utf-8"),
  )

def _write_assets(
    file: ZipFile,
    template: Template,
    i18n: I18N,
    from_dir_path: str,
    assets_path: str | None,
    has_cover: bool,
  ):
  file.writestr(
    zinfo_or_arcname="OEBPS/styles/style.css",
    data=template.render("style.css").encode("utf-8"),
  )
  if has_cover:
    file.writestr(
      zinfo_or_arcname="OEBPS/Text/cover.xhtml",
      data=template.render(
        template="cover.xhtml",
        i18n=i18n,
      ).encode("utf-8"),
    )

  if has_cover:
    file.write(
      filename=os.path.join(from_dir_path, "cover.png"),
      arcname="OEBPS/assets/cover.png",
    )

  if assets_path is not None:
    for file_name in sorted(os.listdir(assets_path)):
      file_path = os.path.join(assets_path, file_name)
      file.write(
        filename=file_path,
        arcname="OEBPS/assets/" + file_name,
      )

def _write_chapters(
    file: ZipFile,
    template: Template,
    i18n: I18N,
    nav_points: list[NavPoint],
    from_dir_path: str,
    has_head_chapter: bool,
    head_chapter_path: str,
):

  if has_head_chapter:
    chapter_xml = _read_xml(head_chapter_path)
    file.writestr(
      zinfo_or_arcname="OEBPS/Text/head.xhtml",
      data=generate_part(template, chapter_xml, i18n).encode("utf-8"),
    )
  for nav_point in nav_points:
    chapter_path = os.path.join(from_dir_path, f"chapter_{nav_point.index_id}.xml")
    if os.path.exists(chapter_path):
      chapter_xml = _read_xml(chapter_path)
      file.writestr(
        zinfo_or_arcname="OEBPS/Text/" + nav_point.file_name,
        data=generate_part(template, chapter_xml, i18n).encode("utf-8"),
      )

def _read_xml(path: str) -> Element:
  with open(path, "r", encoding="utf-8") as file:
    return fromstring(file.read())