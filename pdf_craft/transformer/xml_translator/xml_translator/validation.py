from collections.abc import Generator, Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar, cast
from xml.etree.ElementTree import Element

from tiktoken import Encoding

from pdf_craft.transformer.xml_translator.segment import (
    BlockContentError,
    BlockError,
    BlockExpectedIDsError,
    BlockUnexpectedIDError,
    BlockWrongTagError,
    FoundInvalidIDError,
    InlineError,
    InlineExpectedIDsError,
    InlineLostIDError,
    InlineUnexpectedIDError,
    InlineWrongTagCountError,
)
from ..utils import ensure_list
from pdf_craft.transformer.xml_translator.xml import plain_text

_LEVEL_WEIGHT = 3
_MAX_TEXT_HINT_TOKENS_COUNT = 6


_BLOCK_EXPECTED_IDS_LEVEL = 6
_BLOCK_WRONG_TAG_LEVEL = 5
_BLOCK_FOUND_INVALID_ID_LEVEL = 4
_BLOCK_UNEXPECTED_ID_LEVEL = 3

_INLINE_EXPECTED_IDS_LEVEL = 3
_INLINE_LOST_ID_LEVEL = 2
_INLINE_FOUND_INVALID_ID_LEVEL = 1
_INLINE_WRONG_TAG_COUNT_LEVEL = 0
_INLINE_UNEXPECTED_ID_LEVEL = 0

ERROR = TypeVar("ERROR")
LEVEL_DEPTH = 7


@dataclass
class ErrorItem(Generic[ERROR]):
    error: ERROR
    index1: int
    index2: int
    level: int
    weight: int


@dataclass
class BlockErrorsGroup:
    block_id: int
    block_element: Element
    errors: list[ErrorItem[BlockError | FoundInvalidIDError] | ErrorItem[InlineError | FoundInvalidIDError]]

    @property
    def weight(self) -> int:
        return sum(e.weight for e in self.errors)


@dataclass
class ErrorsGroup:
    upper_errors: list[ErrorItem[BlockError | FoundInvalidIDError]]
    block_groups: list[BlockErrorsGroup]

    @property
    def errors_count(self) -> int:
        count = len(self.upper_errors)
        for block_group in self.block_groups:
            count += len(block_group.errors)
        return count


def nest_as_errors_group(errors: Iterable[BlockError | FoundInvalidIDError]) -> ErrorsGroup | None:
    return _create_errors_group(
        error_items=_transform_errors_to_items(errors),
    )


def truncate_errors_group(errors_group: ErrorsGroup, max_errors: int) -> ErrorsGroup | None:
    errors_items = list(_flatten_errors_group(errors_group))
    if len(errors_items) <= max_errors:
        return errors_group

    errors_items.sort(key=lambda item: (-item[1].level, item[1].index1, item[1].index2))
    errors_items = errors_items[:max_errors]

    return _create_errors_group(
        error_items=errors_items,
    )


def generate_error_message(encoding: Encoding, errors_group: ErrorsGroup, omitted_count: int = 0) -> None | str:
    message_lines: list[str] = []
    for upper_error in errors_group.upper_errors:
        message_lines.append(_format_block_error(upper_error.error))
    if message_lines:
        message_lines.append("")

    for i, block_group in enumerate(errors_group.block_groups):
        if i == 0:
            message_lines.append("")

        block_tag = block_group.block_element.tag
        error_count = len(block_group.errors)
        count_suffix = f" ({error_count} error{'s' if error_count != 1 else ''})"
        message_lines.append(f"In {block_tag}#{block_group.block_id}:{count_suffix}")

        for block_error in block_group.errors:
            message: str
            if isinstance(block_error.error, BlockError):
                message = _format_block_error(block_error.error)
            elif isinstance(block_error.error, InlineError):
                message = _format_inline_error(encoding, block_error.error, block_group.block_id)
            else:
                raise RuntimeError()
            message_lines.append(f"  - {message}")

    if not message_lines:
        return None

    header = (
        f"Found {errors_group.errors_count} error(s). Fix them and return "
        "the COMPLETE corrected XML (not just the changed parts):"
    )
    message_lines.insert(0, "")
    message_lines.insert(0, header)

    if omitted_count > 0:
        message_lines.append("")
        message_lines.append(
            f"... and {omitted_count} more error(s) omitted. "
            f"Fix the above errors first, then resubmit for remaining issues."
        )
        message_lines.append("")
        message_lines.append("Remember: Return the entire <xml>...</xml> block with all corrections applied.")
    else:
        message_lines.append("")
        message_lines.append("Return the entire <xml>...</xml> block with corrections.")

    return "\n".join(message_lines)


