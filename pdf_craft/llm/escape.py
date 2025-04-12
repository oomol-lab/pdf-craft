import re
import io

from typing import Callable
from html import unescape, escape


# LLM 的 XML 难以严格符合转义标准，处理它以让它能通过标准的 XML 来格式化
def normal_llm_response_xml(text: str):
  return _process_xml_text(text, lambda x: escape(unescape(x)))

# 一个简单标准的 XML TAG 表达式，以避免和各种复杂的转义规则冲突。
# LLM 只会生成这个简单标准的表达式，因此不符合的部分一定是正文，从而排除它们可能触碰转义规则的可能。
# 1. 所有的 TAG、属性名必须是字母、数字、下划线、中脊线
# 2. 所有的属性值必须是字母、数字、下划线、中脊线、英语逗号
# 3. 属性必须是双引号
_XML_TAG_PATTERN = re.compile(
  pattern=r'<([a-zA-Z0-9_\-]+)(?:\s+[a-zA-Z][a-zA-Z0-9_\-]*="[a-zA-Z0-9_,\-]*")*\s*/?>|</([a-zA-Z0-9_\-]+)>',
)

def _process_xml_text(text: str, process_func: Callable[[str], str]) -> str:
  buffer = io.StringIO()
  prev_end: int = 0

  for match in _XML_TAG_PATTERN.finditer(text):
    non_tag_part = text[prev_end:match.start()]
    if non_tag_part:
      buffer.write(process_func(non_tag_part))

    buffer.write(match.group())
    prev_end = match.end()

  return buffer.getvalue()