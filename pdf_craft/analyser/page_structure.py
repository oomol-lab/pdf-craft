import io

from html import escape
from ..pdf import Block, Text, TextBlock, AssetBlock, TextKind, AssetKind
from .llm import LLM


class Format:
  def __init__(self, llm: LLM):
    self._llm: LLM = llm

  def push(self, blocks: list[Block]):
    page_xml = self._get_page_xml(blocks)
    print(page_xml)
    print("\nResponse:")
    print(self._llm.request("page_structure", page_xml))

  def _get_page_xml(self, blocks: list[Block]) -> str:
    buffer = io.StringIO()
    buffer.write("<page>\n")
    for block in blocks:
      self._write_block(buffer, block)
    buffer.write("</page>\n")
    return buffer.getvalue()

  def _write_block(self, buffer: io.StringIO, block: Block):
    if isinstance(block, TextBlock):
      tag_name: str
      if block.kind == TextKind.TITLE:
        tag_name = "title"
      elif block.kind == TextKind.PLAIN_TEXT:
        tag_name = "text"
      elif block.kind == TextKind.ABANDON:
        tag_name = "abandon"

      buffer.write("<")
      buffer.write(tag_name)

      if block.kind == TextKind.PLAIN_TEXT:
        buffer.write(" indent=")
        buffer.write("\"true\"" if block.has_paragraph_indentation else "\"false\"")
        buffer.write(" touch-end=")
        buffer.write("\"true\"" if block.last_line_touch_end else "\"false\"")

      buffer.write(">\n")

      self._write_texts(buffer, block.texts)

      buffer.write("</")
      buffer.write(tag_name)
      buffer.write(">\n")

    elif isinstance(block, AssetBlock):
      tag_name: str
      if block.kind == AssetKind.FIGURE:
        tag_name = "figure"
      elif block.kind == AssetKind.TABLE:
        tag_name = "table"
      elif block.kind == AssetKind.FORMULA:
        tag_name = "formula"

      buffer.write("<")
      buffer.write(tag_name)
      buffer.write("/>\n")

      if len(block.texts) > 0:
        buffer.write("<")
        buffer.write(tag_name)
        buffer.write("-caption>\n")

        self._write_texts(buffer, block.texts)

        buffer.write("</")
        buffer.write(tag_name)
        buffer.write("-caption>\n")

  def _write_texts(self, buffer: io.StringIO, texts: list[Text]):
    for text in texts:
      content = text.content.replace("\n", " ")
      content = escape(content.strip())
      buffer.write("<line confidence=")
      buffer.write("{:.2f}".format(text.rank))
      buffer.write(">")
      buffer.write(content)
      buffer.write("</line>\n")