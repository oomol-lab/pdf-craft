import unittest
from typing import cast

from pdf_craft.markdown.paragraph import HTMLTag, parse_raw_markdown


class TestParseRawMarkdown(unittest.TestCase):
    """测试 parse_raw_markdown 函数"""

    def test_plain_text(self):
        """测试纯文本"""
        result = parse_raw_markdown("Hello World")
        self.assertEqual(result, ["Hello World"])

    def test_basic_allowed_tag(self):
        """测试基本的允许标签"""
        result = parse_raw_markdown("<div>Hello</div>")
        self.assertEqual(len(result), 1)

        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "div")
        self.assertEqual(tag.children, ["Hello"])

    def test_nested_allowed_tags(self):
        """测试嵌套的允许标签"""
        result = parse_raw_markdown("<div><p>Paragraph</p></div>")
        self.assertEqual(len(result), 1)

        div_tag = result[0]
        self.assertIsInstance(div_tag, HTMLTag)
        assert isinstance(div_tag, HTMLTag)
        self.assertEqual(div_tag.definition.name, "div")
        self.assertEqual(len(div_tag.children), 1)

        p_tag = div_tag.children[0]
        self.assertIsInstance(p_tag, HTMLTag)
        assert isinstance(p_tag, HTMLTag)
        self.assertEqual(p_tag.definition.name, "p")
        self.assertEqual(p_tag.children, ["Paragraph"])

    def test_inline_tag_with_text(self):
        """测试行内标签与文本混合"""
        result = parse_raw_markdown("Text before <b>bold</b> text after")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "Text before ")
        tag = result[1]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "b")
        self.assertEqual(result[2], " text after")

    def test_self_closing_tag(self):
        """测试自闭合标签"""
        result = parse_raw_markdown("Line one<br />Line two")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "Line one")
        tag = result[1]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "br")
        self.assertEqual(tag.children, [])
        self.assertEqual(result[2], "Line two")

    def test_html_comment_removal(self):
        """测试 HTML 注释移除"""
        result = parse_raw_markdown("Hello <!-- comment --> World")
        self.assertEqual(result, ["Hello ", " World"])

    def test_processing_instruction_removal(self):
        """测试处理指令移除"""
        result = parse_raw_markdown('Hello <?xml version="1.0"?> World')
        self.assertEqual(result, ["Hello ", " World"])

    def test_cdata_section_removal(self):
        """测试 CDATA 段移除"""
        result = parse_raw_markdown("Hello <![CDATA[data]]> World")
        self.assertEqual(result, ["Hello ", " World"])

    def test_declaration_removal(self):
        """测试声明移除"""
        result = parse_raw_markdown("<!DOCTYPE html>Hello")
        self.assertEqual(result, ["Hello"])

    def test_gfm_tagfilter_script(self):
        """测试 GFM tagfilter 过滤 script 标签"""
        result = parse_raw_markdown('<script>alert("XSS")</script>')
        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[0], str)
        self.assertIn("&lt;script", cast(str, result[0]))
        self.assertEqual(result[1], 'alert("XSS")')
        self.assertIsInstance(result[2], str)
        self.assertIn("&lt;/script", cast(str, result[2]))

    def test_gfm_tagfilter_iframe(self):
        """测试 GFM tagfilter 过滤 iframe 标签"""
        result = parse_raw_markdown('<iframe src="evil.com"></iframe>')
        self.assertIsInstance(result[0], str)
        self.assertIn("&lt;iframe", cast(str, result[0]))

    def test_gfm_tagfilter_style(self):
        """测试 GFM tagfilter 过滤 style 标签"""
        result = parse_raw_markdown("<style>body { display: none; }</style>")
        self.assertIsInstance(result[0], str)
        self.assertIn("&lt;style", cast(str, result[0]))

    def test_allowed_attributes(self):
        """测试允许的属性"""
        result = parse_raw_markdown('<div id="test" title="Test Div">Content</div>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        attr_dict = dict(tag.attributes)
        self.assertEqual(attr_dict["id"], "test")
        self.assertEqual(attr_dict["title"], "Test Div")

    def test_disallowed_attributes_filtered(self):
        """测试不允许的属性被过滤"""
        result = parse_raw_markdown('<div id="test" onclick="alert()">Content</div>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        attr_names = [name for name, _ in tag.attributes]
        self.assertIn("id", attr_names)
        self.assertNotIn("onclick", attr_names)

    def test_allowed_url_protocol_https(self):
        """测试允许的 URL 协议 https"""
        result = parse_raw_markdown('<a href="https://example.com">Link</a>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertIn(("href", "https://example.com"), tag.attributes)

    def test_allowed_url_protocol_http(self):
        """测试允许的 URL 协议 http"""
        result = parse_raw_markdown('<a href="http://example.com">Link</a>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertIn(("href", "http://example.com"), tag.attributes)

    def test_allowed_url_protocol_mailto(self):
        """测试允许的 URL 协议 mailto"""
        result = parse_raw_markdown('<a href="mailto:test@example.com">Email</a>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertIn(("href", "mailto:test@example.com"), tag.attributes)

    def test_allowed_relative_url(self):
        """测试允许的相对 URL"""
        result = parse_raw_markdown('<a href="/path/to/page">Link</a>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertIn(("href", "/path/to/page"), tag.attributes)

    def test_disallowed_url_protocol_javascript(self):
        """测试不允许的 URL 协议 javascript"""
        result = parse_raw_markdown('<a href="javascript:alert()">Link</a>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        attr_names = [name for name, _ in tag.attributes]
        self.assertNotIn("href", attr_names)

    def test_disallowed_url_protocol_data(self):
        """测试不允许的 URL 协议 data"""
        result = parse_raw_markdown(
            '<a href="data:text/html,<script>alert()</script>">Link</a>'
        )
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        attr_names = [name for name, _ in tag.attributes]
        self.assertNotIn("href", attr_names)

    def test_disallowed_tag_escaped(self):
        """测试不允许的标签被转义"""
        result = parse_raw_markdown("<custom>Content</custom>")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "&lt;custom&gt;")
        self.assertEqual(result[1], "Content")
        self.assertEqual(result[2], "&lt;/custom&gt;")

    def test_disallowed_tag_with_allowed_child(self):
        """测试不允许的标签包含允许的子标签"""
        result = parse_raw_markdown("<custom><b>Bold</b></custom>")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "&lt;custom&gt;")
        tag = result[1]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "b")
        self.assertEqual(result[2], "&lt;/custom&gt;")

    def test_img_tag_with_src(self):
        """测试图片标签"""
        result = parse_raw_markdown(
            '<img src="https://example.com/image.png" alt="Test Image" />'
        )
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "img")
        self.assertIn(("src", "https://example.com/image.png"), tag.attributes)
        self.assertIn(("alt", "Test Image"), tag.attributes)

    def test_table_structure(self):
        """测试表格结构"""
        html = "<table><tr><td>Cell</td></tr></table>"
        result = parse_raw_markdown(html)
        self.assertEqual(len(result), 1)
        table_tag = result[0]
        self.assertIsInstance(table_tag, HTMLTag)
        assert isinstance(table_tag, HTMLTag)
        self.assertEqual(table_tag.definition.name, "table")
        self.assertEqual(len(table_tag.children), 1)
        tr_tag = table_tag.children[0]
        self.assertIsInstance(tr_tag, HTMLTag)
        assert isinstance(tr_tag, HTMLTag)
        self.assertEqual(tr_tag.definition.name, "tr")

    def test_blockquote_with_cite(self):
        """测试引用标签与 cite 属性"""
        result = parse_raw_markdown(
            '<blockquote cite="https://example.com">Quote</blockquote>'
        )
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "blockquote")
        self.assertIn(("cite", "https://example.com"), tag.attributes)

    def test_details_summary(self):
        """测试 details 和 summary 标签"""
        result = parse_raw_markdown(
            "<details><summary>Title</summary>Content</details>"
        )
        self.assertEqual(len(result), 1)
        details_tag = result[0]
        self.assertIsInstance(details_tag, HTMLTag)
        assert isinstance(details_tag, HTMLTag)
        self.assertEqual(details_tag.definition.name, "details")
        self.assertEqual(len(details_tag.children), 2)
        summary_tag = details_tag.children[0]
        self.assertIsInstance(summary_tag, HTMLTag)
        assert isinstance(summary_tag, HTMLTag)
        self.assertEqual(summary_tag.definition.name, "summary")

    def test_multiple_tags_in_sequence(self):
        """测试多个标签连续出现"""
        result = parse_raw_markdown("<p>First</p><p>Second</p><p>Third</p>")
        self.assertEqual(len(result), 3)
        for item in result:
            self.assertIsInstance(item, HTMLTag)
            assert isinstance(item, HTMLTag)
            self.assertEqual(item.definition.name, "p")

    def test_deeply_nested_tags(self):
        """测试深层嵌套标签"""
        html = "<div><p><span><strong>Deep</strong></span></p></div>"
        result = parse_raw_markdown(html)
        self.assertEqual(len(result), 1)
        div_tag = result[0]
        self.assertIsInstance(div_tag, HTMLTag)
        assert isinstance(div_tag, HTMLTag)
        self.assertEqual(div_tag.definition.name, "div")

        # 检查嵌套结构
        p_tag = div_tag.children[0]
        self.assertIsInstance(p_tag, HTMLTag)
        assert isinstance(p_tag, HTMLTag)
        self.assertEqual(p_tag.definition.name, "p")

        span_tag = p_tag.children[0]
        self.assertIsInstance(span_tag, HTMLTag)
        assert isinstance(span_tag, HTMLTag)
        self.assertEqual(span_tag.definition.name, "span")

        strong_tag = span_tag.children[0]
        self.assertIsInstance(strong_tag, HTMLTag)
        assert isinstance(strong_tag, HTMLTag)
        self.assertEqual(strong_tag.definition.name, "strong")
        self.assertEqual(strong_tag.children, ["Deep"])

    def test_html_entities_in_attributes(self):
        """测试属性中的 HTML 实体"""
        result = parse_raw_markdown('<div title="&lt;Test&gt;">Content</div>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        # HTML entities should be unescaped in attribute values
        self.assertIn(("title", "<Test>"), tag.attributes)

    def test_empty_tags(self):
        """测试空标签"""
        result = parse_raw_markdown("<div></div>")
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.children, [])

    def test_whitespace_in_tags(self):
        """测试标签中的空白"""
        result = parse_raw_markdown('<div   id="test"   >Content</div>')
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertIn(("id", "test"), tag.attributes)

    def test_boolean_attributes(self):
        """测试布尔属性"""
        result = parse_raw_markdown("<input checked disabled />")
        self.assertEqual(len(result), 1)
        # Note: input is not in the whitelist, so it will be escaped
        # Let's test with a whitelisted tag that supports boolean attributes
        result = parse_raw_markdown("<details open>Content</details>")
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertIn(("open", ""), tag.attributes)

    def test_mixed_content_complex(self):
        """测试复杂混合内容"""
        html = "Text before <p>Paragraph with <strong>bold</strong> and <em>italic</em></p> text after"
        result = parse_raw_markdown(html)
        self.assertGreater(len(result), 1)
        self.assertEqual(result[0], "Text before ")
        tag = result[1]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "p")
        self.assertEqual(result[2], " text after")

    def test_incomplete_tag_treated_as_text(self):
        """测试不完整的标签被当作文本"""
        result = parse_raw_markdown("Price < 100 and > 50")
        self.assertEqual(result, ["Price ", "<", " 100 and > 50"])

    def test_video_tag_with_attributes(self):
        """测试 video 标签"""
        html = '<video src="https://example.com/video.mp4" controls width="640">Content</video>'
        result = parse_raw_markdown(html)
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        self.assertEqual(tag.definition.name, "video")
        self.assertIn(("src", "https://example.com/video.mp4"), tag.attributes)
        self.assertIn(("controls", ""), tag.attributes)
        self.assertIn(("width", "640"), tag.attributes)

    def test_list_structure(self):
        """测试列表结构"""
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        result = parse_raw_markdown(html)
        self.assertEqual(len(result), 1)
        ul_tag = result[0]
        self.assertIsInstance(ul_tag, HTMLTag)
        assert isinstance(ul_tag, HTMLTag)
        self.assertEqual(ul_tag.definition.name, "ul")
        self.assertEqual(len(ul_tag.children), 2)
        for child in ul_tag.children:
            self.assertIsInstance(child, HTMLTag)
            assert isinstance(child, HTMLTag)
            self.assertEqual(child.definition.name, "li")

    def test_multiple_attributes_filtering(self):
        """测试多个属性的过滤"""
        html = '<div id="test" class="container" onclick="alert()" data-value="123">Content</div>'
        result = parse_raw_markdown(html)
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        attr_names = [name for name, _ in tag.attributes]
        # id should be allowed
        self.assertIn("id", attr_names)
        # onclick should be filtered out (not in whitelist)
        self.assertNotIn("onclick", attr_names)
        # data-* attributes should be filtered out (not in whitelist)
        self.assertNotIn("data-value", attr_names)

    def test_case_insensitive_tag_names(self):
        """测试标签名大小写不敏感"""
        result = parse_raw_markdown("<DIV>Content</DIV>")
        self.assertEqual(len(result), 1)
        tag = result[0]
        self.assertIsInstance(tag, HTMLTag)
        assert isinstance(tag, HTMLTag)
        # Tag name should be normalized to lowercase
        self.assertEqual(tag.definition.name, "div")

    def test_nested_same_tags(self):
        """测试嵌套相同标签"""
        html = "<div><div>Inner</div></div>"
        result = parse_raw_markdown(html)
        self.assertEqual(len(result), 1)
        outer_div = result[0]
        self.assertIsInstance(outer_div, HTMLTag)
        assert isinstance(outer_div, HTMLTag)
        self.assertEqual(outer_div.definition.name, "div")
        self.assertEqual(len(outer_div.children), 1)
        inner_div = outer_div.children[0]
        self.assertIsInstance(inner_div, HTMLTag)
        assert isinstance(inner_div, HTMLTag)
        self.assertEqual(inner_div.definition.name, "div")
        self.assertEqual(inner_div.children, ["Inner"])


if __name__ == "__main__":
    unittest.main()
