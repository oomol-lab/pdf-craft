import os
from xml.etree.ElementTree import fromstring, tostring, Element
from .gen_part import generate_part
from .template import Template


def generate_files(from_dir_path: str, output_dir_path: str):
  template = Template()
  chapter_path = os.path.join(from_dir_path, "chapter.xml")
  out_text_path = os.path.join(output_dir_path, "OEBPS", "Text")
  out_styles_path = os.path.join(output_dir_path, "OEBPS", "styles")
  out_meta_inf_path = os.path.join(output_dir_path, "META-INF")
  os.makedirs(out_text_path, exist_ok=True)
  os.makedirs(out_styles_path, exist_ok=True)
  os.makedirs(out_meta_inf_path, exist_ok=True)

  _write(
    os.path.join(output_dir_path, "mimetype"),
    template.render("mimetype"),
  )
  _write(
    os.path.join(out_meta_inf_path, "container.xml"),
    template.render("container.xml"),
  )
  _write(
    os.path.join(output_dir_path, "OEBPS", "content.opf"),
    template.render("content.opf"),
  )
  _write(
    os.path.join(out_styles_path, "style.css"),
    template.render("style.css"),
  )
  _write(
    os.path.join(out_text_path, "part0001.xhtml"),
    content=generate_part(template, _read_xml(chapter_path)),
  )

def _read_xml(path: str) -> Element:
  with open(path, "r", encoding="utf-8") as file:
    return fromstring(file.read())

def _write(path: str, content: str | Element):
  if not isinstance(content, str):
    content = tostring(content, encoding="unicode")
  with open(path, "w", encoding="utf-8") as file:
    file.write(content)