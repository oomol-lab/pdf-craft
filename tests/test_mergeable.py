import unittest
from typing import Iterable

from pdf_craft.markdown.paragraph import HTMLTag
from pdf_craft.sequence import BlockMember
from pdf_craft.sequence.mergeable import check_mergeable

_Content = list[str | BlockMember | HTMLTag[BlockMember]]

# history bugs
_REAL_BUG_CASES: Iterable[tuple[_Content, _Content, bool, str]] = (
    (
        ["Perform the procedure according to the following gradient:"],
        ["(4) Solution preparation"],
        False,
        "冒号结尾+编号段落（原始 bug）",
    ),
    (
        ["Perform the procedure according to the following gradient:  "],
        ["  (4) Solution preparation"],
        False,
        "冒号结尾+编号段落（带空白）",
    ),
    (
        [
            (
                "They have been realigning the entire dialectic of the pleasure principle "
                "and reality principle on the object relation, and basing all "
                "analytic furtherance around what might be called a rectifying of the subject's"
            )
        ],
        [
            (
                "relation to the object, regarded as a dual relationship, as a relationship that would be "
                "- so we are told when they speak about the analytic situation "
                "- exceedingly straightforward.1 Well, this relationship between subject and object "
                "that is increasingly tending to occupy the centre stage of analytic theory is the very "
                "thing that we are going to put to the test."
            )
        ],
        True,
        "被错误分割的完整自然段",
    ),
)

_CONSTRUCTED_TEST_CASES: Iterable[tuple[_Content, _Content, bool, str]] = (
    # 编号段落测试 - 不应合并
    (["Previous paragraph"], ["1. First item"], False, "阿拉伯数字+点号"),
    (["Some text"], ["2) Second item"], False, "阿拉伯数字+右括号"),
    (["Introduction text"], ["(1) First point"], False, "括号包裹"),
    (["前面的文字"], ["一、第一项"], False, "中文编号"),
    (["Previous section"], ["I. Introduction"], False, "大写罗马数字"),
    (["Main text"], ["ii) Second point"], False, "小写罗马数字"),
    (["文字内容"], ["（1）第一项"], False, "全角括号"),
    (["Content"], ["[1] Reference"], False, "方括号"),
    (["Text"], ["<1> Item"], False, "尖括号"),
    # 编号+多节点测试
    (["Text"], ["(1) ", "Some other node"], False, "编号后有多个节点"),
    (["Text"], ["(1)", "another node"], False, "编号后文字为空但有多个节点"),
    (["Text"], ["(1)   Content with spaces"], False, "编号后有空白"),
    # 应该合并的情况
    (["First part,"], ["second part"], True, "逗号结尾"),
    (["Text with ("], ["content inside)"], True, "左括号结尾"),
    (["This is hyper-"], ["text"], True, "连字符拼接单词"),
    (["文字内容"], ["继续内容"], True, "小写开头非拉丁"),
    (["第一部分，"], ["第二部分"], True, "中文逗号"),
    (["文字内容（"], ["括号内容）"], True, "中文左括号"),
    (["Text"], ["(1)"], True, "只有编号没有内容"),
    # 不应合并的情况
    (["Complete sentence."], ["New sentence"], False, "句号结尾"),
    (["这是一句话。"], ["这是另一句话"], False, "中文句号"),
    # 边界情况
    ([""], ["text"], False, "空内容"),
    (["   "], ["text"], False, "只有空白字符"),
)


class TestCheckMergeable(unittest.TestCase):
    def test_constructed_cases(self):
        for content1, content2, expected, description in _CONSTRUCTED_TEST_CASES:
            with self.subTest(description=description):
                result = check_mergeable(content1, content2)
                self.assertEqual(
                    result,
                    expected,
                    f"{description}: content1={content1!r}, content2={content2!r}",
                )

    def test_real_bug_cases(self):
        for content1, content2, expected, description in _REAL_BUG_CASES:
            with self.subTest(description=description):
                result = check_mergeable(content1, content2)
                self.assertEqual(
                    result,
                    expected,
                    f"{description}: content1={content1!r}, content2={content2!r}",
                )


if __name__ == "__main__":
    unittest.main()
