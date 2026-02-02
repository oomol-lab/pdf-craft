import json
import logging

from json_repair import repair_json
from pydantic import BaseModel, ValidationError, field_validator

from ..llm import LLM, Message, MessageRole
from ..pdf import Page
from .toc_levels import Ref2Level
from .toc_pages import PageRef
from .text import normalize_text

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3


class TocLevelsSchema(BaseModel):
    """Pydantic schema for TOC levels validation"""

    levels: list[int]

    @field_validator("levels")
    @classmethod
    def validate_levels(cls, v: list[int]) -> list[int]:
        # Rule 1: Check all are valid integers in range
        for i, level in enumerate(v):
            if not isinstance(level, int):
                raise ValueError(
                    f"Level at index {i} is not an integer: {level}. "
                    f"All values must be integers."
                )
            if level < 0:
                raise ValueError(
                    f"Level at index {i} is negative: {level}. "
                    f"All levels must be non-negative (0, 1, 2, ...)."
                )
            if level > 5:
                raise ValueError(
                    f"Level at index {i} is too deep: {level}. "
                    f"Maximum level is 5. Most TOCs have 3-4 levels."
                )

        # Rule 2: Check for reasonable transitions (no jump > 2)
        for i in range(1, len(v)):
            jump = v[i] - v[i - 1]
            if jump > 2:
                raise ValueError(
                    f"Level jump too large at index {i}: "
                    f"from {v[i - 1]} to {v[i]} (jump of {jump}). "
                    f"Level changes should not exceed 2 at once."
                )

        # Rule 3: First level should typically be 0
        if v and v[0] > 1:
            raise ValueError(
                f"First level is {v[0]}, but TOCs typically start at level 0. "
                f"Consider starting with 0 for the first chapter."
            )

        return v


class LLMAnalysisError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


# Type alias for TOC entries with layout info
# Format: (text, references_or_none, indent, font_size, is_matched)
# - references_or_none: None if not matched to content, otherwise list of (page_index, order)
# - is_matched: True if this entry needs to be mapped to Ref2Level
TocEntry = tuple[str, list[tuple[int, int]] | None, float, float, bool]


def _extract_all_toc_entries(
    toc_page_refs: list[PageRef],
    toc_page_contents: list[Page],
) -> list[TocEntry]:
    """
    Extract ALL entries from TOC pages with layout information.

    This includes both matched titles (that appear in content) and unmatched titles.
    The LLM needs to see the complete TOC structure to understand hierarchy.

    Returns:
        List of (text, references_or_none, indent, font_size, is_matched) tuples
    """
    # Build a mapping from page_index to PageRef
    page_index_to_ref: dict[int, PageRef] = {
        ref.page_index: ref for ref in toc_page_refs
    }

    all_entries: list[TocEntry] = []

    for page in toc_page_contents:
        page_ref = page_index_to_ref.get(page.index)

        # Build a mapping from normalized text to matched titles (with references)
        text_to_matched: dict[str, list[tuple[int, int]]] = {}
        if page_ref:
            for matched_title in page_ref.matched_titles:
                references = [(r.page_index, r.order) for r in matched_title.references]
                if references:
                    normalized = normalize_text(matched_title.text)
                    text_to_matched[normalized] = references

        # Extract ALL body layouts from this TOC page
        for layout in page.body_layouts:
            text = layout.text.strip()
            if not text:
                continue

            # Extract layout information
            left, top, _, bottom = layout.det
            indent = left
            font_size = bottom - top

            # Check if this layout matches any matched_title
            normalized = normalize_text(text)
            references = text_to_matched.get(normalized)
            is_matched = references is not None

            all_entries.append((
                text,
                references,
                indent,
                font_size,
                is_matched,
            ))

    return all_entries


def analyse_toc_levels_by_llm(
        llm: LLM,
        toc_page_refs: list[PageRef],
        toc_page_contents: list[Page],
    ) -> Ref2Level:
    """
    Analyze TOC hierarchy levels using LLM with validation and retry mechanism.

    This function extracts ALL entries from TOC pages (not just matched titles)
    to provide complete context for the LLM to understand hierarchy structure.

    Raises:
        LLMAnalysisError: If analysis fails after all retries
    """
    # Extract ALL TOC entries (including unmatched ones for context)
    all_entries = _extract_all_toc_entries(toc_page_refs, toc_page_contents)

    if not all_entries:
        return {}

    # Build initial conversation with the task prompt
    initial_prompt = _build_llm_prompt(all_entries)
    messages: list[Message] = [Message(role=MessageRole.USER, message=initial_prompt)]
    last_error = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = llm.request(input=messages)

            # Validate and parse response
            levels, error_msg = _validate_and_parse(response, all_entries)

            if levels is not None:
                # Success! Map to Ref2Level (only for matched entries)
                print(f"LLM analysis succeeded on attempt {attempt + 1}")
                return _map_to_ref2level(levels, all_entries)

            # Validation failed
            last_error = error_msg
            print(
                f"LLM response validation failed (attempt {attempt + 1}/{_MAX_RETRIES}): {error_msg}"
            )

            # Build conversation history for next attempt
            # Only keep: initial prompt + last assistant response + last error feedback
            if attempt < _MAX_RETRIES - 1:
                error_feedback = _build_error_feedback(error_msg or "Unknown error")
                messages = [
                    messages[0],  # Keep initial user prompt
                    Message(role=MessageRole.ASSISTANT, message=response),
                    Message(role=MessageRole.USER, message=error_feedback),
                ]

        except Exception as e:
            last_error = str(e)
            print(
                f"Unexpected error during LLM analysis (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
            )
            if attempt == _MAX_RETRIES - 1:
                break

    # All attempts failed
    error_detail = f"Last error: {last_error}" if last_error else "Unknown error"
    raise LLMAnalysisError(
        f"LLM analysis failed after {_MAX_RETRIES} attempts. {error_detail}"
    )


