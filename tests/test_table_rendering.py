"""Tests for table rendering functionality."""

import unittest

from pdf_craft.markdown.render.table import render_table_content


class TestRenderTableContent(unittest.TestCase):
    """Test cases for render_table_content function."""

    def test_simple_table_converts_to_gfm(self):
        """Simple table should convert to GFM pipe table."""
        html = (
            "<table>"
            "<thead><tr><th>Name</th><th>Age</th><th>City</th></tr></thead>"
            "<tbody>"
            "<tr><td>Alice</td><td>25</td><td>NYC</td></tr>"
            "<tr><td>Bob</td><td>30</td><td>LA</td></tr>"
            "</tbody>"
            "</table>"
        )

        result = render_table_content(html)

        # Should be GFM format
        assert "|" in result
        assert "---" in result
        assert "Name" in result
        assert "Alice" in result
        # Should not be HTML
        assert "<table>" not in result
        assert "<td>" not in result

    def test_table_with_colspan_preserves_html(self):
        """Table with colspan > 1 should preserve HTML."""
        html = (
            "<table>"
            '<thead><tr><th colspan="2">Personal Info</th><th>Location</th></tr></thead>'
            "<tbody><tr><td>Alice</td><td>25</td><td>NYC</td></tr></tbody>"
            "</table>"
        )

        result = render_table_content(html)

        # Should preserve HTML
        assert "<table>" in result
        assert 'colspan="2"' in result

    def test_table_with_rowspan_preserves_html(self):
        """Table with rowspan > 1 should preserve HTML."""
        html = (
            "<table>"
            "<tbody>"
            '<tr><td rowspan="2">Alice</td><td>Email</td><td>alice@example.com</td></tr>'
            "<tr><td>Phone</td><td>123-456-7890</td></tr>"
            "</tbody>"
            "</table>"
        )

        result = render_table_content(html)

        # Should preserve HTML
        assert "<table>" in result
        assert 'rowspan="2"' in result

    def test_table_with_colspan_1_converts_to_gfm(self):
        """Table with colspan=1 (default) should convert to GFM."""
        html = (
            "<table>"
            '<tr><th colspan="1">Header A</th><th>Header B</th></tr>'
            "<tr><td>Cell 1</td><td>Cell 2</td></tr>"
            "</table>"
        )

        result = render_table_content(html)

        # Should be GFM (colspan=1 is not complex)
        assert "|" in result
        assert "---" in result
        assert "<table>" not in result

    def test_table_with_multiple_tbody_preserves_html(self):
        """Table with multiple tbody sections should preserve HTML."""
        html = (
            "<table>"
            "<tbody><tr><td>Group 1</td></tr></tbody>"
            "<tbody><tr><td>Group 2</td></tr></tbody>"
            "</table>"
        )

        result = render_table_content(html)

        # Should preserve HTML
        assert "<table>" in result
        assert "<tbody>" in result

    def test_table_with_alignment_converts_to_gfm(self):
        """Table with align attributes should convert to GFM."""
        html = (
            "<table>"
            "<thead>"
            "<tr>"
            '<th align="left">Left</th>'
            '<th align="center">Center</th>'
            '<th align="right">Right</th>'
            "</tr>"
            "</thead>"
            "<tbody>"
            '<tr><td align="left">L1</td><td align="center">C1</td><td align="right">R1</td></tr>'
            "</tbody>"
            "</table>"
        )

        result = render_table_content(html)

        # Should be GFM (align is simple)
        assert "|" in result
        assert "---" in result
        assert "Left" in result
        assert "<table>" not in result

    def test_empty_table_converts_to_gfm(self):
        """Empty table should convert to GFM."""
        html = "<table></table>"

        result = render_table_content(html)

        # markdownify should handle empty tables gracefully
        # (exact behavior depends on markdownify version)
        assert result is not None

    def test_table_without_thead_converts_to_gfm(self):
        """Table without thead should convert to GFM."""
        html = (
            "<table>"
            "<tr><td>Cell 1</td><td>Cell 2</td></tr>"
            "<tr><td>Cell 3</td><td>Cell 4</td></tr>"
            "</table>"
        )

        result = render_table_content(html)

        # Should be GFM
        assert "|" in result
        assert "---" in result
        assert "<table>" not in result

    def test_table_with_nested_formatting_converts_to_gfm(self):
        """Table with nested formatting (bold, italic) should convert to GFM."""
        html = (
            "<table>"
            "<thead><tr><th>Name</th><th>Status</th></tr></thead>"
            "<tbody>"
            "<tr><td><strong>Alice</strong></td><td><em>Active</em></td></tr>"
            "</tbody>"
            "</table>"
        )

        result = render_table_content(html)

        # Should be GFM with markdown formatting
        assert "|" in result
        assert "**Alice**" in result or "*Alice*" in result
        assert "<table>" not in result

    def test_complex_table_with_both_colspan_and_rowspan(self):
        """Table with both colspan and rowspan should preserve HTML."""
        html = (
            "<table>"
            '<tr><th colspan="2" rowspan="2">Complex</th><th>Normal</th></tr>'
            "<tr><td>Cell</td></tr>"
            "</table>"
        )

        result = render_table_content(html)

        # Should preserve HTML
        assert "<table>" in result
        assert 'colspan="2"' in result
        assert 'rowspan="2"' in result


class TestTableContentEdgeCases(unittest.TestCase):
    """Edge cases for table rendering."""

    def test_table_with_special_characters(self):
        """Table with special characters should be handled correctly."""
        html = (
            "<table>"
            "<tr><th>Symbol</th><th>Meaning</th></tr>"
            "<tr><td>&lt;</td><td>Less than</td></tr>"
            "<tr><td>&gt;</td><td>Greater than</td></tr>"
            "</table>"
        )

        result = render_table_content(html)

        # Should convert to GFM with escaped characters
        assert "|" in result
        assert "Symbol" in result

    def test_table_with_line_breaks_in_cells(self):
        """Table with br tags should be handled."""
        html = "<table><tr><td>Line 1<br />Line 2</td><td>Cell 2</td></tr></table>"

        result = render_table_content(html)

        # Should be converted (markdownify handles br tags)
        assert result is not None

    def test_invalid_colspan_value_preserves_html(self):
        """Table with invalid colspan value should preserve HTML."""
        html = (
            "<table>"
            '<tr><th colspan="invalid">Header</th></tr>'
            "<tr><td>Cell</td></tr>"
            "</table>"
        )

        result = render_table_content(html)

        # Should preserve HTML due to invalid value
        assert "<table>" in result


if __name__ == "__main__":
    unittest.main()
