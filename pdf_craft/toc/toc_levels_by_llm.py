from ..llm import LLM
from .toc_levels import Ref2Level
from .toc_pages import PageRef


def analyse_toc_levels_by_llm(toc_pages: list[PageRef], llm: LLM) -> Ref2Level:
    title_items: list[tuple[str, list[tuple[int, int]]]] = []  # (text, references)
    for ref in toc_pages:
        for title in ref.matched_titles:
            references = [(r.page_index, r.order) for r in title.references]
            if references:
                title_items.append((title.text, references))

    if not title_items:
        return {}

    prompt = _build_llm_prompt(title_items)
    response = llm.request(input=prompt)
    ref2level = _parse_llm_response(response, title_items)

    return ref2level


def _build_llm_prompt(title_items: list[tuple[str, list[tuple[int, int]]]]) -> str:
    """构造 LLM 提示词，要求分析标题的层级结构"""
    prompt_lines = [
        "你是一个目录分析专家。给定下面的目录标题列表，请分析每个标题的层级（hierarchy level）。",
        "目录通常有多个层级：一级标题（章）、二级标题（节）、三级标题（小节）等。",
        "",
        "请根据标题的内容、上下文和常见的目录结构，为每个标题分配一个层级级别（0、1、2、3...）。",
        "其中 0 表示最高级别（如「第一章」），数字越大表示层级越低（如「第1.1.1小节」）。",
        "",
        "标题列表（编号从0开始）：",
    ]

    for idx, (title_text, _) in enumerate(title_items):
        prompt_lines.append(f"{idx}: {title_text}")

    prompt_lines.extend(
        [
            "",
            "请返回每个标题的层级，格式为：",
            "0: <level>",
            "1: <level>",
            "...",
            "",
            "其中 <level> 是一个整数，表示该标题的层级。只返回结果，不要包含其他说明。",
        ]
    )
    return "\n".join(prompt_lines)


def _parse_llm_response(
    response: str,
    title_items: list[tuple[str, list[tuple[int, int]]]],
) -> Ref2Level:
    ref2level: Ref2Level = {}

    title_to_level: dict[int, int] = {}
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # 尝试解析 "index: level" 格式
        parts = line.split(":")
        if len(parts) != 2:
            continue
        try:
            idx = int(parts[0].strip())
            level = int(parts[1].strip())
            title_to_level[idx] = level
        except ValueError:
            continue

    for idx, (_, references) in enumerate(title_items):
        if idx in title_to_level:
            level = title_to_level[idx]
            for page_index, order in references:
                ref2level[(page_index, order)] = level

    return ref2level
