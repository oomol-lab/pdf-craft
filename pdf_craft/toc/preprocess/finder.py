import ahocorasick

from typing import Iterable, Callable, TypeVar, Generic

from ...language import is_latin_letter
from ..common import PageRef, MatchedTitle, TitleReference
from .text import normalize_text


_MAX_TOC_RATIO = 0.1
_MIN_TOC_LIMIT = 3
_MIN_LATIN_TITLE_LENGTH = 6
_MIN_NON_LATIN_TITLE_LENGTH = 3


# 使用统计学方式寻找文档中目录页所在页数范围。
# 目录页中的文本，会大规模与后续书页中的章节标题匹配，本函数使用此特征来锁定目录页。
def find_toc_pages(
        iter_titles: Callable[[], Iterable[list[str]]],
        iter_page_bodies: Callable[[], Iterable[str]],
    ) -> list[PageRef]:

    matcher: _SubstringMatcher[tuple[int, int]] = _SubstringMatcher() # (page_index, order)
    page_refs: list[PageRef] = []

    for page_index, titles in enumerate(iter_titles(), start=1):
        for order, title in enumerate(titles):
            title = normalize_text(title)
            if _valid_title(title):
                matcher.register_substring(
                    substring=title,
                    payload=(page_index, order),
                )

    if matcher.substrings_count == 0:
        return []

    for page_index, body in enumerate(iter_page_bodies(), start=1):
        matched_titles: list[MatchedTitle] = []
        matched_substrings = matcher.match(normalize_text(body))

        # 每一个匹配的子串提供的分数为：该页匹配次数 / 该子串在文档中出现的总次数
        # 若匹配越多，当然说明此页更有可能是目录页。
        # 但若该子串在文档中大规模出现，例如书籍标题可能反复出现在页眉页脚，此时应该降低权重
        for substring, (matched_count, payloads) in matched_substrings.items():
            references: list[TitleReference] = [
                TitleReference(page_index=index, order=order)
                for index, order in payloads
                if index != page_index
            ]
            if references:
                matched_title = MatchedTitle(
                    text=substring,
                    score=matched_count / len(references),
                    references=references,
                )
                matched_titles.append(matched_title)

        page_refs.append(PageRef(
            page_index=page_index,
            matched_titles=matched_titles,
            score=sum(m.score for m in matched_titles),
        ))

    total_pages = len(page_refs)
    max_toc_pages = max(_MIN_TOC_LIMIT, int(total_pages * _MAX_TOC_RATIO))

    if total_pages <= 1:
        return [] # 仅一页没有抽离目录的必要

    page_refs.sort(key=lambda x: x.score, reverse=True)
    max_diff = 0.0
    cut_position = 0

    for i in range(len(page_refs) - 1):
        diff = page_refs[i].score - page_refs[i + 1].score
        if diff > max_diff:
            max_diff = diff
            cut_position = i + 1

    cut_position = min(cut_position, max_toc_pages)
    toc_page_refs = page_refs[:cut_position]
    toc_page_refs.sort(key=lambda x: x.page_index)

    return toc_page_refs

def _valid_title(title: str) -> bool:
    title = title.strip()
    if any(is_latin_letter(c) for c in title):
        return len(title) >= _MIN_LATIN_TITLE_LENGTH
    else:
        return len(title) >= _MIN_NON_LATIN_TITLE_LENGTH

_P = TypeVar("_P")

class _SubstringMatcher(Generic[_P]):
    def __init__(self):
        self._automaton = ahocorasick.Automaton()
        self._substrings_count: int = 0
        self._substring_to_payloads: dict[str, list[_P]] = {}
        self._finalized = False

    @property
    def substrings_count(self) -> int:
        return self._substrings_count

    def register_substring(self, substring: str, payload: _P) -> None:
        self._substrings_count += 1

        if substring not in self._substring_to_payloads:
            self._substring_to_payloads[substring] = []
            self._automaton.add_word(substring, substring)

        self._substring_to_payloads[substring].append(payload)
        self._finalized = False

    def match(self, text: str) -> dict[str, tuple[int, list[_P]]]:
        if not self._finalized:
            self._automaton.make_automaton()
            self._finalized = True

        match_counts: dict[str, int] = {}
        for _, substring in self._automaton.iter(text):
            match_counts[substring] = match_counts.get(substring, 0) + 1

        match_result: dict[str, tuple[int, list[_P]]] = {}
        for substring, count in match_counts.items():
            match_result[substring] = (count, self._substring_to_payloads[substring])

        return match_result
