import ahocorasick

from typing import Iterable, Callable

from ...language import is_latin_letter
from .text import normalize_text


_MAX_TOC_RATIO = 0.1
_MIN_TOC_LIMIT = 3
_MIN_LATIN_TITLE_LENGTH = 6
_MIN_NON_LATIN_TITLE_LENGTH = 3


# 使用统计学方式寻找文档中目录页所在页数范围。
# 目录页中的文本，会大规模与后续书页中的章节标题匹配，本函数使用此特征来锁定目录页。
def find_toc_page_indexes(
        iter_titles: Callable[[], Iterable[list[str]]],
        iter_page_bodies: Callable[[], Iterable[str]],
    ) -> list[int]:

    matcher = _SubstringMatcher()
    page_scores: list[tuple[int, float]] = []  # (page_index, score)
    id2page: dict[int, int] = {}

    for page_index, titles in enumerate(iter_titles(), start=1):
        for title in titles:
            title = normalize_text(title)
            if _valid_title(title):
                id = matcher.register_substring(title)
                id2page[id] = page_index

    if matcher.substrings_count == 0:
        return []

    for page_index, body in enumerate(iter_page_bodies(), start=1):
        match_result = matcher.match(normalize_text(body))
        # 每一个匹配的子串提供的分数为：该页匹配次数 / 该子串在文档中出现的总次数
        # 若匹配越多，当然说明此页更有可能是目录页。
        # 但若该子串在文档中大规模出现，例如书籍标题可能反复出现在页眉页脚，此时应该降低权重
        score = 0.0
        for _, (matched_count, ids) in match_result.items():
            count_in_document = 0
            for id in ids:
                if page_index != id2page[id]:
                    count_in_document += 1
            if count_in_document > 0:
                score += matched_count / count_in_document
        page_scores.append((page_index, score))

    total_pages = len(page_scores)
    max_toc_pages = max(_MIN_TOC_LIMIT, int(total_pages * _MAX_TOC_RATIO))

    if total_pages <= 1:
        return [] # 仅一页没有抽离目录的必要

    page_scores.sort(key=lambda x: x[1], reverse=True)
    max_diff = 0.0
    cut_position = 0

    for i in range(len(page_scores) - 1):
        diff = page_scores[i][1] - page_scores[i + 1][1]
        if diff > max_diff:
            max_diff = diff
            cut_position = i + 1

    cut_position = min(cut_position, max_toc_pages)
    toc_pages = page_scores[:cut_position]

    return sorted([i for i, _ in toc_pages])

def _valid_title(title: str) -> bool:
    title = title.strip()
    if any(is_latin_letter(c) for c in title):
        return len(title) >= _MIN_LATIN_TITLE_LENGTH
    else:
        return len(title) >= _MIN_NON_LATIN_TITLE_LENGTH

class _SubstringMatcher:
    def __init__(self):
        self._automaton = ahocorasick.Automaton()
        self._next_id = 0
        self._substrings_count: int = 0
        self._substring_to_ids: dict[str, list[int]] = {}
        self._finalized = False

    @property
    def substrings_count(self) -> int:
        return self._substrings_count

    def register_substring(self, substring: str) -> int:
        current_id = self._next_id
        self._next_id += 1
        self._substrings_count += 1

        if substring not in self._substring_to_ids:
            self._substring_to_ids[substring] = []
            self._automaton.add_word(substring, substring)

        self._substring_to_ids[substring].append(current_id)
        self._finalized = False

        return current_id

    def match(self, text: str) -> dict[str, tuple[int, list[int]]]:
        if not self._finalized:
            self._automaton.make_automaton()
            self._finalized = True

        match_counts: dict[str, int] = {}
        for _, substring in self._automaton.iter(text):
            match_counts[substring] = match_counts.get(substring, 0) + 1

        match_result: dict[str, tuple[int, list[int]]] = {}
        for substring, count in match_counts.items():
            match_result[substring] = (count, self._substring_to_ids[substring])

        return match_result
