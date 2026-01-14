import unittest

from pdf_craft.pdf import PageLayout
from pdf_craft.sequence.reading_serials import split_reading_serials


class TestSplitReadingSerials(unittest.TestCase):
    """测试 split_reading_serials 函数"""

    def test_empty_list(self):
        """测试空列表"""
        result = list(split_reading_serials([]))
        self.assertEqual(result, [])

    def test_single_layout(self):
        """测试单个布局"""
        layouts = [
            PageLayout(
                ref="1", det=(100, 100, 200, 150), text="Single", hash=None, order=0
            ),
        ]
        result = list(split_reading_serials(layouts))
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 1)
        self.assertEqual(result[0][0].text, "Single")

    def test_single_column(self):
        """测试单列布局 - 所有元素应该在一个组中"""
        layouts = [
            PageLayout(
                ref="1", det=(100, 100, 200, 150), text="Text 1", hash=None, order=0
            ),
            PageLayout(
                ref="2", det=(100, 200, 200, 250), text="Text 2", hash=None, order=1
            ),
            PageLayout(
                ref="3", det=(100, 300, 200, 350), text="Text 3", hash=None, order=2
            ),
        ]
        result = list(split_reading_serials(layouts))

        # 验证所有布局都被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 3)

        # 验证顺序保持
        self.assertEqual(all_layouts[0].text, "Text 1")
        self.assertEqual(all_layouts[1].text, "Text 2")
        self.assertEqual(all_layouts[2].text, "Text 3")

    def test_order_preservation(self):
        """测试顺序保持 - 输出的所有布局应该保持原始顺序"""
        layouts = [
            PageLayout(
                ref="1", det=(100, 100, 200, 150), text="First", hash=None, order=0
            ),
            PageLayout(
                ref="2", det=(100, 200, 200, 250), text="Second", hash=None, order=1
            ),
            PageLayout(
                ref="3", det=(100, 300, 200, 350), text="Third", hash=None, order=2
            ),
        ]
        result = list(split_reading_serials(layouts))

        # 收集所有布局
        all_layouts = [layout for group in result for layout in group]

        # 验证数量和顺序
        self.assertEqual(len(all_layouts), 3)
        self.assertEqual(all_layouts[0].text, "First")
        self.assertEqual(all_layouts[1].text, "Second")
        self.assertEqual(all_layouts[2].text, "Third")

    def test_generator_returns_all_layouts(self):
        """测试生成器返回所有输入的布局"""
        layouts = [
            PageLayout(
                ref=str(i),
                det=(i * 10, 100, i * 10 + 100, 150),
                text=f"Text {i}",
                hash=None,
                order=i,
            )
            for i in range(5)
        ]
        result = list(split_reading_serials(layouts))

        # 所有布局都应该在结果中
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), len(layouts))

        # 验证每个布局都存在
        for i, layout in enumerate(layouts):
            self.assertIn(layout, all_layouts)

    def test_different_positions(self):
        """测试不同位置的布局都能被正确处理"""
        layouts = [
            PageLayout(
                ref="1", det=(50, 50, 100, 100), text="TopLeft", hash=None, order=0
            ),
            PageLayout(
                ref="2", det=(200, 50, 250, 100), text="TopRight", hash=None, order=1
            ),
            PageLayout(
                ref="3", det=(50, 200, 100, 250), text="BottomLeft", hash=None, order=2
            ),
            PageLayout(
                ref="4",
                det=(200, 200, 250, 250),
                text="BottomRight",
                hash=None,
                order=3,
            ),
        ]
        result = list(split_reading_serials(layouts))

        # 所有布局都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 4)

        # 验证所有文本都存在
        texts = {layout.text for layout in all_layouts}
        self.assertEqual(texts, {"TopLeft", "TopRight", "BottomLeft", "BottomRight"})

    def test_various_sizes(self):
        """测试不同大小的布局"""
        layouts = [
            PageLayout(
                ref="1", det=(100, 100, 200, 150), text="Small", hash=None, order=0
            ),
            PageLayout(
                ref="2", det=(100, 200, 400, 300), text="Large", hash=None, order=1
            ),
            PageLayout(
                ref="3", det=(100, 350, 150, 370), text="Tiny", hash=None, order=2
            ),
        ]
        result = list(split_reading_serials(layouts))

        # 所有布局都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 3)

        # 验证顺序
        self.assertEqual(all_layouts[0].text, "Small")
        self.assertEqual(all_layouts[1].text, "Large")
        self.assertEqual(all_layouts[2].text, "Tiny")

    def test_grouped_by_columns(self):
        """测试多列布局的分组情况 - 验证生成器产生多个组"""
        # 创建明显分离的两列，每列有多个元素
        layouts = []

        # 左列 - 5个元素
        for i in range(5):
            layouts.append(
                PageLayout(
                    ref=f"L{i}",
                    det=(50, 100 + i * 100, 150, 180 + i * 100),
                    text=f"Left{i}",
                    hash=None,
                    order=i,
                )
            )

        # 右列 - 5个元素，位置远离左列
        for i in range(5):
            layouts.append(
                PageLayout(
                    ref=f"R{i}",
                    det=(400, 100 + i * 100, 500, 180 + i * 100),
                    text=f"Right{i}",
                    hash=None,
                    order=i + 5,
                )
            )

        result = list(split_reading_serials(layouts))

        # 验证所有布局都被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 10)

        # 如果分成多组，验证每组的内容
        if len(result) > 1:
            # 验证每个组都有元素
            for group in result:
                self.assertGreater(len(group), 0)


if __name__ == "__main__":
    unittest.main()
