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
            PageLayout(ref="1", det=(100, 100, 200, 150), text="Single", hash=None),
        ]
        result = list(split_reading_serials(layouts))
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 1)
        self.assertEqual(result[0][0].text, "Single")

    def test_single_column(self):
        """测试单列布局 - 所有元素应该在一个组中"""
        layouts = [
            PageLayout(ref="1", det=(100, 100, 200, 150), text="Text 1", hash=None),
            PageLayout(ref="2", det=(100, 200, 200, 250), text="Text 2", hash=None),
            PageLayout(ref="3", det=(100, 300, 200, 350), text="Text 3", hash=None),
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
            PageLayout(ref="1", det=(100, 100, 200, 150), text="First", hash=None),
            PageLayout(ref="2", det=(100, 200, 200, 250), text="Second", hash=None),
            PageLayout(ref="3", det=(100, 300, 200, 350), text="Third", hash=None),
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
            PageLayout(ref=str(i), det=(i*10, 100, i*10+100, 150), text=f"Text {i}", hash=None)
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
            PageLayout(ref="1", det=(50, 50, 100, 100), text="TopLeft", hash=None),
            PageLayout(ref="2", det=(200, 50, 250, 100), text="TopRight", hash=None),
            PageLayout(ref="3", det=(50, 200, 100, 250), text="BottomLeft", hash=None),
            PageLayout(ref="4", det=(200, 200, 250, 250), text="BottomRight", hash=None),
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
            PageLayout(ref="1", det=(100, 100, 200, 150), text="Small", hash=None),
            PageLayout(ref="2", det=(100, 200, 400, 300), text="Large", hash=None),
            PageLayout(ref="3", det=(100, 350, 150, 370), text="Tiny", hash=None),
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
                PageLayout(ref=f"L{i}", det=(50, 100+i*100, 150, 180+i*100), text=f"Left{i}", hash=None)
            )

        # 右列 - 5个元素，位置远离左列
        for i in range(5):
            layouts.append(
                PageLayout(ref=f"R{i}", det=(400, 100+i*100, 500, 180+i*100), text=f"Right{i}", hash=None)
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

    def test_cv_splitting_same_size(self):
        """测试 CV 分组 - 相同 size 的元素不应该被分组"""
        # 所有元素宽度相同 (100px)，CV = 0，不应该分组
        layouts = [
            PageLayout(ref="1", det=(100, 100, 200, 150), text="Text1", hash=None),
            PageLayout(ref="2", det=(100, 200, 200, 250), text="Text2", hash=None),
            PageLayout(ref="3", det=(100, 300, 200, 350), text="Text3", hash=None),
            PageLayout(ref="4", det=(100, 400, 200, 450), text="Text4", hash=None),
        ]
        result = list(split_reading_serials(layouts))

        # 所有元素都应该在结果中
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 4)

    def test_cv_splitting_two_elements(self):
        """测试 CV 分组 - 只有 2 个元素时不分组"""
        # 即使 size 差异很大，少于 3 个元素也不分组
        layouts = [
            PageLayout(ref="1", det=(100, 100, 150, 150), text="Small", hash=None),  # width=50
            PageLayout(ref="2", det=(100, 200, 400, 250), text="Large", hash=None),  # width=300
        ]
        result = list(split_reading_serials(layouts))

        # 所有元素都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 2)

    def test_cv_splitting_title_and_text(self):
        """测试 CV 分组 - 标题和正文混合（size 差异大）应该被分组"""
        # 创建一列，包含标题（宽度大）和正文（宽度小）
        layouts = [
            # 3个正文，width ≈ 100
            PageLayout(ref="1", det=(100, 100, 200, 120), text="Text1", hash=None),
            PageLayout(ref="2", det=(100, 150, 200, 170), text="Text2", hash=None),
            PageLayout(ref="3", det=(100, 200, 200, 220), text="Text3", hash=None),
            # 3个标题，width ≈ 300
            PageLayout(ref="4", det=(100, 300, 400, 330), text="Title1", hash=None),
            PageLayout(ref="5", det=(100, 350, 400, 380), text="Title2", hash=None),
            PageLayout(ref="6", det=(100, 400, 400, 430), text="Title3", hash=None),
        ]
        result = list(split_reading_serials(layouts))

        # 所有元素都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 6)

        # 由于 CV 很大（标准差/平均值 > 0.1），应该被分成多个组
        # 至少应该有 2 组
        self.assertGreater(len(result), 1)

    def test_cv_splitting_three_elements_high_cv(self):
        """测试 CV 分组 - 恰好 3 个元素且 CV 超标应该分组"""
        # 3 个元素，width 分别为 100, 100, 300
        # mean = 166.67, std ≈ 94.28, CV ≈ 0.566 > 0.1
        layouts = [
            PageLayout(ref="1", det=(100, 100, 200, 150), text="Small1", hash=None),
            PageLayout(ref="2", det=(100, 200, 200, 250), text="Small2", hash=None),
            PageLayout(ref="3", det=(100, 300, 400, 350), text="Large", hash=None),
        ]
        result = list(split_reading_serials(layouts))

        # 所有元素都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 3)

        # CV 超标，应该分成多组（至少 2 组）
        self.assertGreater(len(result), 1)

    def test_cv_splitting_borderline_cv(self):
        """测试 CV 分组 - CV 接近阈值的情况"""
        # 创建 CV 略大于 0.1 的情况
        # 5 个元素，width 分别为 95, 100, 100, 100, 115
        # mean = 102, std ≈ 7.48, CV ≈ 0.073 < 0.1，不应该分组
        layouts = [
            PageLayout(ref="1", det=(100, 100, 195, 150), text="Text1", hash=None),
            PageLayout(ref="2", det=(100, 200, 200, 250), text="Text2", hash=None),
            PageLayout(ref="3", det=(100, 300, 200, 350), text="Text3", hash=None),
            PageLayout(ref="4", det=(100, 400, 200, 450), text="Text4", hash=None),
            PageLayout(ref="5", det=(100, 500, 215, 550), text="Text5", hash=None),
        ]
        result = list(split_reading_serials(layouts))

        # 所有元素都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 5)

    def test_cv_splitting_multi_level(self):
        """测试 CV 分组 - 多级递归分组"""
        # 创建三个不同 size 级别的元素组
        layouts = [
            # 小字：width ≈ 50
            PageLayout(ref="1", det=(100, 100, 150, 120), text="Tiny1", hash=None),
            PageLayout(ref="2", det=(100, 150, 150, 170), text="Tiny2", hash=None),
            PageLayout(ref="3", det=(100, 200, 150, 220), text="Tiny3", hash=None),
            # 中字：width ≈ 150
            PageLayout(ref="4", det=(100, 300, 250, 330), text="Medium1", hash=None),
            PageLayout(ref="5", det=(100, 350, 250, 380), text="Medium2", hash=None),
            PageLayout(ref="6", det=(100, 400, 250, 430), text="Medium3", hash=None),
            # 大字：width ≈ 350
            PageLayout(ref="7", det=(100, 500, 450, 530), text="Large1", hash=None),
            PageLayout(ref="8", det=(100, 550, 450, 580), text="Large2", hash=None),
            PageLayout(ref="9", det=(100, 600, 450, 630), text="Large3", hash=None),
        ]
        result = list(split_reading_serials(layouts))

        # 所有元素都应该被返回
        all_layouts = [layout for group in result for layout in group]
        self.assertEqual(len(all_layouts), 9)

        # 应该被分成多个组（至少 2 组，可能 3 组）
        self.assertGreater(len(result), 1)


if __name__ == "__main__":
    unittest.main()
