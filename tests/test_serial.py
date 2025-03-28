import os
import unittest

from typing import cast, Any
from xml.etree.ElementTree import tostring, Element
from pdf_craft.llm import LLM
from pdf_craft.analyser.serial import serials, Serial
from pdf_craft.analyser.utils import normalize_xml_text, search_xml_children


class TextSerial(unittest.TestCase):
  def test_spread_page_text(self):
    self.maxDiff = 12000
    chunks_path = os.path.join(__file__, "..", "serial_chunks", "POUR MARX")
    chunks_path = os.path.abspath(chunks_path)
    serial1, serial2 = list(serials(
      llm=_fake_llm(),
      chunks_path=chunks_path,
    ))
    self.assertListEqual(
      list(_parse_main_texts(serial1)),
      [(
        '<headline>关于“真正人道主义”的补记</headline>'
      ), (
        '<text>我这里想简单谈谈“真正人道主义”<ref id="1" />一词。</text>'
      ), (
        '<text>1.首先，黑格尔把产生科学认识的工作当成了“具体本身（实在）的产生过程”。'
        '不过，他只是在第二个问题上又有了混淆，才陷入了这种“幻觉”之中。</text>'
      ), (
        '<text>2.他把在认识过程开始时出现的普遍概念（例如：《逻辑学》中的普遍性概'
        '念和“存在”概念）当成了这一过程的本质和动力，当作“自我产生着的概念”；'
        '<ref id="4" />他把将被理论实践加工为认识（“一般丙”）的“一般甲”当成了'
        '加工过程的本质和动力！如果从另一种实践那儿借用一个例子来作比较，<ref id="2" /> '
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
      list(_parse_citations(serial1)),
      [
        (1, "(1)", [
          '<text>“真正人道主义”的概念是J.桑普汉在《光明》报58期发表的一篇文章（参见《新评论》杂志1965年'
          '3月164期）的基本论据，也是从马克思青年时期著作中借用的一个概念。</text>',
        ]),
        (2, "(24)", [
          '<text>这种比较是有根据的，因为这两种不同的实践都具有实践的一般本质。</text>',
        ]),
        (4, "(23)", [
          '<text>马克思：《政治经济学批判导言》，见《马克思恩格斯选集》中文版第二卷第104页。</text>',
        ]),
      ],
    )
    self.assertListEqual(
      list(_parse_main_texts(serial2)),
      [(
        '<text> 思辨通过抽象颠倒了事物的顺序，把抽象概念的自生过程当成了具体实在的自生过程。马克思在'
        '《神圣家族》中对此作了清楚的解释，<ref id="3" /> 指出了在黑格尔的思辨哲学中，水果的抽象如何'
        '通过它的自生自长运动而产生出梨、葡萄和黄香李……费尔巴哈则于1839年已在他对黑格尔的“具体普遍性”'
        '的卓越批判中作出了更好的阐述和批判。 </text>'
      )],
    )
    self.assertListEqual(
      list(_parse_citations(serial2)),
      [
        (3, "(25)", [
          '<text>《神圣家族》写于1844年。</text>',
          '<figure hash="FOOBAR">《德意志意识形态》（1845）和《哲学的贫困》（1847）再次谈到这个问题。</figure>',
        ]),
      ],
    )

  def test_spread_2_pages_text(self):
    self.maxDiff = 12000
    chunks_path = os.path.join(__file__, "..", "serial_chunks", "Der Witz und seine Beziehung zum")
    chunks_path = os.path.abspath(chunks_path)

    serials_list = list(serials(
      llm=_fake_llm(),
      chunks_path=chunks_path,
    ))
    # serial2 will be skipped
    self.assertEqual(len(serials_list), 2)
    serial1, serial3 = serials_list

    self.assertListEqual(
      list(_parse_main_texts(serial1)),
      [(
        '<headline>诙谐及其与潜意识的关系</headline>'
      ), (
        '<text> 然而，我们觉得有必要修正斯宾塞的这种观点，这在某种程度上是为了给其观点中的某些思想下一个更为确切的定义，'
        '同时也是为了改变它们。我们应该说，如果先前为特殊精神道路的贯注所运用的那些心理能量的配额变得毫无用处，以致于它可以'
        '自由地释放时，笑才会出现。我们都知道，做出这种假设会招致什么样的“憎恶的面孔”；但为了捍卫自己，我们将冒险引用'
        '李普斯的专著《滑稽与幽默》（1898，第71页）中的一句很贴切的话，从该书中我们可以得到除滑稽和幽默以外的许多问题的'
        '启示。</text>'
      ), (
        '<text> 他说：“最后，特殊的心理学问题总是不偏不倚地引导我们深入到心理学中去，因此，从根本上说，人们不能孤立地'
        '处理任何心理学问题。”自从我开始从哲学的角度对心理病理学中的事实加以整理时起，就已习惯于使用“心理能量”、“释放”'
        '这些术语，以及把心理能量当做一种数量来处理。在《释梦》（1900a）里，我曾试图（和李普斯一样）证实“心理上真正有'
        '效的（really psychically elective）东西本身就是潜意识的心理过程，而不是意识的内容。<ref id="1" /> 只有'
        '当我谈到“心理途径的贯注“（"cathexis of psychical paths”）时，我似乎才开始背离李普斯所通常使用的那些类比。我'
        '的经验是，心理能量可以沿着某些联想途径进行移置，以及心理过程的种种痕迹不仅是坚不可摧的，而且还是持久的，这些经验'
        '实际上已经向我暗示，我可以采用某种类似的方法来描绘那些未知的东西。为了避免产生误解，我必须再补充一点，我现在并不是'
        '想公开声明，细胞和神经纤维或者目前已有了自己地位的神经系统就是这些心理途径<ref id="2" />，即使这些途径可以用'
        '至今仍无法说明的某种方式以神经系统的有机元素来表示。</text>'
      )],
    )
    self.assertListEqual(
      list(_parse_citations(serial1)),
      [
        (1, "(6)", [
          '<text> 参阅上面引用的李普斯的那本书中的第八章《论心力》的某些段落。他在书中说：“因此，下面这个概述仍然有用：'
          '心理生活的种种因素并不是意识的内容，其本身就是潜意识的心理过程。倘若心理学的任务并不只是希望叙述意识的内容，那么'
          '它就必须从这些意识内容的特征及其瞬息的关系中推断出这些潜意识过程的实质。心理学必须是这些过程的一种理论。而这样的'
          '一种心理学很快会发现，这些过程还具有许多并不是由种种相应的意识内容所表现出来的特点。”（李普斯，出处同前，第123~'
          '124页）。亦参见我的《释梦》的第七章［标准版，第5卷，第611~614页］。</text>',
        ]),
        (2, "(7)", [
          '<text> ［大约10年前，弗洛伊德在他死后才出版的《科学心理学设计》（1950a）一书中曾煞费苦心地试图确切证明这一问题，'
          '但没有成功。］</text>',
        ]),
      ],
    )
    self.assertListEqual(
      list(_parse_main_texts(serial3)),
      [(
        '<text> 因此，根据我们的假设，在笑的过程中，仍然存在着允许迄今为止用于贯注的心理能量自由释放的种种条件。但是，由于'
        '笑——的确，不是所有的笑，但诙谐的笑却是肯定的——是一种快乐的象征，所以我们倾向于把这种快乐与先前所存在着的贯注的解除'
        '联系起来。</text>'
      ), (
        '<text>我们已经做过这样的猜测，而且我们以后将会看得更清楚，在分散注意力的条件下，我们已经在诙谐听者身上发现了一个'
        '极为重要的心理过程的特征。<ref id="3" /> 关于这一点，我们仍然可以了解许多其他的东西。</text>'
      )],
    )
    self.assertListEqual(
      list(_parse_citations(serial3)),
      [
        (3, "(10)", [
          '<text> ［后来弗洛伊德指出，分散注意力的方法也是催眠暗示中常用的一种技巧。参看《群体心理学与自我的分析》（1921c）'
          '中的第十章。标准版，第18卷，第126页。在他死后发表的关于《精神分析与心灵感应》（1914d［1921］，同上，第184页）一文'
          '中，他也发表了自己的看法，即在某些读心症（thought reading）术的事例中，也是同一过程在起作用。在弗洛伊德从技巧上对'
          '《癔症研究》（1895年）（同上。第2卷，第271页）所做的技术贡献中，在他对自己的“压力”（pressure）技巧机制所做的解释'
          '中，我们可能发现他曾经朦朦胧胧地提及过这种方法。］</text>',
        ]),
      ],
    )

def _parse_main_texts(serial: Serial):
  for element in serial.main_texts:
    yield normalize_xml_text(tostring(element, encoding="unicode"))

def _parse_citations(serial: Serial):
  ids: list[int] = []
  for element in serial.main_texts:
    for child, _ in search_xml_children(element):
      if child.tag != "ref":
        continue
      id = int(child.get("id"))
      ids.append(id)

  for id in sorted(ids):
    citation = serial.citations.get(id)
    yield id, citation.label, [
      normalize_xml_text(tostring(e, encoding="unicode"))
      for e in citation.content
    ]

class _FakeLLM:
  def request(self, template_name: str, xml_data: Element, params: dict[str, Any]) -> str:
    raise AssertionError("Should not be called")

def _fake_llm() -> LLM:
  return cast(LLM, _FakeLLM())