from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum, auto
from xml.etree.ElementTree import Element

from pdf_craft.transformer.xml_translator.segment import TextSegment, combine_text_segments
from pdf_craft.transformer.xml_translator.xml import index_of_parent, is_inline_element, iter_with_stack
from .stream_mapper import InlineSegmentMapping


class SubmitKind(Enum):
    REPLACE = auto()
    APPEND_TEXT = auto()
    APPEND_BLOCK = auto()


def submit(element: Element, action: SubmitKind, mappings: list[InlineSegmentMapping]) -> Element:
    submitter = _Submitter(
        element=element,
        action=action,
        mappings=mappings,
    )
    replaced_root = submitter.do()
    if replaced_root is not None:
        return replaced_root

    return element


@dataclass
class _Node:
    raw_element: Element
    items: list[tuple[list[TextSegment], "_Node"]]  # empty for peak, non-empty for platform
    tail_text_segments: list[TextSegment]


class _Submitter:
    def __init__(
        self,
        element: Element,
        action: SubmitKind,
        mappings: list[InlineSegmentMapping],
    ) -> None:
        self._action: SubmitKind = action
        self._nodes: list[_Node] = list(_nest_nodes(mappings))
        self._parents: dict[int, Element] = self._collect_parents(element, mappings)

    def _collect_parents(self, element: Element, mappings: list[InlineSegmentMapping]):
        ids: set[int] = set(id(e) for e, _ in mappings)
        parents_dict: dict[int, Element] = {}
        for parents, child in iter_with_stack(element):
            if parents and id(child) in ids:
                parents_dict[id(child)] = parents[-1]
        return parents_dict

    def do(self):
        replaced_root: Element | None = None

        for node in self._nodes:
            submitted = self._submit_node(node)
            if replaced_root is None:
                replaced_root = submitted

        return replaced_root

    # @return replaced root element, or None if appended to parent
    def _submit_node(self, node: _Node) -> Element | None:
        if node.items or self._action == SubmitKind.APPEND_TEXT:
            return self._submit_by_text(node)
        else:
            return self._submit_by_block(node)

    def _submit_by_block(self, node: _Node) -> Element | None:
        parent = self._parents.get(id(node.raw_element), None)
        if parent is None:
            return node.raw_element

        preserved_elements: list[Element] = []
        if self._action == SubmitKind.REPLACE:
            for child in list(node.raw_element):
                if not is_inline_element(child):
                    child.tail = None
                    preserved_elements.append(child)

        index = index_of_parent(parent, node.raw_element)
        combined = self._combine_text_segments(node.tail_text_segments)

        if combined is not None:
            # 在 APPEND_BLOCK 模式下，如果是 inline tag，则在文本前面加空格
            if self._action == SubmitKind.APPEND_BLOCK and is_inline_element(combined) and combined.text:
                combined.text = " " + combined.text
            parent.insert(index + 1, combined)
            index += 1

        for elem in preserved_elements:
            parent.insert(index + 1, elem)
            index += 1

        if combined is not None or preserved_elements:
            if preserved_elements:
                preserved_elements[-1].tail = node.raw_element.tail
            elif combined is not None:
                combined.tail = node.raw_element.tail
            node.raw_element.tail = None

            if self._action == SubmitKind.REPLACE:
                parent.remove(node.raw_element)

        return None

    def _submit_by_text(self, node: _Node) -> Element | None:
        replaced_root: Element | None = None
        child_nodes = dict((id(node), node) for _, node in node.items)
        last_tail_element: Element | None = None
        tail_elements: dict[int, Element] = {}

        for child_element in node.raw_element:
            child_node = child_nodes.get(id(child_element), None)
            if child_node is not None:
                if last_tail_element is not None:
                    tail_elements[id(child_element)] = last_tail_element
                last_tail_element = child_element

        for text_segments, child_node in node.items:
            anchor_element = _find_anchor_in_parent(node.raw_element, child_node.raw_element)
            if anchor_element is None:
                # 防御性编程：理论上 anchor_element 不应该为 None，
                #           因为 _nest_nodes 已经通过 _check_includes 验证了包含关系。
                continue

            tail_element = tail_elements.get(id(anchor_element), None)
            items_preserved_elements: list[Element] = []

            if self._action == SubmitKind.REPLACE:
                end_index = index_of_parent(node.raw_element, anchor_element)
                items_preserved_elements = self._remove_elements_after_tail(
                    node_element=node.raw_element,
                    tail_element=tail_element,
                    end_index=end_index,
                )

            self._append_combined_after_tail(
                node_element=node.raw_element,
                text_segments=text_segments,
                tail_element=tail_element,
                anchor_element=anchor_element,
                append_to_end=False,
            )
            if items_preserved_elements:
                insert_position = index_of_parent(node.raw_element, anchor_element)
                for i, elem in enumerate(items_preserved_elements):
                    node.raw_element.insert(insert_position + i, elem)

        for _, child_node in node.items:
            submitted = self._submit_node(child_node)
            if replaced_root is None:
                replaced_root = submitted

        if node.raw_element:
            last_tail_element = node.raw_element[-1]
        else:
            last_tail_element = None

        tail_preserved_elements: list[Element] = []
        if self._action == SubmitKind.REPLACE:
            tail_preserved_elements = self._remove_elements_after_tail(
                node_element=node.raw_element,
                tail_element=last_tail_element,
                end_index=None,  # None 表示删除到末尾
            )
        self._append_combined_after_tail(
            node_element=node.raw_element,
            text_segments=node.tail_text_segments,
            tail_element=last_tail_element,
            anchor_element=None,
            append_to_end=True,
        )
        if tail_preserved_elements:
            for elem in tail_preserved_elements:
                node.raw_element.append(elem)

        return replaced_root

    def _remove_elements_after_tail(
        self,
        node_element: Element,
        tail_element: Element | None,
        end_index: int | None = None,
    ) -> list[Element]:
        if tail_element is None:
            start_index = 0
            node_element.text = None
        else:
            start_index = index_of_parent(node_element, tail_element) + 1
            tail_element.tail = None

        if end_index is None:
            end_index = len(node_element)

        preserved_elements: list[Element] = []
        for i in range(start_index, end_index):
            elem = node_element[i]
            if not is_inline_element(elem):
                elem.tail = None
                preserved_elements.append(elem)

        for i in range(end_index - 1, start_index - 1, -1):
            node_element.remove(node_element[i])

        return preserved_elements

    def _append_combined_after_tail(
        self,
        node_element: Element,
        text_segments: list[TextSegment],
        tail_element: Element | None,
        anchor_element: Element | None,
        append_to_end: bool,
    ) -> None:
        combined = self._combine_text_segments(text_segments)
        if combined is None:
            return

        if combined.text:
            will_inject_space = self._action == SubmitKind.APPEND_TEXT or (
                is_inline_element(combined) and self._action == SubmitKind.APPEND_BLOCK
            )
            if tail_element is not None:
                tail_element.tail = self._append_text_in_element(
                    origin_text=tail_element.tail,
                    append_text=combined.text,
                    will_inject_space=will_inject_space,
                )
            elif anchor_element is None:
                node_element.text = self._append_text_in_element(
                    origin_text=node_element.text,
                    append_text=combined.text,
                    will_inject_space=will_inject_space,
                )
            else:
                ref_index = index_of_parent(node_element, anchor_element)
                if ref_index > 0:
                    # 添加到前一个元素的 tail
                    prev_element = node_element[ref_index - 1]
                    prev_element.tail = self._append_text_in_element(
                        origin_text=prev_element.tail,
                        append_text=combined.text,
                        will_inject_space=will_inject_space,
                    )
                else:
                    # ref_element 是第一个元素，添加到 node_element.text
                    node_element.text = self._append_text_in_element(
                        origin_text=node_element.text,
                        append_text=combined.text,
                        will_inject_space=will_inject_space,
                    )

        if tail_element is not None:
            insert_position = index_of_parent(node_element, tail_element) + 1
        elif append_to_end:
            insert_position = len(node_element)
        elif anchor_element is not None:
            # 使用 ref_element 来定位插入位置
            # 如果文本被添加到前一个元素的 tail，则在前一个元素之后插入
            ref_index = index_of_parent(node_element, anchor_element)
            if ref_index > 0:
                # 在前一个元素之后插入
                insert_position = ref_index
            else:
                # ref_element 是第一个元素，插入到开头
                insert_position = 0
        else:
            insert_position = 0

        for i, child in enumerate(combined):
            node_element.insert(insert_position + i, child)

    def _combine_text_segments(self, text_segments: list[TextSegment]) -> Element | None:
        segments = (t.strip_block_parents() for t in text_segments)
        combined = next(combine_text_segments(segments), None)
        if combined is None:
            return None
        else:
            return combined[0]

    def _append_text_in_element(
        self,
        origin_text: str | None,
        append_text: str,
        will_inject_space: bool,
    ) -> str:
        if origin_text is None:
            return append_text
        elif will_inject_space:
            return origin_text.rstrip() + " " + append_text.lstrip()
        else:
            return origin_text + append_text