@dataclass
class _Block:
    id: int
    element: Element


def _transform_errors_to_items(errors: Iterable[BlockError | FoundInvalidIDError]):
    for i, block_error in enumerate(errors):
        if isinstance(block_error, BlockContentError):
            block = _Block(
                id=block_error.id,
                element=block_error.element,
            )
            for j, inline_error in enumerate(block_error.errors):
                level = _get_inline_error_level(inline_error)
                weight = _calculate_error_weight(inline_error, level)
                yield (
                    block,
                    ErrorItem(
                        error=inline_error,
                        index1=i,
                        index2=j,
                        level=level,
                        weight=weight,
                    ),
                )
        else:
            level = _get_block_error_level(block_error)
            weight = _calculate_error_weight(block_error, level)
            error_item: ErrorItem[BlockError | FoundInvalidIDError] = ErrorItem(
                error=block_error,
                index1=i,
                index2=0,
                level=level,
                weight=weight,
            )
            block: _Block | None = None
            if isinstance(block_error, BlockWrongTagError) and block_error.block is not None:
                block = _Block(
                    id=block_error.block[0],
                    element=block_error.block[1],
                )
            yield block, error_item


def _flatten_errors_group(
    errors_group: ErrorsGroup,
) -> Generator[
    tuple[
        _Block | None,
        ErrorItem[BlockError | FoundInvalidIDError] | ErrorItem[InlineError | FoundInvalidIDError],
    ],
    None,
    None,
]:
    for error in errors_group.upper_errors:
        yield None, error

    for block_group in errors_group.block_groups:
        block = _Block(
            id=block_group.block_id,
            element=block_group.block_element,
        )
        for error in block_group.errors:
            yield block, error


def _create_errors_group(
    error_items: Iterable[
        tuple[
            _Block | None,
            ErrorItem[BlockError | FoundInvalidIDError] | ErrorItem[InlineError | FoundInvalidIDError],
        ]
    ],
) -> ErrorsGroup | None:
    upper_errors: list[ErrorItem[BlockError | FoundInvalidIDError]] = []
    block_elements: dict[int, Element] = {}
    block_errors_dict: dict[
        int, list[ErrorItem[BlockError | FoundInvalidIDError] | ErrorItem[InlineError | FoundInvalidIDError]]
    ] = {}

    for block, error in error_items:
        if block is None:
            upper_errors.append(cast(ErrorItem[BlockError | FoundInvalidIDError], error))
        else:
            block_errors = ensure_list(block_errors_dict, block.id)
            block_errors.append(error)
            block_elements[block.id] = block.element

    if not upper_errors and not block_errors_dict:
        return None

    block_errors_groups: list[BlockErrorsGroup] = []
    for block_id, block_errors in block_errors_dict.items():
        block_element = block_elements.get(block_id)
        if block_element is None:
            continue

        block_error_group = BlockErrorsGroup(
            block_id=block_id,
            block_element=block_element,
            errors=sorted(block_errors, key=lambda e: (-e.weight, e.index1, e.index2)),
        )
        block_errors_groups.append(block_error_group)

    upper_errors.sort(key=lambda e: (-e.level, e.index1, e.index2))
    block_errors_groups.sort(key=lambda g: -g.weight)

    return ErrorsGroup(
        upper_errors=upper_errors,
        block_groups=block_errors_groups,
    )