def _build_llm_prompt(all_entries: list[TocEntry]) -> str:
    """Build LLM prompt with complete TOC content and layout information"""
    prompt_lines = [
        "You are analyzing a complete table of contents (TOC) from a book.",
        "Below is the COMPLETE TOC content extracted from the TOC pages, in order.",
        "",
        "Each entry includes:",
        "- Text: The title/content text",
        "- Indent: Horizontal position (larger = more indented)",
        "- Size: Font size (larger font usually = higher level)",
        "",
        "Your task: Assign a hierarchy level (0, 1, 2, 3, ...) to EACH entry.",
        "",
        "Hierarchy levels:",
        '- Level 0: Top-level volumes/books (e.g., "第一卷", "第二卷", "Volume I")',
        '- Level 1: Major parts/sections (e.g., "第 I 部分", "第 II 部分", "Part A")',
        '- Level 2: Subsections (e.g., "1.1 Introduction", "1.2 Methods")',
        '- Level 3+: Deeper subsections',
        "",
        "Analysis strategy:",
        "1. Look at the COMPLETE structure - understand the overall organization",
        "2. Identify top-level divisions (volumes, books) → Level 0",
        "3. Identify major sections within each division → Level 1",
        "4. Identify subsections → Level 2+",
        "",
        "Key clues:",
        '- Text patterns: "第一卷/第二卷" (volumes) > "第 I/II/III 部分" (parts) > subsections',
        '- Numbering: Roman numerals (I, II, III) often indicate parts/sections',
        '- Font size: Larger = higher level (but not always reliable)',
        '- Indent: Larger indent = deeper level (but not always reliable)',
        "",
        "IMPORTANT:",
        "- Analyze the ENTIRE list as a whole, not entry by entry",
        "- Look for patterns and groupings",
        "- Some entries may be page numbers, headers, or other metadata - assign appropriate levels",
        "",
        "Complete TOC entries (indexed from 0):",
    ]

    for idx, (text, _, indent, font_size, _) in enumerate(all_entries):
        prompt_lines.append(f"{idx}: [Indent:{indent:.1f}, Size:{font_size:.1f}] {text}")

    prompt_lines.extend(
        [
            "",
            f"Return ONLY a JSON array of {len(all_entries)} integers representing the level for each entry.",
            "Example format: [0, 1, 2, 1, 0, 1, 1, 2]",
            "",
            "Your response (JSON array only, no explanation):",
        ]
    )
    return "\n".join(prompt_lines)


def _build_error_feedback(error_message: str) -> str:
    """Build error feedback for LLM to correct its response"""
    prompt_lines = [
        "Your previous response had an error:",
        error_message,
        "",
        "Please correct the response. Remember:",
        "- Return ONLY a JSON array of integers",
        "- All values must be between 0 and 5",
        "- Level changes should not jump more than 2 at once",
        "- TOCs typically start at level 0",
        "",
        "Corrected response (JSON array only):",
    ]
    return "\n".join(prompt_lines)


def _validate_and_parse(
    response: str,
    all_entries: list[TocEntry],
) -> tuple[list[int] | None, str | None]:
    """
    Validate and parse LLM response.

    Returns:
        (levels, error_message)
        - If successful: (levels, None)
        - If failed: (None, error_message_for_llm)
    """
    try:
        # Step 1: Repair JSON format issues
        repaired = repair_json(response.strip())

        # Step 2: Parse JSON
        data = json.loads(repaired)

        # Step 3: Type check
        if not isinstance(data, list):
            return None, (
                "Response must be a JSON array of integers, e.g., [0, 1, 2]. "
                f"You returned: {type(data).__name__}"
            )

        # Step 4: Validate with Pydantic (types, ranges, transitions)
        schema = TocLevelsSchema(levels=data)

        # Step 5: Context-aware validation (length match)
        expected_len = len(all_entries)
        actual_len = len(schema.levels)
        if actual_len != expected_len:
            return None, (
                f"Array length mismatch: you provided {actual_len} levels, "
                f"but there are {expected_len} TOC entries. "
                f"You must provide exactly one level for each entry (0 to {expected_len - 1})."
            )

        # Success!
        return schema.levels, None

    except json.JSONDecodeError as e:
        return None, (
            f"Invalid JSON syntax: {str(e)}. "
            f"Please return a valid JSON array like [0, 1, 2, 1, 0]."
        )

    except ValidationError as e:
        # Extract first error message (already LLM-friendly from our validators)
        errors = e.errors()
        if errors and "msg" in errors[0]:
            return None, errors[0]["msg"]
        return None, str(e)

    except Exception as e:
        return None, f"Unexpected error: {str(e)}"


def _map_to_ref2level(
    levels: list[int],
    all_entries: list[TocEntry],
) -> Ref2Level:
    """
    Map validated levels to Ref2Level dictionary.

    Only entries with references (is_matched=True) are included in the result.
    """
    ref2level: Ref2Level = {}

    for idx, (_, references, _, _, is_matched) in enumerate(all_entries):
        if not is_matched or references is None:
            # Skip entries that don't need to be mapped to content
            continue

        level = levels[idx]
        for page_index, order in references:
            ref2level[(page_index, order)] = level

    return ref2level
