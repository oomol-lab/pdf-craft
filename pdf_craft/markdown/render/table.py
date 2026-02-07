from typing import TYPE_CHECKING

from markdownify import MarkdownConverter

if TYPE_CHECKING:
    from bs4 import Tag


class _TableComplexityException(Exception):
    pass


class _GFMTableConverter(MarkdownConverter):
    """
    Custom converter that detects complex table features.

    The base markdownify library will silently convert complex HTML tables to
    GFM pipe tables, but this loses information (e.g., merged cells become
    empty cells). This custom converter detects such cases and raises an
    exception to allow fallback to HTML.

    Background:
    - GFM markdown tables don't support colspan/rowspan
    - markdownify doesn't provide rowspan support (issue #121):
      https://github.com/matthewwithanm/python-markdownify/issues/121
    - Instead of silent data loss, we detect complexity and preserve HTML

    Raises TableComplexityException when encountering:
    - colspan > 1
    - rowspan > 1
    - Multiple tbody sections
    """

    def __init__(self, **options):
        super().__init__(**options)
        self._tbody_count = 0

    def convert_td(self, el: "Tag", text: str, parent_tags: set[str]) -> str:
        self._check_cell_complexity(el)
        return super().convert_td(el, text, parent_tags)  # type: ignore[attr-defined]

    def convert_th(self, el: "Tag", text: str, parent_tags: set[str]) -> str:
        self._check_cell_complexity(el)
        return super().convert_th(el, text, parent_tags)  # type: ignore[attr-defined]

    def convert_table(self, el: "Tag", text: str, parent_tags: set[str]) -> str:
        self._tbody_count = len(el.find_all("tbody", recursive=False))
        if self._tbody_count > 1:
            raise _TableComplexityException(
                f"Table has {self._tbody_count} tbody sections (GFM only supports 1)"
            )
        return super().convert_table(el, text, parent_tags)  # type: ignore[attr-defined]

    def _check_cell_complexity(self, el: "Tag") -> None:
        colspan = el.get("colspan", "1")
        rowspan = el.get("rowspan", "1")

        try:
            colspan_str = str(colspan) if colspan else "1"
            rowspan_str = str(rowspan) if rowspan else "1"

            if int(colspan_str) > 1:
                raise _TableComplexityException(
                    f"Table has colspan={colspan_str} (GFM doesn't support colspan)"
                )
            if int(rowspan_str) > 1:
                raise _TableComplexityException(
                    f"Table has rowspan={rowspan_str} (GFM doesn't support rowspan)"
                )
        except ValueError as error:
            raise _TableComplexityException(
                "Table has invalid colspan/rowspan values"
            ) from error


def render_table_content(html_string: str) -> str:
    try:
        converter = _GFMTableConverter(heading_style="ATX")
        gfm_table = converter.convert(html_string).strip()
        return gfm_table
    except _TableComplexityException:
        return html_string