def _calculate_error_weight(error: BlockError | InlineError | FoundInvalidIDError, level: int) -> int:
    # BlockExpectedIDsError 和 InlineExpectedIDsError 的权重乘以 id2element 数量
    if isinstance(error, (BlockExpectedIDsError, InlineExpectedIDsError)):
        return (_LEVEL_WEIGHT**level) * len(error.id2element)
    else:
        return _LEVEL_WEIGHT**level


def _get_block_error_level(error: BlockError | FoundInvalidIDError) -> int:
    if isinstance(error, BlockWrongTagError):
        return _BLOCK_WRONG_TAG_LEVEL
    elif isinstance(error, BlockExpectedIDsError):
        return _BLOCK_EXPECTED_IDS_LEVEL
    elif isinstance(error, BlockUnexpectedIDError):
        return _BLOCK_UNEXPECTED_ID_LEVEL
    elif isinstance(error, FoundInvalidIDError):
        return _BLOCK_FOUND_INVALID_ID_LEVEL
    else:
        return 0


def _get_inline_error_level(error: InlineError | FoundInvalidIDError) -> int:
    if isinstance(error, InlineLostIDError):
        return _INLINE_LOST_ID_LEVEL
    elif isinstance(error, InlineExpectedIDsError):
        return _INLINE_EXPECTED_IDS_LEVEL
    elif isinstance(error, InlineUnexpectedIDError):
        return _INLINE_UNEXPECTED_ID_LEVEL
    elif isinstance(error, InlineWrongTagCountError):
        return _INLINE_WRONG_TAG_COUNT_LEVEL
    elif isinstance(error, FoundInvalidIDError):
        return _INLINE_FOUND_INVALID_ID_LEVEL
    else:
        return 0


def _format_block_error(error: BlockError | FoundInvalidIDError) -> str:
    if isinstance(error, BlockWrongTagError):
        if error.block is None:
            return (
                f"Root tag mismatch: expected `<{error.expected_tag}>`, but found `<{error.instead_tag}>`. "
                f"Fix: Change the root tag to `<{error.expected_tag}>`."
            )
        else:
            return (
                f"Wrong tag for block at `{error.instead_tag}#{error.block[0]}`: "
                f'expected `<{error.expected_tag} id="{error.block[0]}">`, '
                f'but found `<{error.instead_tag} id="{error.block[0]}">`. '
                f"Fix: Change the tag to `<{error.expected_tag}>`."
            )
    elif isinstance(error, BlockExpectedIDsError):
        # Add context hints with original text content
        context_hints: list[str] = []
        for id, elem in sorted(error.id2element.items()):
            original_text = plain_text(elem).strip()
            if original_text:
                # Truncate to first 30 chars for block-level hints
                text_preview = original_text[:30] + "..." if len(original_text) > 30 else original_text
                context_hints.append(f'  - `<{elem.tag} id="{id}">`: "{text_preview}"')

        if context_hints:
            message = "Missing block elements (find translation and wrap):\n" + "\n".join(context_hints)
        else:
            # Fallback if no text hints available
            missing_elements = [f'<{elem.tag} id="{id}">' for id, elem in sorted(error.id2element.items())]
            elements_str = ", ".join(missing_elements)
            message = f"Missing expected blocks: {elements_str}. Fix: Add these missing blocks with the correct IDs."

        return message

    elif isinstance(error, BlockUnexpectedIDError):
        selector = f"{error.element.tag}#{error.id}"
        return f"Unexpected block found at `{selector}`. Fix: Remove this unexpected block."

    elif isinstance(error, FoundInvalidIDError):
        if error.invalid_id is None:
            example = f"<{error.element.tag}>"
        else:
            example = f'<{error.element.tag} id="{error.invalid_id}">'
        return f"Invalid or missing ID attribute: {example}. Fix: Ensure all blocks have valid numeric IDs."
    else:
        return "Unknown block error. Fix: Review the block structure."


