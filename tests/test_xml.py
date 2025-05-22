import unittest

from io import StringIO
from xml.etree.ElementTree import tostring, Element
from pdf_craft.xml.tag import Tag, TagKind
from pdf_craft.xml.parser import parse_tags
from pdf_craft.xml import decode_friendly, encode_friendly


# pylint: disable=W1401
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
# pylint: enable=W1401

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

  def test_decode(self):
    encoded = list(decode_friendly(_WIKI_XML_DESCRIPTION, "response"))
    self.assertEqual(len(encoded), 1)
    response_text = tostring(encoded[0], encoding="unicode")
    expected_text = """
  <response result="well-done" page-index="12">
    <section id="1-2" />
    <fragment>hello world</fragment>
  </response>
    """
    self.assertEqual(
      first=response_text.strip(),
      second=expected_text.strip()
    )

  def test_encode(self):
    root = Element("response", {
      "foobar": "hello",
      "apple": "OSX",
    })
    root.text = "\n1 + 1 < 3\n"
    section1 = Element("section", {
      "id": "10-20",
    })
    section1.tail = "\ncheck <html id=\"110\"> ...<1> foobar</html>\n"
    section2 = Element("section", {
      "id": "25-30",
      "name": "latest_section"
    })
    section2.tail = "\n"
    root.extend((section1, section2))
    root.tail = "\ncannot encode content...\n"
    expected_text = """
  <response apple="OSX" foobar="hello">
  1 + 1 < 3
  <section id="10-20"/>
  check &lt;html id=&quot;110&quot;&gt; ...<1> foobar&lt;/html&gt;
  <section id="25-30" name="latest_section"/>
</response>
    """
    self.assertEqual(
      first=encode_friendly(root).strip(),
      second=expected_text.strip()
    )