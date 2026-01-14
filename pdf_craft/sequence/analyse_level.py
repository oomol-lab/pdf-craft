from ..common import median, split_by_cv
from ..config import MAX_TITLE_CV
from ..pdf import TITLE_TAGS
from .chapter import Chapter, ParagraphLayout

# markdown 最大支持 6 级标题，减去作为标题的 1 级
_MAX_TITLE_GROUP = 5


def analyse_chapter_internal_levels(chapter: Chapter) -> Chapter:
    for level, layouts in enumerate(
        start=1,  # 0 作为 chapter 总标题保留，故从 1 开始
        iterable=reversed(
            split_by_cv(  # 标题从大到小排列等级，故反转
                payload_items=_collect_heights(chapter),
                max_cv=MAX_TITLE_CV,
                max_groups=_MAX_TITLE_GROUP,
            )
        ),
    ):
        for layout in layouts:
            layout.level = level

    return chapter


def _collect_heights(chapter: Chapter):
    layout_items: list[tuple[float, ParagraphLayout]] = []
    for i, layout in enumerate(chapter.layouts):
        if not isinstance(layout, ParagraphLayout) or layout.ref not in TITLE_TAGS:
            continue
        if i == 0:
            layout.level = 0
        elif layout.blocks:
            height = median(b.det[3] - b.det[1] for b in layout.blocks)
            layout_items.append((height, layout))
    return layout_items
