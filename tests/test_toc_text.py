import unittest

from pdf_craft.toc.text import normalize_text


class TestNormalizeText(unittest.TestCase):
    """测试 normalize_text 函数"""

    def test_basic_whitespace_normalization(self):
        """测试基本的空白符规范化"""
        # 多个空格被替换为单个空格
        text = "hello    world"
        result = normalize_text(text)
        self.assertEqual(result, "hello world")

        # 制表符和换行符被替换为单个空格
        text = "hello\t\n\r  world"
        result = normalize_text(text)
        self.assertEqual(result, "hello world")

    def test_hyphenated_word_reconnection(self):
        """测试连字符断词处理（拉丁字母换行连接）"""
        # 拉丁字母 + 连字符 + 空格 + 拉丁字母 -> 连接单词
        text = "under- standing"
        result = normalize_text(text)
        self.assertEqual(result, "understanding")

        # 使用不同的连字符
        text = "hyper— text"
        result = normalize_text(text)
        self.assertEqual(result, "hypertext")

    def test_chinese_space_removal(self):
        """测试中文字符之间空格的删除"""
        # 中文字符之间的空格应该被删除
        text = "这是 一个 测试"
        result = normalize_text(text)
        self.assertEqual(result, "这是一个测试")

    def test_latin_space_preservation(self):
        """测试拉丁字母之间空格的保留"""
        # 拉丁字母之间的空格应该被保留
        text = "hello world"
        result = normalize_text(text)
        self.assertEqual(result, "hello world")

    def test_punctuation_removal(self):
        """测试标点符号的删除"""
        # ASCII 标点符号应该被删除
        # 注意：标点符号后的空格也会被删除，因为只保留拉丁字母之间的空格
        text = "Hello, World! How are you?"
        result = normalize_text(text)
        self.assertEqual(result, "helloworldhow are you")

        # 中文标点符号应该被删除
        text = "你好，世界！"
        result = normalize_text(text)
        self.assertEqual(result, "你好世界")

    def test_latin_lowercase_conversion(self):
        """测试拉丁字母的小写化"""
        text = "HELLO World"
        result = normalize_text(text)
        self.assertEqual(result, "hello world")

    def test_accent_removal(self):
        """测试重音符号的去除"""
        # 法语重音符号应该被去除
        text = "café résumé"
        result = normalize_text(text)
        self.assertEqual(result, "cafe resume")

        # 德语变音符号应该被去除
        text = "über schön"
        result = normalize_text(text)
        self.assertEqual(result, "uber schon")

    def test_mixed_scenario(self):
        """测试混合场景"""
        # 混合中文、英文、标点符号、连字符断词
        text = "第一章：Introduction to Compu- ter Science（计算机 科学 导论）！"
        result = normalize_text(text)
        self.assertEqual(result, "第一章introduction to computer science计算机科学导论")

        # 包含重音符号和标点符号的混合文本
        # 注意：冒号后的空格会被删除，因为冒号不是拉丁字母
        text = "Résumé: Machine   Learning, 机器 学习"
        result = normalize_text(text)
        self.assertEqual(result, "resumemachine learning机器学习")


if __name__ == "__main__":
    unittest.main()
