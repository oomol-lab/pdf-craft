"""Immutable EPUB content locations captured before translation changes the DOM."""

from dataclasses import dataclass
from xml.etree.ElementTree import Element

from ...transformer.xml_translator.segment.text_segment import TextPosition, TextSegment


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class ContentPoint:
    path: tuple[int, ...]
    offset: int


@dataclass(frozen=True)
class ContentRange:
    start: ContentPoint
    end: ContentPoint

    def cfi(self, package_path: str) -> str:
        left, right = self.start.path, self.end.path
        common = 0
        # Keep the terminal text step in each endpoint, including same-node ranges.
        while common < min(len(left), len(right)) - 1 and left[common] == right[common]:
            common += 1

        def path(parts):
            return "".join(f"/{step}" for step in parts)

        return (f"epubcfi({package_path}!{path(left[:common])},"
                f"{path(left[common:])}:{self.start.offset},"
                f"{path(right[common:])}:{self.end.offset})")


class ContentCFILocator:
    """Linear index; paths are generated only when capturing a fragment.

    XML entities are already expanded by the parser. ElementTree's ``tail``
    belongs to the text position after that child, not inside the child.
    """

    def __init__(self, root: Element) -> None:
        self._parents: dict[int, tuple[int | None, int]] = {id(root): (None, 0)}
        self._texts: dict[tuple[int, TextPosition], tuple[int, int, str]] = {}
        for parent in root.iter():
            self._texts[id(parent), TextPosition.TEXT] = (id(parent), 1, parent.text or "")
            for index, child in enumerate(parent):
                step = (index + 1) * 2
                self._parents[id(child)] = (id(parent), step)
                self._texts[id(child), TextPosition.TAIL] = (id(parent), step + 1, child.tail or "")

    def point(self, element: Element, position: TextPosition, offset: int, *, node_identity: int = 0) -> ContentPoint:
        parent, text_step, text = self._texts[node_identity or id(element), position]
        if not 0 <= offset <= len(text):
            raise ValueError("source offset is outside the original text")
        steps = [text_step]
        while True:
            ancestor, step = self._parents[parent]
            if ancestor is None:
                break
            steps.append(step)
            parent = ancestor
        return ContentPoint(tuple(reversed(steps)), _utf16(text[:offset]))

    def capture(self, segments: list[TextSegment]) -> ContentRange:
        spans = [span for segment in segments for span in segment.origins]
        if not spans:
            raise ValueError("translation fragment has no original source")
        first, last = spans[0], spans[-1]
        return ContentRange(
            self.point(first.element, first.position, first.start, node_identity=first.node_identity),
            self.point(last.element, last.position, last.end, node_identity=last.node_identity),
        )
