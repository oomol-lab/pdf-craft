import unittest

from pdf_craft.common.cv_splitter import split_by_cv


class TestSplitByCV(unittest.TestCase):
    """测试 split_by_cv 函数"""

    def test_empty_list(self):
        """测试空列表"""
        result = split_by_cv([], max_cv=0.1)
        self.assertEqual(result, [[]])

    def test_single_element(self):
        """测试单个元素"""
        result = split_by_cv([(100.0, "A")], max_cv=0.1)
        self.assertEqual(result, [["A"]])

    def test_two_elements(self):
        """测试两个元素 - 不应该分组（无论 CV 多大）"""
        # 即使 size 差异很大，少于 3 个元素也不分组
        result = split_by_cv([(50.0, "Small"), (300.0, "Large")], max_cv=0.1)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]), {"Small", "Large"})

    def test_same_size_no_split(self):
        """测试相同 size 的元素不应该被分组"""
        # 所有元素 size 相同，CV = 0，不应该分组
        pairs = [(100.0, f"Text{i}") for i in range(4)]
        result = split_by_cv(pairs, max_cv=0.1)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 4)

    def test_low_cv_no_split(self):
        """测试 CV 低于阈值不分组"""
        # CV 略小于阈值，不应该分组
        # sizes: 95, 100, 100, 100, 115
        # mean = 102, std ≈ 7.48, CV ≈ 0.073 < 0.1
        pairs = [
            (95.0, "Text1"),
            (100.0, "Text2"),
            (100.0, "Text3"),
            (100.0, "Text4"),
            (115.0, "Text5"),
        ]
        result = split_by_cv(pairs, max_cv=0.1)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 5)

    def test_high_cv_split_simple(self):
        """测试 CV 超过阈值应该分组 - 简单情况"""
        # sizes: 100, 100, 300
        # mean = 166.67, std ≈ 94.28, CV ≈ 0.566 > 0.1
        pairs = [
            (100.0, "Small1"),
            (100.0, "Small2"),
            (300.0, "Large"),
        ]
        result = split_by_cv(pairs, max_cv=0.1)

        # 应该分成 2 组
        self.assertEqual(len(result), 2)

        # 所有元素都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(set(all_items), {"Small1", "Small2", "Large"})

    def test_high_cv_split_multiple_levels(self):
        """测试多级递归分组"""
        # 三个不同 size 级别：50, 150, 350
        pairs = [
            (50.0, "Tiny1"),
            (50.0, "Tiny2"),
            (50.0, "Tiny3"),
            (150.0, "Medium1"),
            (150.0, "Medium2"),
            (150.0, "Medium3"),
            (350.0, "Large1"),
            (350.0, "Large2"),
            (350.0, "Large3"),
        ]
        result = split_by_cv(pairs, max_cv=0.1)

        # 应该被分成多个组
        self.assertGreater(len(result), 1)

        # 所有元素都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(len(all_items), 9)

    def test_max_groups_limit(self):
        """测试 max_groups 参数限制组数"""
        # 三个不同 size 级别，但限制最多 2 组
        pairs = [
            (50.0, "Tiny1"),
            (50.0, "Tiny2"),
            (50.0, "Tiny3"),
            (150.0, "Medium1"),
            (150.0, "Medium2"),
            (150.0, "Medium3"),
            (350.0, "Large1"),
            (350.0, "Large2"),
            (350.0, "Large3"),
        ]
        result = split_by_cv(pairs, max_cv=0.1, max_groups=2)

        # 最多 2 组
        self.assertLessEqual(len(result), 2)

        # 所有元素都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(len(all_items), 9)

    def test_max_groups_one(self):
        """测试 max_groups=1 不分组"""
        pairs = [
            (50.0, "A"),
            (150.0, "B"),
            (350.0, "C"),
        ]
        result = split_by_cv(pairs, max_cv=0.1, max_groups=1)

        # 只有 1 组
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]), {"A", "B", "C"})

    def test_max_groups_priority_highest_cv(self):
        """测试 max_groups 限制时优先分割 CV 最大的组"""
        # 创建两个高 CV 组，一个 CV 更高
        # Group 1: 小差异 (100, 110, 120) - CV 较低
        # Group 2: 大差异 (50, 500) - CV 很高
        # 当限制为 2 组时，应该优先分割 CV 大的
        pairs = [
            (100.0, "A1"),
            (110.0, "A2"),
            (120.0, "A3"),
            (50.0, "B1"),
            (500.0, "B2"),
        ]
        # 限制为 2 组
        result_limited = split_by_cv(pairs, max_cv=0.05, max_groups=2)
        self.assertLessEqual(len(result_limited), 2)

        # 所有元素都应该在结果中
        all_items = [item for group in result_limited for item in group]
        self.assertEqual(len(all_items), 5)

    def test_max_groups_larger_than_needed(self):
        """测试 max_groups 大于实际需要的组数"""
        pairs = [
            (100.0, "Small1"),
            (100.0, "Small2"),
            (300.0, "Large"),
        ]
        # 限制 10 组，但实际只需要 2 组
        result = split_by_cv(pairs, max_cv=0.1, max_groups=10)

        # 应该分成 2 组（不会因为 max_groups 更大就分更多）
        self.assertEqual(len(result), 2)

        # 所有元素都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(set(all_items), {"Small1", "Small2", "Large"})

    def test_split_preserves_payload(self):
        """测试分割不会丢失或改变 payload"""
        # 使用各种类型的 payload
        pairs = [
            (100.0, "string"),
            (100.0, 123),
            (100.0, ("tuple", "data")),
            (300.0, {"dict": "value"}),
        ]
        result = split_by_cv(pairs, max_cv=0.1)

        # 所有 payload 都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(len(all_items), 4)
        self.assertIn("string", all_items)
        self.assertIn(123, all_items)
        self.assertIn(("tuple", "data"), all_items)
        self.assertIn({"dict": "value"}, all_items)

    def test_size_ordering_independence(self):
        """测试输入顺序不影响分组结果"""
        # 相同的 size，不同的输入顺序
        pairs1 = [(100.0, "A"), (200.0, "B"), (100.0, "C")]
        pairs2 = [(200.0, "B"), (100.0, "A"), (100.0, "C")]

        result1 = split_by_cv(pairs1, max_cv=0.1)
        result2 = split_by_cv(pairs2, max_cv=0.1)

        # 组数应该相同
        self.assertEqual(len(result1), len(result2))

        # 所有元素都应该在结果中（虽然组内顺序可能不同）
        all_items1 = [item for group in result1 for item in group]
        all_items2 = [item for group in result2 for item in group]
        self.assertEqual(set(all_items1), set(all_items2))

    def test_edge_case_all_unique_sizes(self):
        """测试边界情况：所有 size 都不同"""
        pairs = [
            (100.0, "A"),
            (200.0, "B"),
            (300.0, "C"),
            (400.0, "D"),
            (500.0, "E"),
        ]
        result = split_by_cv(pairs, max_cv=0.1)

        # 所有元素都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(len(all_items), 5)

    def test_max_groups_with_indivisible_groups(self):
        """测试当某些组无法继续分割时的 max_groups 行为"""
        # 创建一些只有 2 个元素的组（无法再分割）
        pairs = [
            (100.0, "A"),
            (110.0, "B"),
            (500.0, "C"),
            (510.0, "D"),
        ]
        result = split_by_cv(pairs, max_cv=0.05, max_groups=5)

        # 所有元素都应该在结果中
        all_items = [item for group in result for item in group]
        self.assertEqual(len(all_items), 4)


if __name__ == "__main__":
    unittest.main()