def _nest_nodes(mappings: list[InlineSegmentMapping]) -> Generator[_Node, None, None]:
    # 需要翻译的文字会被嵌套到两种不同的结构中。
    # 最常见的的是 peak 结构，例如如下结构，没有任何子结构（inline 标签不是视为子结构）。
    # 可直接文本替换或追加。
    # <div>Some text <b>bold text</b> more text.</div>
    #
    # 但是还有一种少见的 platform 结构，它内部被其他 peak/platform 切割。
    #   <div>
    #     Some text before.
    #     <!-- 如下 peak 将它的阅读流切段 -->
    #     <div>Paragraph 1.</div>
    #     Some text in between.
    #   </div>
    # 如果直接对它进行替换或追加，读者阅读流会被破坏，从而读起来怪异。
    # 正是因为这种结构的存在，必须还原成树型结构，然后用特殊的方式来处理 platform 结构。
    #
    # 总之，我们假设 95% 的阅读体验由 peak 提供，但为兼顾剩下的 platform 结构，故加此步骤。
    stack: list[_Node] = []

    for block_element, text_segments in mappings:
        keep_depth: int = 0
        upwards: bool = False
        for i in range(len(stack) - 1, -1, -1):
            if stack[i].raw_element is block_element:
                keep_depth = i + 1
                upwards = True
                break

        if not upwards:
            for i in range(len(stack) - 1, -1, -1):
                if _check_includes(stack[i].raw_element, block_element):
                    keep_depth = i + 1
                    break

        while len(stack) > keep_depth:
            child_node = _fold_top_of_stack(stack)
            if not upwards and child_node is not None:
                yield child_node

        if upwards:
            stack[keep_depth - 1].tail_text_segments.extend(text_segments)
        else:
            stack.append(
                _Node(
                    raw_element=block_element,
                    items=[],
                    tail_text_segments=list(text_segments),
                )
            )
    while stack:
        child_node = _fold_top_of_stack(stack)
        if child_node is not None:
            yield child_node


def _find_anchor_in_parent(parent: Element, descendant: Element) -> Element | None:
    for child in parent:
        if child is descendant:
            return descendant

    for child in parent:
        if _check_includes(child, descendant):
            return child

    return None


def _fold_top_of_stack(stack: list[_Node]):
    child_node = stack.pop()
    if not stack:
        return child_node
    parent_node = stack[-1]
    parent_node.items.append((parent_node.tail_text_segments, child_node))
    parent_node.tail_text_segments = []
    return None


def _check_includes(parent: Element, child: Element) -> bool:
    for _, checked in iter_with_stack(parent):
        if child is checked:
            return True
    return False
