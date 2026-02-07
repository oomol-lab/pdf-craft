from dataclasses import dataclass
from typing import Callable, Generic, Iterable, TypeVar

import ahocorasick

from ..language import is_latin_letter
from .text import normalize_text

_MAX_TOC_RATIO = 0.1
_TOC_HEAD_RATIO = 0.18
_TOC_SCORE_MIN_RATIO = 3.0  # 经验估计，以后再调整吧
_MIN_TOC_LIMIT = 3
_MIN_LATIN_TITLE_LENGTH = 6
_MIN_NON_LATIN_TITLE_LENGTH = 3


@dataclass
class PageRef:
    page_index: int
    score: float
    matched_titles: list["MatchedTitle"]


@dataclass
class MatchedTitle:
    text: str
    score: float
    references: list["TitleReference"]


@dataclass
class TitleReference:
    page_index: int
    order: int


# 使用统计学方式寻找文档中目录页所在页数范围。
# 目录页中的文本，会大规模与后续书页中的章节标题匹配，本函数使用此特征来锁定目录页。
def find_toc_pages(
    iter_titles: Callable[[], Iterable[list[tuple[int, str]]]],
    iter_page_bodies: Callable[[], Iterable[str]],
) -> list[PageRef]:
    matcher: _SubstringMatcher[tuple[int, int]] = (
        _SubstringMatcher()
    )  # (page_index, order)
    page_refs: list[PageRef] = []

    for page_index, titles_items in enumerate(iter_titles(), start=1):
        for order, title in titles_items:
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

        page_refs.append(
            PageRef(
                page_index=page_index,
                matched_titles=matched_titles,
                score=sum(m.score for m in matched_titles),
            )
        )

    page_refs.sort(key=lambda x: x.score, reverse=True)
    max_diff = 0.0
    cut_position = 0

    for i in range(len(page_refs) - 1):
        diff = page_refs[i].score - page_refs[i + 1].score
        if diff > max_diff:
            max_diff = diff
            cut_position = i + 1

    toc_page_refs = page_refs[:cut_position]
    toc_page_refs.sort(key=lambda x: x.page_index)

    max_content_score = 0.0
    if cut_position < len(page_refs):
        max_content_score = page_refs[cut_position].score

    # DEBUG: 显示内容
    # for i, ref in enumerate(page_refs):
    #     if i == cut_position:
    #         print("\n----- TOC CANDIDATE CUT -----\n")
    #     print(f"[TOC PAGE] page_index={ref.page_index}, score={ref.score:.4f}")
    #     for title in ref.matched_titles:
    #         print(f"  [TITLE] score={title.score:.4f}, text={title.text}")
    #         for reference in title.references:
    #             print(f"    [REF] page_index={reference.page_index}, order={reference.order}")

    return _human_like_toc_filter(
        toc_page_refs=toc_page_refs,
        total_pages=len(page_refs),
        max_content_score=max_content_score,
    )


def _valid_title(title: str) -> bool:
    title = title.strip()
    if any(is_latin_letter(c) for c in title):
        return len(title) >= _MIN_LATIN_TITLE_LENGTH
    else:
        return len(title) >= _MIN_NON_LATIN_TITLE_LENGTH


def _human_like_toc_filter(
    toc_page_refs: list[PageRef],
    total_pages: int,
    max_content_score: float,
) -> list[PageRef]:
    max_toc_pages = max(_MIN_TOC_LIMIT, int(total_pages * _MAX_TOC_RATIO))
    max_toc_page_index = round(total_pages * _TOC_HEAD_RATIO)
    toc_page_refs = [
        ref for ref in toc_page_refs if ref.page_index <= max_toc_page_index
    ]
    if len(toc_page_refs) > max_toc_pages:
        toc_page_refs = toc_page_refs[:max_toc_pages]

    if not toc_page_refs:
        return toc_page_refs

    serial_refs: list[PageRef] = [toc_page_refs[0]]
    last_page_index = serial_refs[0].page_index

    for i in range(1, len(toc_page_refs)):
        ref = toc_page_refs[i]
        if ref.page_index == last_page_index + 1:
            serial_refs.append(ref)
            last_page_index = ref.page_index
        else:
            break

    if not serial_refs:
        return serial_refs

    serial_page_indexes = {ref.page_index for ref in serial_refs}
    for ref in toc_page_refs:
        if ref.page_index not in serial_page_indexes:
            max_content_score = max(max_content_score, ref.score)

    max_toc_score = serial_refs[0].score
    if max_toc_score < _TOC_SCORE_MIN_RATIO * max_content_score:
        return []  # 说明目录页不足以与非目录页拉开差距，不可贸然判断

    return serial_refs


_P = TypeVar("_P")


class _SubstringMatcher(Generic[_P]):
    def __init__(self):
        # ahocorasick is a C extension module, static analyzers cannot introspect it at analysis time
        self._automaton = ahocorasick.Automaton()  # type: ignore[attr-defined]  # pylint: disable=c-extension-no-member
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
