import unittest

from pdf_craft.llm.escape import _process_xml_text


class TextLLMEscape(unittest.TestCase):
  def test_llm_escape(self):
    processed_text = _process_xml_text(
      text="""
      <root>
        <self-closing />
        Here is some text.
        <open-tag>Some content</open-tag>
        More text here.
        <another_self_closing attribute1="value1" attribute2="value2" />
        </close-tag>
      </root>""",
      process_func=lambda x: x.upper(),
    )
    self.assertEqual(
      first=processed_text,
      second="""
      <root>
        <self-closing />
        HERE IS SOME TEXT.
        <open-tag>SOME CONTENT</open-tag>
        MORE TEXT HERE.
        <another_self_closing attribute1="value1" attribute2="value2" />
        </close-tag>
      </root>""",
    )