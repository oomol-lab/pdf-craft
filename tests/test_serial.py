import os
import unittest

from typing import cast, Any
from xml.etree.ElementTree import tostring, Element
from pdf_craft.analyser.serial import serials
from pdf_craft.analyser.llm import LLM


class TextSerial(unittest.TestCase):
  def test_spread_page_text(self):
    chunks_path = os.path.join(__file__, "..", "serial_chunks", "POUR MARX")
    chunks_path = os.path.abspath(chunks_path)
    serial1, serial2 = list(serials(
      llm=_fake_llm(),
      index=None,
      chunks_path=chunks_path,
    ))
    self.assertListEqual(
      [tostring(e, encoding="unicode") for e in serial1.main_texts],
      [(
        '<headline>关于“真正人道主义”的补记</headline>'
      ), (
        '<text>我这里想简单谈谈“真正人道主义”<ref id="1"/>一词。</text>'
      ), (
        '<text>1.首先，黑格尔把产生科学认识的工作当成了“具体本身（实在）的产生过程”。'
        '不过，他只是在第二个问题上又有了混淆，才陷入了这种“幻觉”之中。</text>'
        '<text>2.他把在认识过程开始时出现的普遍概念（例如：《逻辑学》中的普遍性概'
        '念和“存在”概念）当成了这一过程的本质和动力，当作“自我产生着的概念”；'
        '<ref id="2" />他把将被理论实践加工为认识（“一般丙”）的“一般甲”当成了'
        '加工过程的本质和动力！如果从另一种实践那儿借用一个例子来作比较，<ref id="3" />'
        '这就等于说，是煤炭通过它的辩证的自我发展，产生出蒸汽机、工厂以及其他非凡的技术设备、'
        '传动设备、物理设备、化学设备、电器设备等，这些设备今天又使煤的开采和煤的无数变革成为'
        '可能！黑格尔之所以陷入这种幻觉，正是因为他把有关普遍性以及它的作用和意义的意识形态观点'
        '强加于理论实践的现实。然而，在实践的辩证法中，开始的抽象一般（“一般甲”），即被加工的'
        '一般，不同于进行加工的一般（“一般乙”），更不同于作为加工产物的具体一般（“一般丙”），即'
        '认识（“具体的理论”）。进行加工的“一般乙”完全不是被加工的“一般甲”从自在向自为的简单发展，'
        '不是前者向后者的过渡（不论这种过渡何等复杂）；因为“一般乙”是特定科学的“理论”，而作为一种'
        '“理论”，它是全过程的结果（从科学创立起的全部科学史），它是一个真正的演变过程，而不是一个普通'
        '的发展过程（例如像黑格尔所说的从自在到自为的发展过程），它在形式上表现为能够引起真正质的中断的'
        '突变和改组。因此，“一般乙”对“一般甲”进行的加工，无论在科学的创建时期，或在科学史随后的阶段中，'
        '都绝不是“一般乙”对自己的加工。在“一般甲”被加工后，它总是产生了真正的变革。虽然“一般甲”'
        '还保留一般的“形式”，但这种形式不能说明任何问题，因为它已经变成了另一种一般，这后一种一般不再'
        '是意识形态的一般，也不是属于科学的过去阶段的一般，而是在质的方面已经焕然一新的具体的科学'
        '一般。</text>'
      )],
    )
    self.assertListEqual(
      [tostring(e, encoding="unicode") for e in serial2.main_texts],
      [(
        '<text> 思辨通过抽象颠倒了事物的顺序，把抽象概念的自生过程当成了具体实在的自生过程。马克思在'
        '《神圣家族》中对此作了清楚的解释，<ref id="1" />指出了在黑格尔的思辨哲学中，水果的抽象如何'
        '通过它的自生自长运动而产生出梨、葡萄和黄香李……费尔巴哈则于1839年已在他对黑格尔的“具体普遍性”'
        '的卓越批判中作出了更好的阐述和批判。</text>'
      )],
    )
    # TODO: test citations

class _FakeLLM:
  def request(self, template_name: str, xml_data: Element, params: dict[str, Any]) -> str:
    raise AssertionError("Should not be called")

def _fake_llm() -> LLM:
  return cast(LLM, _FakeLLM())