import unittest

from typing import Iterable
from xml.etree.ElementTree import Element
from pdf_craft.analyser.index import parse_index


class TestGroup(unittest.TestCase):
  def test_identify_step_by_step(self):
    index = parse_index(
      start_page_index=0,
      end_page_index=0,
      root=_index_xml(
        prefaces=(
          _xml("引言", ()),
          _xml("序言", ()),
        ),
        chapters=(
          _xml("第一章 自成一体的农业起源", (
            _xml("考古发现所展示的原始农业面貌", ()),
            _xml("关于中国农业起源的若干问题", ()),
          )),
          _xml("第二章 悠悠千古话沟", (
            _xml("青铜农具与耒耜", ()),
            _xml("以农田沟洫为特征的农业体系", ()),
            _xml("五谷、六畜及其他", ()),
            _xml("后记", ()),
          )),
          _xml("第三章 铁器牛耕谱新篇", (
            _xml("战国、秦汉、魏晋南北朝农业", ()),
            _xml("在传统农具领域发生的革命", ()),
            _xml("大规模农田灌溉工程的兴建和农区的扩展", ()),
            _xml("后记", ()),
          )),
          _xml("第四章 在人口膨胀压力下继续发展", (
            _xml("精耕细作农业技术体系的形成", ()),
            _xml("农业生产全方位的发展", ()),
            _xml("从华夷杂处到农牧分区", ()),
            _xml("农业优势的南北易位", ()),
            _xml("后记", ()),
          )),
        ),
      )
    )
    self.assertIsNotNone(index)
    self.assertEqual(index.markdown, "\n".join((
      "### Prefaces",
      "",
      "* 引言",
      "* 序言",
      "",
      "### Chapters",
      "",
      "* 第一章 自成一体的农业起源",
      "  + 考古发现所展示的原始农业面貌",
      "  + 关于中国农业起源的若干问题",
      "* 第二章 悠悠千古话沟",
      "  + 青铜农具与耒耜",
      "  + 以农田沟洫为特征的农业体系",
      "  + 五谷、六畜及其他",
      "  + 后记",
      "* 第三章 铁器牛耕谱新篇",
      "  + 战国、秦汉、魏晋南北朝农业",
      "  + 在传统农具领域发生的革命",
      "  + 大规模农田灌溉工程的兴建和农区的扩展",
      "  + 后记",
      "* 第四章 在人口膨胀压力下继续发展",
      "  + 精耕细作农业技术体系的形成",
      "  + 农业生产全方位的发展",
      "  + 从华夷杂处到农牧分区",
      "  + 农业优势的南北易位",
      "  + 后记",
      "",
    )))
    first_chapter = index.identify_chapter("引言", 1)
    self.assertIsNotNone(first_chapter)

    chapter = index.identify_chapter("第二章 悠悠千古话沟", 1)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, "第二章 悠悠千古话沟")
    self.assertEqual(chapter.id, 7)

    chapter = index.identify_chapter("五谷、六畜及其他", 2)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, "五谷、六畜及其他")
    self.assertEqual(chapter.id, 10)

    chapter = index.identify_chapter("后记", 2)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, second="后记")
    self.assertEqual(chapter.id, 11)

    chapter = index.identify_chapter("第四章 在人口膨胀压力下继续发展", 1)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, second="第四章 在人口膨胀压力下继续发展")
    self.assertEqual(chapter.id, 17)

    chapter = index.identify_chapter("后记", 2)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, second="后记")
    self.assertEqual(chapter.id, 22)

    index.reset_stack_with_chapter(first_chapter)

    chapter = index.identify_chapter("后记", 2)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, second="后记")
    self.assertEqual(chapter.id, 11)

    chapter = index.identify_chapter("后记", 2)
    self.assertIsNotNone(chapter)
    self.assertEqual(chapter.headline, second="后记")
    self.assertEqual(chapter.id, 16)

def _index_xml(prefaces: Iterable[Element], chapters: Iterable[Element]) -> Element:
  index_xml = Element("index")
  if len(prefaces) > 0:
    prefaces_xml = Element("prefaces")
    for preface in prefaces:
      prefaces_xml.append(preface)
    index_xml.append(prefaces_xml)
  if len(chapters) > 0:
    chapters_xml = Element("chapters")
    for chapter in chapters:
      chapters_xml.append(chapter)
    index_xml.append(chapters_xml)
  return index_xml

def _xml(headline: str, children: Iterable[Element]) -> Element:
  chapter_xml = Element("chapter")
  headline_xml = Element("headline")
  headline_xml.text = headline
  page_xml = Element("page")
  page_xml.text = "1"
  children_xml = Element("children")
  for child in children:
    children_xml.append(child)
  chapter_xml.append(headline_xml)
  chapter_xml.append(page_xml)
  chapter_xml.append(children_xml)
  return chapter_xml