import unittest

from io import StringIO
from xml.etree.ElementTree import tostring
from pdf_craft.xml.parser import parse_tags, Tag, TagKind
from pdf_craft.xml.decoder import decode


_WIKI_XML_DESCRIPTION = """
  一个tag属于标记结构，以<开头，以>结尾。Tag名字是大小写敏感，不能包括任何字符
  !"#$%&'()*+,/;<=>?@[\]^`{|}~， 也不能有空格符， 不能以"-"或"."或数字开始。
  可分为三：
    <1> 起始标签 start-tag，如<section>;
    <2> 结束标签 end-tag，如</section>;
    <3> 空白标签 empty-element tag，如<line-break/>.

  <response result="well-done" page-index="12">
    <section id="1-2"/>
    <fragment>hello world</fragment>
  </response>
"""

class TextXML(unittest.TestCase):
  def test_parse_tags(self):
    tags: list[str] = []
    watch_fragment = False
    fragment: str = ""
    cell_buffer = StringIO()

    for cell in parse_tags(_WIKI_XML_DESCRIPTION):
      if isinstance(cell, Tag):
        tags.append(str(cell))
        if cell.name == "fragment":
          watch_fragment = (cell.kind == TagKind.OPENING)
      elif watch_fragment:
        fragment += cell
      cell_buffer.write(str(cell))

    self.assertEqual(_WIKI_XML_DESCRIPTION, cell_buffer.getvalue())
    self.assertEqual(fragment, "hello world")
    self.assertListEqual(tags, [
      '<section>',
      '</section>',
      '<line-break/>',
      '<response result="well-done" page-index="12">',
      '<section id="1-2"/>',
      '<fragment>',
      '</fragment>',
      '</response>',
    ])

  def test_encode(self):
    encoded = list(decode(_WIKI_XML_DESCRIPTION, "response"))
    self.assertEqual(len(encoded), 1)
    response_text = tostring(encoded[0], encoding="unicode")
    self.assertEqual(
      first=response_text.strip(),
      second="""
  <response result="well-done" page-index="12">
    <section id="1-2" />
    <fragment>hello world</fragment>
  </response>
""".strip()
    )