import unittest

from xml.etree.ElementTree import tostring, Element
from resource_segmentation import Group, Segment, Resource, Incision
from pdf_craft.llm import LLM
from pdf_craft.analyser.common import PageRef
from pdf_craft.analyser.page_clipper import get_and_clip_pages, PageXML


class TestPageClipper(unittest.TestCase):
  def test_clip_segments(self):
    self.maxDiff = 4096
    pages = _Pages([
      _tag("page", {}, [
        _tag("headline", {}, [
          "Lamian War",
          "Main article: Lamian War",
        ]),
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "The news of Alexander's death inspired a revolt in Greece, known as the",
          "Lamian War. Athens and other cities formed a coalition and besieged",
          "Antipater in the fortress of Lamia, however, Antipater was relieved by a",
          "force sent by Leonnatus, who was killed in battle. The Athenians were",
          "defeated at the Battle of Crannon on September 5, 322 BC, by Craterus and",
          "his fleet.",
        ]),
      ]),
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "At this time, Peithon suppressed a revolt of Greek settlers in the eastern",
          "parts of the empire, and Perdiccas and Eumenes subdued Cappadocia.",
        ]),
      ]),
      _tag("page", {}, [
        _tag("headline", {}, [
          _line("First War of the Diadochi, 321–319 BC")
        ]),
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "Perdiccas, who was already betrothed to the daughter of Antipater,",
          "attempted to marry Alexander's sister, Cleopatra, a marriage which would",
          "have given him claim to the Macedonian throne. In 322 BC, Antipater,",
          "power. Soon after, Antipater would send his army, under the command of",
        ]),
      ]),
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "Craterus, into Asia Minor. In late 322 or early 321 BC, Ptolemy stole",
          "Alexander's body on its way to Macedonia and then joined the coalition.",
        ]),
      ]),
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "A force under Eumenes defeated Craterus at the battle of the Hellespont, however,",
          "Perdiccas was soon after murdered by his own generals Peithon, Seleucus, and Antigenes",
          "during his invasion of Egypt, after a failed attempt to cross the Nile.",
        ]),
      ]),
    ])
    group = Group(
      head_remain_count=122,
      tail_remain_count=68,
      head=[pages.segment([0, 1])],
      body=[pages.text_info(2)],
      tail=[pages.segment([3, 4])],
    )
    elements, remain_head, remain_tail = _split_page_xml_list(
      get_and_clip_pages(pages.llm, group, pages.get_element),
    )
    self.assertEqual(remain_head, 2)
    self.assertEqual(remain_tail, 1)
    self.assertElementsEqual(elements, [
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "defeated at the Battle of Crannon on September 5, 322 BC, by Craterus and",
          "his fleet.",
        ]),
      ]),
      pages.get_element(1),
      pages.get_element(2),
      pages.get_element(3),
    ])

  def test_clip_texts(self):
    self.maxDiff = 4096
    pages = _Pages([
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "He also sent his nephew Ptolemaios with an army through Cappadocia to the Hellespont to cut",
          "Asander off from Lysimachus and Cassander. Polemaios was successful, securing the northwest",
          "of Asia Minor for Antigonus, even invading Ionia/Lydia and bottling",
          "up Asander in Caria, but he was unable to drive his opponent from his satrapy.",
        ]),
      ]),
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "Eventually Antigonus decided to campaign against Asander himself, leaving ",
          "his oldest son Demetrius to protect Syria and Phoenica against Ptolemy. Ptolemy",
          "and Seleucus invaded from Egypt and defeated Demetrius in the Battle of Gaza.",
          "After the battle, Seleucus went east and secured control of Babylon (his old satrapy),",
        ]),
      ]),
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "and then went on to secure the eastern satrapies of Alexander's empire.",
          "Antigonus, having defeated Asander, sent his nephews Telesphorus and Polemaios",
          "to Greece to fight Cassander, he himself returned to Syria/Phoenica,",
          "drove off Ptolemy, and sent Demetrius east to take care of Seleucus.",
        ]),
      ]),
    ])
    group = Group(
      head_remain_count=57,
      tail_remain_count=55,
      head=[pages.text_info(0)],
      body=[pages.text_info(1)],
      tail=[pages.text_info(2)],
    )
    elements, remain_head, remain_tail = _split_page_xml_list(
      get_and_clip_pages(pages.llm, group, pages.get_element),
    )
    self.assertEqual(remain_head, 1)
    self.assertEqual(remain_tail, 1)
    self.assertElementsEqual(elements, [
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          " invading Ionia/Lydia and bottling",
          "up Asander in Caria, but he was unable to drive his opponent from his satrapy.",
        ]),
      ]),
      pages.get_element(1),
      _tag("page", {}, [
        _tag("text", {"start-incision": "uncertain", "end-incision": "uncertain"}, [
          "and then went on to secure the eastern satrapies of Alexander's empire.",
          "Antigonus, having defeated Asander, sent his",
        ]),
      ]),
    ])

  def assertElementsEqual(self, elements1: list[Element], elements2: list[Element]):
    self.assertEqual(len(elements1), len(elements2), "Lengths are not equal")
    for i, (element1, element2) in enumerate(zip(elements1, elements2)):
      self.assertElementEqual(element1, element2, f"Element {i} is not equal")

  def assertElementEqual(self, element1: Element, element2: Element, msg: str | None = None):
    text1 = tostring(element1, encoding="unicode")
    text2 = tostring(element2, encoding="unicode")
    return self.assertEqual(text1, text2, msg)

class _Pages:
  def __init__(self, elements: list[Element]):
    self._elements: list[Element] = elements
    self.llm: LLM = LLM(
      token_encoding="o200k_base",
      key="never touch",
      url="never touch",
      model="never touch",
    )

  def get_element(self, page_index: int) -> Element:
    return self._elements[page_index]

  def text_info(self, page_index: int) -> Resource[PageRef]:
    element = self._elements[page_index]
    element_text = tostring(element, encoding="unicode")
    tokens = self.llm.count_tokens_count(element_text)
    return Resource(
      count=tokens,
      start_incision=Incision.UNCERTAIN,
      end_incision=Incision.UNCERTAIN,
      payload=PageRef(page_index),
    )

  def segment(self, page_indexes: list[int]) -> Segment[PageRef]:
    text_infos = [self.text_info(i) for i in page_indexes]
    return Segment(
      count=sum(info.count for info in text_infos),
      resources=text_infos,
    )

def _split_page_xml_list(page_xml_list: list[PageXML]) -> tuple[list[Element], int, int]:
  page_xmls, head_count, tail_count = [], 0, 0
  found_body = False
  for page_xml in page_xml_list:
    page_xmls.append(page_xml.xml)
    if not page_xml.is_gap:
      found_body = True
    elif found_body:
      tail_count += 1
    else:
      head_count += 1
  return page_xmls, head_count, tail_count

def _tag(tag_name: str, attr: dict[str, str], children: list[Element]) -> Element:
  element = Element(tag_name, attr)
  for child in children:
    if isinstance(child, str):
      child = _line(child)
    element.append(child)
  return element

def _line(content: str) -> Element:
  line_tag = Element("line")
  line_tag.text = content
  return line_tag