def _format_inline_error(encoding: Encoding, error: InlineError | FoundInvalidIDError, block_id: int) -> str:
    if isinstance(error, InlineLostIDError):
        selector = _build_inline_selector(encoding, error.stack, block_id, element=error.element)
        return f"Element at `{selector}` is missing an ID attribute. Fix: Add the required ID attribute."

    elif isinstance(error, InlineExpectedIDsError):
        # Add context hints with original text content
        context_hints: list[str] = []
        for id, elem in sorted(error.id2element.items()):
            original_text = plain_text(elem).strip()
            if original_text:
                text_hint = _extract_text_hint(encoding, elem)
                context_hints.append(f'  - `<{elem.tag} id="{id}">`: "{text_hint}"')

        if context_hints:
            message = "Missing inline elements (find translation and wrap):\n" + "\n".join(context_hints)
        else:
            # Fallback if no text hints available
            missing_elements = [f'<{elem.tag} id="{id}">' for id, elem in sorted(error.id2element.items())]
            elements_str = ", ".join(missing_elements)
            message = f"Missing expected inline elements: {elements_str}. Fix: Add these missing inline elements."

        return message

    elif isinstance(error, InlineUnexpectedIDError):
        selector = f"{error.element.tag}#{error.id}"
        return f"Unexpected inline element at `{selector}`. Fix: Remove this unexpected element."

    elif isinstance(error, InlineWrongTagCountError):
        tag = error.found_elements[0].tag if error.found_elements else "unknown"
        selector = _build_inline_selector(encoding, error.stack, block_id, tag=tag)
        expected = error.expected_count
        found = len(error.found_elements)

        if expected == 0 and found > 0:
            # 情况1: 不应该有，但发现了
            return (
                f"Found unexpected `<{tag}>` elements at `{selector}`. "
                f"There should be none, but {found} were found. "
                f"Fix: Remove all `<{tag}>` elements from this location."
            )
        elif expected > 0 and found == 0:
            # 情况2: 应该有，但没找到
            return (
                f"Missing `<{tag}>` elements at `{selector}`. "
                f"Expected {expected}, but none were found. "
                f"Fix: Add {expected} `<{tag}>` element(s) to this location."
            )
        elif found > expected:
            # 情况3: 数量过多
            extra = found - expected
            return (
                f"Too many `<{tag}>` elements at `{selector}`. "
                f"Expected {expected}, but found {found} ({extra} extra). "
                f"Fix: Remove {extra} `<{tag}>` element(s)."
            )
        else:
            # 情况4: 数量过少
            missing = expected - found
            return (
                f"Too few `<{tag}>` elements at `{selector}`. "
                f"Expected {expected}, but only found {found} ({missing} missing). "
                f"Fix: Add {missing} more `<{tag}>` element(s)."
            )
    elif isinstance(error, FoundInvalidIDError):
        if error.invalid_id is None:
            example = f"<{error.element.tag}>"
        else:
            example = f'<{error.element.tag} id="{error.invalid_id}">'
        return f"Invalid inline ID: {example}. Fix: Ensure inline elements have valid numeric IDs."
    else:
        return "Unknown inline error. Fix: Review the inline structure."


def _build_inline_selector(
    encoding: Encoding,
    stack: list[Element],
    block_id: int,
    element: Element | None = None,
    tag: str | None = None,
) -> str:
    if element is not None:
        element_id = element.get("id")
        if element_id is not None:
            # 能用 ID 直接定位，就不必用路径定位
            return f"{element.tag}#{element_id}"
        tag = element.tag

    # 路径：block#id > parent > ... > tag
    block_tag = stack[0].tag if stack else "unknown"
    path_parts = [f"{block_tag}#{block_id}"]

    for parent in stack[1:]:
        path_parts.append(parent.tag)

    if tag:
        path_parts.append(tag)

    selector = " > ".join(path_parts)

    if element is not None:
        text_hint = _extract_text_hint(encoding, element)
        if text_hint:
            selector += f' (contains text: "{text_hint}")'
    return selector


def _extract_text_hint(encoding: Encoding, element: Element) -> str:
    text = plain_text(element).strip()
    if text:
        tokens = encoding.encode(text)
        if len(tokens) > _MAX_TEXT_HINT_TOKENS_COUNT:
            tokens = tokens[:_MAX_TEXT_HINT_TOKENS_COUNT]
            text = encoding.decode(tokens).strip() + " ..."
    return text
